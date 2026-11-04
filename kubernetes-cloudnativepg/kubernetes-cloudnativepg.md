# Production PostgreSQL with CloudNativePG

The bundled `postgresql.*` StatefulSet shipped with the Helm chart is intended
for **development, CI, and single-tenant convenience installs only**. It runs a
single replica, has no automated backups, no point-in-time recovery, no
failover, and no upgrade tooling. Do not run it in production.

This guide walks through the recommended production setup: a PostgreSQL cluster
managed by [CloudNativePG (CNPG)](https://cloudnative-pg.io/) — a CNCF-graduated
Kubernetes operator that provides HA, streaming replication, backups to object
storage, point-in-time recovery, monitoring, and rolling minor/major upgrades.

The integration with `titiler-openeo` is intentionally minimal: CNPG generates
a Kubernetes `Secret` containing a ready-to-use connection URI, and the chart
already supports injecting that URI directly into the application via
`envVars.fromSecret`. No `lookup`, no helper-generated DSN.

## Why CloudNativePG

- **HA out of the box** — synchronous/asynchronous replicas, automatic failover
  (uses the Kubernetes API as the consensus layer; no external `etcd` or
  `consul`).
- **Backups to S3/GCS/Azure Blob** via Barman Cloud, with retention policies
  and point-in-time recovery.
- **Rolling upgrades** for minor versions and supervised major upgrades.
- **Built-in connection pooling** through the `Pooler` CR (PgBouncer under the
  hood).
- **Prometheus metrics** via a sidecar exporter, plus a maintained
  `PodMonitor`.
- **GitOps-friendly** — every operation is declarative. `helm template` /
  ArgoCD render the full picture without needing live-cluster lookups.

## Prerequisites

- Kubernetes 1.27+
- Helm 3.0+
- `cert-manager` installed in the cluster (CNPG uses it for its admission
  webhooks)
- An S3-compatible object store reachable from the cluster (optional, but
  required for backups and point-in-time recovery)

## 1. Install the CloudNativePG operator

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update

helm upgrade --install cnpg cnpg/cloudnative-pg \
  --namespace cnpg-system \
  --create-namespace \
  --version 0.22.x
```

Verify the operator is healthy before continuing:

```bash
kubectl -n cnpg-system rollout status deploy/cnpg-cloudnative-pg
kubectl get crd clusters.postgresql.cnpg.io
```

## 2. Provide credentials via a sealed/external secret (GitOps)

CNPG normally generates the `app` user password on first bootstrap. For a
GitOps workflow you almost always want to **own the password yourself** so the
chart can render the same Secret reference across all environments and so
`helm template` / `argocd diff` produce stable output.

Create a Secret of type `kubernetes.io/basic-auth` and reference it from the
`Cluster` (any tool that produces a normal Secret works — Sealed Secrets,
External Secrets Operator, SOPS, Vault Agent, etc.):

```yaml
# titiler-openeo-pg-app.yaml (this is what your sealed/external secret
# eventually decrypts to inside the cluster)
apiVersion: v1
kind: Secret
metadata:
  name: titiler-openeo-pg-app
  namespace: titiler-openeo
type: kubernetes.io/basic-auth
stringData:
  username: openeo
  password: "<strong-random-password>"
```

> **Tip:** generate the password once with
> `openssl rand -base64 32 | tr -d '/=+' | head -c 32`, then seal/encrypt it.

## 3. Define the PostgreSQL `Cluster`

The example below provisions a 3-instance highly-available cluster with daily
backups to S3 and a 7-day retention window. Tune the resource requests,
instance count, and storage size to your workload.

```yaml
# titiler-openeo-pg-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: titiler-openeo-pg
  namespace: titiler-openeo
spec:
  instances: 3
  imageName: ghcr.io/cloudnative-pg/postgresql:17.2-bookworm

  storage:
    size: 50Gi
    storageClass: gp3            # adjust to your cluster's default

  walStorage:
    size: 10Gi
    storageClass: gp3

  resources:
    requests:
      cpu: 500m
      memory: 2Gi
    limits:
      memory: 2Gi

  postgresql:
    parameters:
      max_connections: "200"
      shared_buffers: "512MB"
      effective_cache_size: "1536MB"
      work_mem: "16MB"
      maintenance_work_mem: "128MB"
      log_min_duration_statement: "500"   # log slow queries (>500ms)

  bootstrap:
    initdb:
      database: openeo
      owner: openeo
      secret:
        name: titiler-openeo-pg-app   # the Secret from step 2

  # Optional but strongly recommended: continuous backups to S3
  backup:
    barmanObjectStore:
      destinationPath: s3://your-bucket/titiler-openeo-pg
      s3Credentials:
        accessKeyId:
          name: barman-s3-creds
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: barman-s3-creds
          key: SECRET_ACCESS_KEY
      wal:
        compression: gzip
      data:
        compression: gzip
    retentionPolicy: "7d"

  monitoring:
    enablePodMonitor: true   # requires the Prometheus operator
```

Apply it and wait for the cluster to become `Ready`:

```bash
kubectl apply -f titiler-openeo-pg-cluster.yaml
kubectl -n titiler-openeo wait cluster/titiler-openeo-pg \
  --for=condition=Ready --timeout=10m
```

## 4. Locate the DSN secret CNPG manages for you

CNPG materialises a Secret named `<cluster-name>-app` (here:
`titiler-openeo-pg-app` — the same one you provided, now enriched). It
contains a `uri` key whose value is already a fully-formed PostgreSQL DSN
pointing at the **read/write** service:

```bash
kubectl -n titiler-openeo get secret titiler-openeo-pg-app \
  -o jsonpath='{.data.uri}' | base64 -d
# → postgresql://openeo:...@titiler-openeo-pg-rw:5432/openeo
```

This is the exact format `titiler-openeo` expects for
`TITILER_OPENEO_STORE_URL` and `TITILER_OPENEO_TILE_STORE_URL`. We can hand it
to the application without ever materialising the password in the rendered
chart manifests.

> The Secret also exposes individual `username`, `password`, `host`, `port`,
> `dbname`, `pgpass`, and `jdbc-uri` keys if you need them for sidecars or
> migration jobs.

## 5. Point the Helm chart at the CNPG-managed DSN

Disable the bundled in-chart postgres and inject the URI through
`envVars.fromSecret`. The chart's deployment template will render a
`secretKeyRef` (no plaintext) and **skip emitting its own helper-derived
`value:`** for that variable, so there is no risk of accidental override.

```yaml
# values-production.yaml
postgresql:
  enabled: false

database:
  type: postgresql
  # `database.external` is NOT required when the DSN is fully supplied via
  # envVars.fromSecret below.

envVars:
  fromSecret:
    - name: TITILER_OPENEO_STORE_URL
      secretName: titiler-openeo-pg-app
      secretKey: uri
    # Optional: only if you use the tile-assignment feature. By default,
    # point it at the same DSN — tile assignment uses SQLAlchemy tables
    # that coexist happily with the main store.
    - name: TITILER_OPENEO_TILE_STORE_URL
      secretName: titiler-openeo-pg-app
      secretKey: uri
```

Install or upgrade the release:

```bash
helm upgrade --install titiler-openeo \
  oci://ghcr.io/developmentseed/charts/titiler-openeo \
  --version <chart-version> \
  -n titiler-openeo \
  -f values-production.yaml
```

Verify that the rendered Deployment uses a `secretKeyRef` and not a
plaintext value:

```bash
kubectl -n titiler-openeo get deploy titiler-openeo \
  -o jsonpath='{.spec.template.spec.containers[0].env}' | jq '
    .[] | select(.name == "TITILER_OPENEO_STORE_URL")'
# → { "name": "TITILER_OPENEO_STORE_URL",
#     "valueFrom": { "secretKeyRef": { "name": "titiler-openeo-pg-app",
#                                      "key": "uri" } } }
```

## 6. (Optional) Add a PgBouncer pooler

For workloads with high connection churn (many short-lived API requests),
front the cluster with a CNPG `Pooler`. It exposes a Service named
`<pooler-name>-pooler-rw` that speaks the PostgreSQL wire protocol with
transaction-mode pooling.

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Pooler
metadata:
  name: titiler-openeo-pg-pooler
  namespace: titiler-openeo
spec:
  cluster:
    name: titiler-openeo-pg
  instances: 2
  type: rw
  pgbouncer:
    poolMode: transaction
    parameters:
      max_client_conn: "500"
      default_pool_size: "25"
```

Then either:

- override the `host` portion of the DSN by maintaining your own Secret that
  points at the pooler Service (`titiler-openeo-pg-pooler-rw`), or
- keep the CNPG-managed `*-app` Secret for migrations and other admin tasks
  and override only the runtime DSN.

A common pattern is to keep `titiler-openeo-pg-app` for one-off jobs and
maintain a sealed Secret (e.g. `titiler-openeo-pg-pooled`) for the running
application. The chart values then reference the pooled Secret in
`envVars.fromSecret`.

## 7. (Optional) Point-in-time recovery

Provided you configured `backup.barmanObjectStore` in step 3, you can
restore the cluster to any timestamp inside the retention window by
creating a new `Cluster` with `bootstrap.recovery`. See the [CNPG recovery
documentation](https://cloudnative-pg.io/documentation/current/recovery/)
for the full procedure; the important point for `titiler-openeo` is that
after recovery, the **Secret name does not change**, so the chart values
remain valid across recoveries without any redeploy.

## 8. Monitoring

If you set `monitoring.enablePodMonitor: true` in the `Cluster` spec and
have the Prometheus Operator installed, CNPG ships a curated set of
metrics out of the box, including:

- `cnpg_collector_*` — replication lag, WAL position, last completed
  backup time.
- `pg_stat_*` — standard PostgreSQL statistics.

Pair these with the [official CNPG Grafana dashboards](https://github.com/cloudnative-pg/grafana-dashboards)
for production-ready visibility.

## 9. Upgrades

Minor PostgreSQL upgrades are handled by bumping `spec.imageName` in the
`Cluster` resource — CNPG performs a rolling restart, draining replicas one
at a time. Major upgrades require the supervised `pg_upgrade` workflow; see
the [CNPG major upgrade guide](https://cloudnative-pg.io/documentation/current/major_upgrade/).

In both cases, `titiler-openeo` itself needs no redeploy: the Service name
and Secret stay the same.

## Migrating an existing install away from the bundled postgres

If you currently run the chart with `postgresql.enabled: true`, migrate in
this order to avoid downtime on the data plane:

1. Provision the CNPG `Cluster` alongside the existing install (steps 1–3).
2. Dump the bundled database and restore it into the CNPG cluster:

   ```bash
   kubectl exec -n titiler-openeo titiler-openeo-postgresql-0 -- \
     pg_dump -U openeo -d openeo -Fc > openeo.dump

   kubectl cp openeo.dump titiler-openeo/titiler-openeo-pg-1:/tmp/openeo.dump
   kubectl exec -n titiler-openeo titiler-openeo-pg-1 -- \
     pg_restore -U openeo -d openeo -1 /tmp/openeo.dump
   ```

3. Update `values.yaml` per step 5 and run `helm upgrade`. Once the new
   pods are healthy, the bundled StatefulSet, Service, and PVC become
   orphaned and can be deleted manually after a final verification.

## Troubleshooting

- **`titiler-openeo` pod `CrashLoopBackOff` with auth errors after the
  switch:** confirm the `uri` key in the Secret resolves to the
  `<cluster>-rw` Service, not `<cluster>-r` (the read-only endpoint). CNPG
  populates `host` correctly for the `app` user; if you crafted the DSN
  manually, double-check.
- **`helm template` shows no `TITILER_OPENEO_STORE_URL` at all:**
  verify `database.type: postgresql` is set. With other backends the chart
  intentionally omits the env var.
- **Pooler pods refuse connections:** ensure the `Pooler`'s implicit
  Secret (`<pooler>-pgbouncer`) exists and that the CNPG operator has
  reconciled it. The `cnpg.io/operator` logs will tell you what's
  missing.

## See also

- Chart configuration reference: [Helm chart README](https://github.com/sentinel-hub/titiler-openeo/blob/main/deployment/k8s/charts/README.md)
- CloudNativePG documentation: <https://cloudnative-pg.io/documentation/current/>
- Tile assignment feature: [Tile Assignment](tile-assignment.md)
