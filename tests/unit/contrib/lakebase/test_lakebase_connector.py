from __future__ import annotations

import packaging.version
import pytest

from procrastinate import psycopg_connector, sync_psycopg_connector
from procrastinate.contrib import lakebase
from procrastinate.contrib.lakebase import credentials

from .conftest import ENDPOINT, HOST, FakeWorkspace


def test_init__passes_provider_as_pool_kwargs(fake_workspace: FakeWorkspace):
    connector = lakebase.LakebaseConnector(
        endpoint=ENDPOINT, workspace_client=fake_workspace, max_size=3
    )

    provider = connector.credential_provider
    assert isinstance(provider, credentials.LakebaseCredentialProvider)
    assert connector._pool_args == {"kwargs": provider, "max_size": 3}
    assert provider()["host"] == HOST


def test_init__does_not_touch_the_network(fake_workspace: FakeWorkspace):
    lakebase.LakebaseConnector(endpoint=ENDPOINT, workspace_client=fake_workspace)

    assert fake_workspace.postgres.credential_calls == []
    assert fake_workspace.postgres.endpoint_calls == []


def test_init__forwards_provider_options(fake_workspace: FakeWorkspace):
    connector = lakebase.LakebaseConnector(
        endpoint=ENDPOINT,
        database="jobs",
        user="sp-id",
        host="explicit.example.com",
        workspace_client=fake_workspace,
    )

    result = connector.credential_provider()

    assert (result["dbname"], result["user"], result["host"]) == (
        "jobs",
        "sp-id",
        "explicit.example.com",
    )


@pytest.mark.parametrize("argument", ["conninfo", "kwargs"])
def test_init__rejects_arguments_owned_by_the_provider(
    fake_workspace: FakeWorkspace, argument: str
):
    with pytest.raises(TypeError, match=argument):
        lakebase.LakebaseConnector(
            endpoint=ENDPOINT, workspace_client=fake_workspace, **{argument: "x"}
        )


def test_init__rejects_old_psycopg_pool(fake_workspace: FakeWorkspace, monkeypatch):
    monkeypatch.setattr(
        psycopg_connector, "psycopg_pool_version", packaging.version.Version("3.2.6")
    )

    with pytest.raises(RuntimeError, match=r"psycopg_pool>=3\.3\.0"):
        lakebase.LakebaseConnector(endpoint=ENDPOINT, workspace_client=fake_workspace)


def test_init__unknown_psycopg_pool_version_is_accepted(
    fake_workspace: FakeWorkspace, monkeypatch
):
    monkeypatch.setattr(psycopg_connector, "psycopg_pool_version", None)

    lakebase.LakebaseConnector(endpoint=ENDPOINT, workspace_client=fake_workspace)


def test_init__malformed_endpoint(fake_workspace: FakeWorkspace):
    with pytest.raises(ValueError, match="projects/<project>/branches/<branch>"):
        lakebase.LakebaseConnector(
            endpoint="my-project", workspace_client=fake_workspace
        )


def test_get_sync_connector__shares_the_provider(fake_workspace: FakeWorkspace):
    connector = lakebase.LakebaseConnector(
        endpoint=ENDPOINT, workspace_client=fake_workspace
    )

    sync_connector = connector.get_sync_connector()

    assert isinstance(sync_connector, sync_psycopg_connector.SyncPsycopgConnector)
    assert sync_connector._pool_args["kwargs"] is connector.credential_provider
