from __future__ import annotations

import dataclasses
from typing import Any

import pytest

ENDPOINT = "projects/my-project/branches/production/endpoints/primary"
HOST = "ep-test.database.cloud.databricks.com"
USER = "someone@example.com"


@dataclasses.dataclass(frozen=True)
class FakeTimestamp:
    seconds: int

    def ToSeconds(self) -> int:  # mirrors protobuf Timestamp
        return self.seconds


@dataclasses.dataclass(frozen=True)
class FakeCredential:
    token: str
    expire_time: FakeTimestamp | None


class FakePostgres:
    def __init__(self) -> None:
        self.endpoint_calls: list[str] = []
        self.credential_calls: list[str] = []
        self.host: str | None = HOST
        self.token_lifetime: int | None = 3600
        self.now: float = 0.0
        self.error: Exception | None = None

    def get_endpoint(self, name: str) -> Any:
        self.endpoint_calls.append(name)
        hosts = None if self.host is None else _Namespace(host=self.host)
        return _Namespace(status=_Namespace(hosts=hosts))

    def generate_database_credential(self, endpoint: str) -> FakeCredential:
        self.credential_calls.append(endpoint)
        if self.error is not None:
            raise self.error
        expire_time = (
            None
            if self.token_lifetime is None
            else FakeTimestamp(seconds=int(self.now) + self.token_lifetime)
        )
        return FakeCredential(
            token=f"token-{len(self.credential_calls)}", expire_time=expire_time
        )


class FakeCurrentUser:
    def __init__(self) -> None:
        self.calls = 0

    def me(self) -> Any:
        self.calls += 1
        return _Namespace(user_name=USER)


class FakeWorkspace:
    def __init__(self) -> None:
        self.postgres = FakePostgres()
        self.current_user = FakeCurrentUser()


class _Namespace:
    def __init__(self, **attributes: Any) -> None:
        self.__dict__.update(attributes)


@pytest.fixture
def fake_workspace() -> FakeWorkspace:
    return FakeWorkspace()
