from __future__ import annotations

import datetime
from typing import Any

import packaging.version

from procrastinate import psycopg_connector

from . import credentials

MIN_PSYCOPG_POOL_VERSION = packaging.version.Version("3.3.0")
RESERVED_ARGUMENTS = ("conninfo", "kwargs")


class LakebaseConnector(psycopg_connector.PsycopgConnector):
    def __init__(
        self,
        *,
        endpoint: str,
        database: str = credentials.DEFAULT_DATABASE,
        user: str | None = None,
        host: str | None = None,
        workspace_client: Any | None = None,
        refresh_margin: datetime.timedelta = credentials.DEFAULT_REFRESH_MARGIN,
        **kwargs: Any,
    ) -> None:
        """
        Create a connector for a Databricks Lakebase endpoint. It behaves like
        `PsycopgConnector`, but every new connection authenticates with an
        OAuth database credential that is generated through the Databricks SDK
        and renewed before it expires.

        Requires the ``lakebase`` extra and ``psycopg_pool>=3.3.0``.

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
            A ``databricks.sdk.WorkspaceClient``. Defaults to
            ``WorkspaceClient()``.
        refresh_margin :
            How long before expiry a cached credential stops being reused.
        kwargs :
            Passed to `PsycopgConnector` (``json_dumps``, ``json_loads``,
            ``pool_factory`` and pool arguments such as ``max_size``).
            ``conninfo`` and ``kwargs`` are not accepted: connection parameters
            come from the credential provider.
        """
        reserved = sorted(set(kwargs).intersection(RESERVED_ARGUMENTS))
        if reserved:
            raise TypeError(
                f"LakebaseConnector does not accept {', '.join(reserved)}: "
                "connection parameters are derived from endpoint, database, "
                "user and host"
            )
        _check_psycopg_pool_version(psycopg_connector.psycopg_pool_version)

        self.credential_provider = credentials.LakebaseCredentialProvider(
            endpoint=endpoint,
            database=database,
            user=user,
            host=host,
            workspace_client=workspace_client,
            refresh_margin=refresh_margin,
        )
        super().__init__(kwargs=self.credential_provider, **kwargs)


def _check_psycopg_pool_version(version: packaging.version.Version | None) -> None:
    # Like PsycopgConnector, assume a recent psycopg_pool when undetectable.
    if version is not None and version < MIN_PSYCOPG_POOL_VERSION:
        raise RuntimeError(
            f"LakebaseConnector requires psycopg_pool>={MIN_PSYCOPG_POOL_VERSION} "
            f"(found {version})"
        )
