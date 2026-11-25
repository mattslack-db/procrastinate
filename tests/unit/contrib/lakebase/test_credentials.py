from __future__ import annotations

import datetime
import threading

import pytest

from procrastinate import exceptions
from procrastinate.contrib.lakebase import credentials

from .conftest import ENDPOINT, HOST, USER, FakeWorkspace


def make_provider(
    workspace: FakeWorkspace, **kwargs: object
) -> credentials.LakebaseCredentialProvider:
    return credentials.LakebaseCredentialProvider(
        endpoint=ENDPOINT,
        workspace_client=workspace,
        clock=lambda: workspace.postgres.now,
        **kwargs,  # type: ignore[arg-type]
    )


def test_call__returns_connection_kwargs(fake_workspace: FakeWorkspace):
    provider = make_provider(fake_workspace)

    assert provider() == {
        "host": HOST,
        "dbname": "databricks_postgres",
        "user": USER,
        "password": "token-1",
        "sslmode": "require",
    }


def test_init__does_not_touch_the_network(fake_workspace: FakeWorkspace):
    make_provider(fake_workspace)

    assert fake_workspace.postgres.endpoint_calls == []
    assert fake_workspace.postgres.credential_calls == []
    assert fake_workspace.current_user.calls == 0


def test_call__looks_up_host_and_user_once(fake_workspace: FakeWorkspace):
    provider = make_provider(fake_workspace)

    provider()
    provider()

    assert fake_workspace.postgres.endpoint_calls == [ENDPOINT]
    assert fake_workspace.current_user.calls == 1


def test_call__explicit_host_and_user_skip_lookups(fake_workspace: FakeWorkspace):
    provider = make_provider(
        fake_workspace, host="explicit.example.com", user="sp-id", database="jobs"
    )

    result = provider()

    assert result["host"] == "explicit.example.com"
    assert result["user"] == "sp-id"
    assert result["dbname"] == "jobs"
    assert fake_workspace.postgres.endpoint_calls == []
    assert fake_workspace.current_user.calls == 0


def test_call__reuses_token_before_refresh_margin(fake_workspace: FakeWorkspace):
    provider = make_provider(fake_workspace)

    first = provider()
    fake_workspace.postgres.now = 3600 - 301  # just before the 5 minute margin
    second = provider()

    assert first["password"] == second["password"] == "token-1"
    assert len(fake_workspace.postgres.credential_calls) == 1


def test_call__mints_new_token_inside_refresh_margin(fake_workspace: FakeWorkspace):
    provider = make_provider(fake_workspace)

    provider()
    fake_workspace.postgres.now = 3600 - 300  # exactly at the margin
    second = provider()

    assert second["password"] == "token-2"


def test_call__custom_refresh_margin(fake_workspace: FakeWorkspace):
    provider = make_provider(
        fake_workspace, refresh_margin=datetime.timedelta(minutes=30)
    )

    provider()
    fake_workspace.postgres.now = 1800
    second = provider()

    assert second["password"] == "token-2"


def test_call__credential_without_expiry_is_not_cached(fake_workspace: FakeWorkspace):
    fake_workspace.postgres.token_lifetime = None
    provider = make_provider(fake_workspace)

    first = provider()
    second = provider()

    assert (first["password"], second["password"]) == ("token-1", "token-2")


def test_call__mint_failure_propagates_and_next_call_retries(
    fake_workspace: FakeWorkspace,
):
    provider = make_provider(fake_workspace)
    provider()
    fake_workspace.postgres.now = 4000  # cached token has expired
    fake_workspace.postgres.error = RuntimeError("workspace unreachable")

    with pytest.raises(RuntimeError, match="workspace unreachable"):
        provider()

    fake_workspace.postgres.error = None
    assert provider()["password"] == "token-3"


def test_call__endpoint_without_host_raises_and_retries(
    fake_workspace: FakeWorkspace,
):
    fake_workspace.postgres.host = None
    provider = make_provider(fake_workspace)

    with pytest.raises(exceptions.ConnectorException, match="has no host"):
        provider()

    fake_workspace.postgres.host = HOST
    assert provider()["host"] == HOST


def test_call__empty_token_raises(fake_workspace: FakeWorkspace, mocker):
    mocker.patch.object(
        fake_workspace.postgres,
        "generate_database_credential",
        return_value=mocker.Mock(token="", expire_time=None),
    )
    provider = make_provider(fake_workspace)

    with pytest.raises(
        exceptions.ConnectorException, match="empty database credential"
    ):
        provider()


def test_call__concurrent_calls_mint_once(fake_workspace: FakeWorkspace):
    # A slow mint keeps the first caller inside the provider while the others
    # arrive, so this fails if the lock is removed.
    fake_workspace.postgres.mint_delay = 0.05
    provider = make_provider(fake_workspace)
    results: list[str] = []

    def call() -> None:
        results.append(provider()["password"])

    threads = [threading.Thread(target=call) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["token-1"] * 20
    assert len(fake_workspace.postgres.credential_calls) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "my-project",
        "projects/my-project",
        "projects/my-project/branches/production",
        "projects/my-project/branches/production/endpoints/",
        "projects//branches/production/endpoints/primary",
    ],
)
def test_init__rejects_malformed_endpoint(fake_workspace: FakeWorkspace, endpoint: str):
    with pytest.raises(ValueError, match="projects/<project>/branches/<branch>"):
        credentials.LakebaseCredentialProvider(
            endpoint=endpoint, workspace_client=fake_workspace
        )


def test_call__mint_failure_log_has_no_token(fake_workspace: FakeWorkspace, caplog):
    provider = make_provider(fake_workspace)
    provider()
    fake_workspace.postgres.now = 4000
    fake_workspace.postgres.error = RuntimeError("boom")

    with pytest.raises(RuntimeError), caplog.at_level("ERROR"):
        provider()

    assert ENDPOINT in caplog.text
    assert "token-1" not in caplog.text


def test_init__missing_sdk_raises_at_construction(monkeypatch):
    class MissingSdk:
        def __getattr__(self, item: str):
            raise ImportError("No module named 'databricks'")

    monkeypatch.setattr(credentials, "databricks_sdk", MissingSdk())

    with pytest.raises(ImportError, match="databricks"):
        credentials.LakebaseCredentialProvider(endpoint=ENDPOINT)


def test_call__host_lookup_failure_is_logged(fake_workspace: FakeWorkspace, caplog):
    fake_workspace.postgres.endpoint_error = RuntimeError("endpoint not found")
    provider = make_provider(fake_workspace)

    with (
        pytest.raises(RuntimeError, match="endpoint not found"),
        caplog.at_level("ERROR"),
    ):
        provider()

    assert "look up the host" in caplog.text
    assert ENDPOINT in caplog.text
