"""
Runs against a real Lakebase endpoint. Skipped unless
LAKEBASE_TEST_ENDPOINT is set. Optional:
LAKEBASE_TEST_PROFILE (Databricks config profile) and
LAKEBASE_TEST_DATABASE.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

import procrastinate
from procrastinate.contrib import lakebase

ENDPOINT = os.environ.get("LAKEBASE_TEST_ENDPOINT")
NOTIFY_DEADLINE = 15.0
LISTEN_DEADLINE = 30.0
POLLING_INTERVAL = 120.0

pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="LAKEBASE_TEST_ENDPOINT is not set"
)


@pytest.fixture
async def lakebase_app():
    import databricks.sdk

    workspace_client = databricks.sdk.WorkspaceClient(
        profile=os.environ.get("LAKEBASE_TEST_PROFILE")
    )
    app = procrastinate.App(
        connector=lakebase.LakebaseConnector(
            endpoint=ENDPOINT or "",
            database=os.environ.get("LAKEBASE_TEST_DATABASE", "databricks_postgres"),
            workspace_client=workspace_client,
        )
    )
    async with app.open_async():
        tables = await app.connector.execute_query_all_async(
            "SELECT 1 FROM pg_tables WHERE tablename = 'procrastinate_jobs'"
        )
        if not tables:
            await app.schema_manager.apply_schema_async()
        yield app


async def wait_until_listening(app: procrastinate.App, queue: str) -> None:
    # The listener's connection shows its LISTEN statement as its last query.
    pattern = f"LISTEN %procrastinate_queue_v1#{queue}%"
    async with asyncio.timeout(LISTEN_DEADLINE):
        while not await app.connector.execute_query_all_async(
            "SELECT 1 FROM pg_stat_activity "
            "WHERE query LIKE %(pattern)s AND pid <> pg_backend_pid()",
            pattern=pattern,
        ):
            await asyncio.sleep(0.5)


async def test_worker_runs_job_through_notify(lakebase_app: procrastinate.App):
    # A queue name unique to this run keeps leftovers of earlier runs out.
    queue = f"lakebase_{uuid.uuid4().hex[:12]}"
    ran = asyncio.Event()

    @lakebase_app.task(queue=queue, name=f"task_{queue}")
    async def mark_done() -> None:
        ran.set()

    worker = asyncio.create_task(
        lakebase_app.run_worker_async(
            queues=[queue],
            fetch_job_polling_interval=POLLING_INTERVAL,
            install_signal_handlers=False,
        )
    )
    try:
        await wait_until_listening(lakebase_app, queue)
        await asyncio.sleep(1)  # let the worker's initial fetch finish
        await mark_done.defer_async()
        # Polling is 120 s, so finishing in time proves NOTIFY delivery.
        await asyncio.wait_for(ran.wait(), timeout=NOTIFY_DEADLINE)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    provider = lakebase_app.connector.credential_provider  # type: ignore[attr-defined]
    assert provider._credential is not None


def test_sync_defer(lakebase_app: procrastinate.App):
    queue = f"lakebase_{uuid.uuid4().hex[:12]}"

    @lakebase_app.task(queue=queue, name=f"task_{queue}")
    def noop() -> None:
        pass

    job_id = noop.defer()

    assert isinstance(job_id, int)
