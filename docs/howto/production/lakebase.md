# Use Databricks Lakebase

[Lakebase](https://docs.databricks.com/aws/en/oltp/) is the managed Postgres
service of Databricks. Procrastinate's schema works on it unchanged, including
`LISTEN/NOTIFY`. What differs is authentication: instead of a static password,
Lakebase expects an OAuth token that expires after one hour.

{py:class}`procrastinate.contrib.lakebase.LakebaseConnector` takes care of
that. It generates a database credential through the Databricks SDK and reuses
it until shortly before it expires, so every new connection is opened with a
valid one. This includes the connection the worker uses to listen for
notifications.

## Install

```console
$ pip install 'procrastinate[lakebase]'
```

The connector needs `psycopg_pool` 3.3 or later.

## Create the app

```python
import procrastinate
from procrastinate.contrib.lakebase import LakebaseConnector

app = procrastinate.App(
    connector=LakebaseConnector(
        endpoint="projects/my-project/branches/production/endpoints/primary",
    )
)
```

`endpoint` is the resource path of the Lakebase endpoint (called a *compute* in
the Databricks UI). The other parameters are optional:

- `database`: the Postgres database, `databricks_postgres` by default.
- `user`: the Postgres role. By default this is the identity the Databricks SDK
  is authenticated as: your user name, or the application ID of a service
  principal.
- `host`: the endpoint hostname. By default it is looked up from `endpoint`.
- `workspace_client`: a `databricks.sdk.WorkspaceClient`. By default the
  connector creates one, which uses the SDK's
  [default authentication](https://docs.databricks.com/aws/en/dev-tools/auth/unified-auth)
  (environment variables, a configuration profile, or the ambient identity of a
  Databricks App or job).
- `refresh_margin`: how long before expiry a credential stops being reused,
  five minutes by default.

Any other keyword argument is passed to
{py:class}`procrastinate.PsycopgConnector`, for example `max_size`. `conninfo`
and `kwargs` are not accepted, because the connector builds the connection
parameters itself.

Nothing is requested from Databricks until the app is opened, so the app can be
created at import time.

## Apply the schema

```console
$ procrastinate --app=myapp.app schema --apply
```

The role that applies the schema owns the Procrastinate tables. A worker that
connects as a different role (a service principal, for instance) needs to be
granted access to them by the owning role.

## Scale to zero

When a Lakebase endpoint suspends, its connections are closed. The connector
needs no configuration for this: the pool replaces dead connections and each
new connection gets a valid credential. If the listener cannot reconnect, it
logs a warning and tries again every 5 seconds; the worker keeps polling for
jobs in the meantime. The first query after a suspension is slower while the
endpoint resumes.

Lakebase suspends an endpoint after it has been idle for the configured
timeout. A running worker holds connections and polls for jobs every
`fetch_job_polling_interval` (5 seconds by default), so expect the endpoint to
stay active for as long as a worker is running.

## Troubleshooting

If the app cannot authenticate when it opens (a wrong endpoint, or no access to
the workspace), opening fails with a `psycopg_pool.PoolTimeout` after the
pool's timeout, as it does for wrong credentials with any Postgres server. The
reason is logged by `procrastinate.contrib.lakebase.credentials` at error
level.
