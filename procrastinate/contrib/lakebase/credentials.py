from __future__ import annotations

import dataclasses
import datetime
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from procrastinate import exceptions, utils

if TYPE_CHECKING:
    import databricks.sdk as databricks_sdk
else:
    (databricks_sdk,) = utils.import_or_wrapper("databricks.sdk")

logger = logging.getLogger(__name__)

DEFAULT_DATABASE = "databricks_postgres"
DEFAULT_REFRESH_MARGIN = datetime.timedelta(minutes=5)
SSL_MODE = "require"
ENDPOINT_FORMAT = "projects/<project>/branches/<branch>/endpoints/<endpoint>"
ENDPOINT_PATTERN = re.compile(r"projects/[^/]+/branches/[^/]+/endpoints/[^/]+")


@dataclasses.dataclass(frozen=True)
class _Credential:
    token: str
    # Epoch seconds. None means the server gave no expiry: never reuse it.
    expires_at: float | None


class LakebaseCredentialProvider:
    """
    Callable returning psycopg connection keyword arguments for a Databricks
    Lakebase endpoint, with a short-lived OAuth token as the password.

    The token is cached and renewed ``refresh_margin`` before it expires. The
    provider is thread-safe and synchronous, so the same instance can be given
    to both ``psycopg_pool.ConnectionPool`` and ``AsyncConnectionPool`` as
    their ``kwargs`` argument.

    Parameters
    ----------
    endpoint :
        Endpoint resource path:
        ``projects/<project>/branches/<branch>/endpoints/<endpoint>``.
    database :
        Postgres database name.
    user :
        Postgres role. Defaults to the identity of the workspace client.
    host :
        Endpoint hostname. Defaults to a lookup through the workspace client.
    workspace_client :
        A ``databricks.sdk.WorkspaceClient``. Defaults to ``WorkspaceClient()``,
        which uses the SDK's default authentication chain.
    refresh_margin :
        How long before expiry a cached token stops being reused.
    clock :
        Function returning the current time as epoch seconds.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        database: str = DEFAULT_DATABASE,
        user: str | None = None,
        host: str | None = None,
        workspace_client: Any | None = None,
        refresh_margin: datetime.timedelta = DEFAULT_REFRESH_MARGIN,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not ENDPOINT_PATTERN.fullmatch(endpoint):
            raise ValueError(
                f"Invalid Lakebase endpoint {endpoint!r}: expected {ENDPOINT_FORMAT}"
            )
        if workspace_client is None:
            # Fail here rather than at the first connection if the SDK is missing.
            _ = databricks_sdk.WorkspaceClient
        self._endpoint = endpoint
        self._database = database
        self._user = user
        self._host = host
        self._workspace_client = workspace_client
        self._refresh_margin = refresh_margin.total_seconds()
        self._clock = clock
        self._credential: _Credential | None = None
        self._lock = threading.Lock()

    def __call__(self) -> dict[str, Any]:
        with self._lock:
            host = self._resolve_host()
            user = self._resolve_user()
            token = self._current_token()
        return {
            "host": host,
            "dbname": self._database,
            "user": user,
            "password": token,
            "sslmode": SSL_MODE,
        }

    def _workspace(self) -> Any:
        if self._workspace_client is None:
            self._workspace_client = databricks_sdk.WorkspaceClient()
        return self._workspace_client

    def _resolve_host(self) -> str:
        if self._host is None:
            endpoint = self._call_sdk(
                "look up the host",
                lambda: self._workspace().postgres.get_endpoint(name=self._endpoint),
            )
            hosts = getattr(getattr(endpoint, "status", None), "hosts", None)
            host = getattr(hosts, "host", None)
            if not host:
                raise exceptions.ConnectorException(
                    f"Lakebase endpoint {self._endpoint} has no host yet"
                )
            self._host = host
        return self._host

    def _resolve_user(self) -> str:
        if self._user is None:
            self._user = self._call_sdk(
                "look up the current user",
                lambda: self._workspace().current_user.me().user_name,
            )
        return self._user

    def _current_token(self) -> str:
        cached = self._credential
        if cached is not None and self._is_reusable(cached):
            return cached.token

        self._credential = None
        credential = self._mint()
        self._credential = credential
        return credential.token

    def _is_reusable(self, credential: _Credential) -> bool:
        if credential.expires_at is None:
            return False
        return self._clock() < credential.expires_at - self._refresh_margin

    def _call_sdk(self, action: str, call: Callable[[], Any]) -> Any:
        # psycopg_pool only reports a failed connection attempt as a warning,
        # so log the reason here. The token is never part of the message.
        try:
            return call()
        except Exception:
            logger.exception(
                "Could not %s for Lakebase endpoint %s",
                action,
                self._endpoint,
                extra={"action": "lakebase_sdk_error"},
            )
            raise

    def _mint(self) -> _Credential:
        response = self._call_sdk(
            "generate a database credential",
            lambda: self._workspace().postgres.generate_database_credential(
                endpoint=self._endpoint
            ),
        )
        if not response.token:
            raise exceptions.ConnectorException(
                f"Lakebase returned an empty database credential for {self._endpoint}"
            )
        expire_time = response.expire_time
        return _Credential(
            token=response.token,
            expires_at=None if expire_time is None else float(expire_time.ToSeconds()),
        )
