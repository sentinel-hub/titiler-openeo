# openEO by TiTiler Helm Chart

This Helm chart deploys openEO by TiTiler on a Kubernetes cluster. It provides a flexible deployment configuration with support for different database backends and service configurations.

## Prerequisites

* Kubernetes 1.16+
* Helm 3.0+
* PostgreSQL (optional, can be deployed as a subchart)

## Installation

Add the repository and install the chart. The chart needs authentication: at
least one basic-auth user, or OIDC (see
[Authentication Configuration](#authentication-configuration)).

```bash
helm install titiler-openeo . -n your-namespace \
  --set-string 'auth.basic.users[0].username=alice' \
  --set-string 'auth.basic.users[0].password=<choose-a-password>'
```

## Upgrading to 4.0.0

* Basic auth has no default user any more: set at least one user in
  `auth.basic.users` (username and password as quoted strings), supply the
  users from a Secret (see
  [Authentication Configuration](#authentication-configuration)), or use OIDC.
  `helm upgrade` fails until you do.
* `helm upgrade --reuse-values` keeps the old default user, `test` with the
  password `password`. The chart rejects this user, so `helm upgrade` fails
  until you replace it.
* Installs that already supply `TITILER_OPENEO_AUTH_USERS` through `env` or
  `envVars` must set `auth.basic.enabled=false`. `helm upgrade` fails until you
  do.
* Installs that configured users now get exactly those users; the app's
  built-in login stops working.
* OIDC installs are unaffected.
* Passwords are stored in the chart's ConfigMap and in the Helm release Secret.
* Pods read the users at start-up, so after changing users run
  `kubectl rollout restart deployment/<release>-titiler-openeo`.

## Deploying with CDSE (Copernicus Data Space Ecosystem)

To deploy an instance working with CDSE:

1. Obtain your S3 access credentials (access key and secret key) by following the instructions at ['Access to EO data via S3'](https://documentation.dataspace.copernicus.eu/APIs/S3.html) in the CDSE documentation.

2. Create a values file (e.g., `values-cdse.yaml`) with CDSE-specific configurations:

```yaml
# STAC configuration
stac:
  apiUrl: "https://stac.dataspace.copernicus.eu/v1"

env:
  # CDSE S3 Configuration
  AWS_S3_ENDPOINT: "eodata.dataspace.copernicus.eu"
  AWS_ACCESS_KEY_ID: "your_access_key"  # Add your S3 access key from CDSE
  AWS_SECRET_ACCESS_KEY: "your_secret_key"  # Add your S3 secret key from CDSE
  AWS_VIRTUAL_HOSTING: "FALSE"
  # GDAL Performance Tuning
  CPL_VSIL_CURL_CACHE_SIZE: "200000000"
  GDAL_HTTP_MULTIPLEX: "TRUE"
  GDAL_CACHEMAX: "500"
  GDAL_INGESTED_BYTES_AT_OPEN: "32768"
  GDAL_HTTP_MERGE_CONSECUTIVE_RANGES: "YES"
  VSI_CACHE_SIZE: "536870912"
  VSI_CACHE: "TRUE"

resources:
  limits:
    cpu: 2
    memory: 4Gi
  requests:
    cpu: 1
    memory: 2Gi

auth:
  basic:
    users:
      - username: "alice"
        password: "<choose-a-password>"
```

3. Install the chart with CDSE values:

```bash
helm install titiler-openeo . -f values-cdse.yaml -n your-namespace
```

## Configuration

### Global Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Container image repository | `ghcr.io/sentinel-hub/titiler-openeo` |
| `image.tag` | Container image tag (defaults to `v{{ .Chart.AppVersion }}` when unset) | unset |
| `image.pullPolicy` | Container image pull policy | `IfNotPresent` |
| `initStore.image.repository` / `initStore.image.tag` | Image of the init-store init container (JSON store only) | `docker.io/library/busybox` / `1.38.0@sha256:…` |
| `replicaCount` | Number of replicas | `1` |
| `strategy` | Deployment update strategy. Empty uses the Kubernetes default (`RollingUpdate`). Set `type: Recreate` for upgrades that must stop all old pods first (see the admin guide upgrade notes) | `{}` |

### Database Configuration

The chart supports three database types:

1. JSON (default no persistence)

```yaml
database:
  type: "json"
  json:
    enabled: true
    path: "/mnt/data/store.json"
    seed: "files/eoapi.json"
```

2. DuckDB

```yaml
database:
  type: "duckdb"
  duckdb:
    enabled: true
    path: "/mnt/data/store.db"
```

3. PostgreSQL (using subchart)

```yaml
postgresql:
  enabled: true
  auth:
    database: openeo
    username: openeo
    password: "your-password"
database:
  type: "postgresql"
```

### PostgreSQL schema

With `database.type: postgresql`, or a `TITILER_OPENEO_STORE_URL` from
`envVars.fromSecret`, the API does not connect to the database or create tables
at startup. A pod whose database is down stays Running but not
Ready (`/readyz` returns 503), so it gets no traffic and is not restarted.

The tables come from a Job, `<release>-titiler-openeo-schema`
(`titiler-openeo-schema` for a release named `titiler-openeo`). It runs
`python -m titiler.openeo.services.create_schema` with the API's image, store
URLs and `env` map. It creates the missing tables and never alters or drops
one, except the one-time `udp_definitions` key change of ADR 0012 (see the
admin guide's upgrade notes), so it is safe to run on every upgrade.

* **When**: the Job is a `post-install,post-upgrade` Helm hook. With `--wait`,
  it runs once the release's pods are Ready; readiness checks the connection,
  not the tables, so this cannot deadlock. With the bundled PostgreSQL, an init
  container also waits until PostgreSQL accepts connections. It is not a
  pre-install or pre-upgrade hook: pre-install runs before the bundled
  PostgreSQL exists, and pre-upgrade runs before an upgrade could start, resize
  or create it. If the release fails before its hooks (for example `--wait`
  times out because `/readyz` also checks the STAC API), the Job does not run
  until the next install or upgrade.
* **New tables**: the API can serve before the Job has run. On a fresh install
  (a few seconds), and after an upgrade that adds a table, requests that use a
  missing table fail until the Job completes (with `--wait`, for the whole
  rollout).
* **Bundled PostgreSQL without persistence** (`postgresql.persistence.enabled:
  false`): every new PostgreSQL pod starts with an empty database, and nothing
  creates the tables again until the next install or upgrade runs the Job. Run
  the command in an API pod (see below) to restore them sooner.
* **Image**: an image older than the command (titiler-openeo 0.19.0 and
  before, or an image built on one) creates its tables at startup, so with it
  the Job logs that it has nothing to do and succeeds.
* **Failures**: the Job retries 3 times, then fails the release. A failed Job is
  kept until the next install or upgrade replaces it:
  `kubectl logs job/<release>-titiler-openeo-schema` (add
  `-c wait-for-postgresql` for the init container). A successful Job is
  deleted.
* **Scheduling**: the Job uses the API's `resources`, `nodeSelector`,
  `tolerations`, `imagePullSecrets`, `serviceAccountName` and security
  contexts, but not its `affinity`. On a cluster with no room for one more
  API-sized pod (for example at `autoscaling.maxReplicas`), the Job stays
  Pending until the Helm timeout, and the release fails after the rollout.
* **Environment**: the Job gets `TITILER_OPENEO_STORE_URL` and
  `TITILER_OPENEO_TILE_STORE_URL` from the same values as the API (`database`,
  `env`, `envVars`), and the `env` map (for example `PGSSLMODE`), but no other
  `envVars` entry and no volume. A DSN that needs a file from
  `extraHostPathMounts` (such as `sslrootcert=/certs/ca.pem`) fails in the Job.
* **JSON or DuckDB store with a PostgreSQL tile store** (a
  `TITILER_OPENEO_TILE_STORE_URL` set through `env` or `envVars`): there is no
  Job, so nothing creates `tile_assignments`. After install and each upgrade,
  run the command in an API pod (it skips the JSON or DuckDB store):
  `kubectl exec deploy/<release>-titiler-openeo -- python -m titiler.openeo.services.create_schema`.
* **`helm template | kubectl apply`** (and kustomize `helmCharts`): the Job is
  applied like any other resource. A Job's pod template cannot change, so
  delete the completed Job before applying a new version, or render with
  `--no-hooks` and run the command yourself.
* **Argo CD** runs the Job once per sync, as a PostSync hook, after every
  resource of the application is Healthy. If one stays Progressing (for example
  an Ingress that never gets a load-balancer address), the Job never runs, and
  a new database gets no tables.
* **Outside Helm**, run `python -m titiler.openeo.services.create_schema` with
  the same `TITILER_OPENEO_STORE_URL` (and `TITILER_OPENEO_TILE_STORE_URL`)
  before starting a new version.

### Persistence Configuration

```yaml
database:
  persistence:
    enabled: true
    size: 1Gi
    storageClassName: "standard"
```

### Ingress Configuration

```yaml
ingress:
  enabled: true
  className: "nginx"
  annotations:
    kubernetes.io/ingress.class: nginx
  hosts:
    - host: titiler.yourdomain.com
      paths: ["/"]
```

### STAC Configuration

```yaml
stac:
  apiUrl: "https://stac.eoapi.dev"  # STAC API endpoint URL
```

### Authentication Configuration

The chart supports two authentication methods:

1. Basic Authentication (for testing)

Basic authentication is the default and has no default user: rendering fails
until `auth.basic.users` has at least one user with a username and a password.
The chart passes them to the app as `TITILER_OPENEO_AUTH_USERS` through a
ConfigMap, so anyone who can read ConfigMaps in the namespace can read the
passwords.

```yaml
auth:
  method: "basic"
  basic:
    enabled: true
    users:
      - username: "alice"
        password: "<choose-a-password>"
```

To keep the passwords out of the ConfigMap, supply the users from a Secret.
The Secret key holds the JSON object that the app reads, for example
`{"alice":{"password":"<choose-a-password>"}}`:

```yaml
auth:
  method: "basic"
  basic:
    enabled: false
envVars:
  fromSecret:
    - name: TITILER_OPENEO_AUTH_USERS
      secretName: titiler-openeo-users
      secretKey: users
```

2. OpenID Connect (for production)

```yaml
auth:
  method: "oidc"
  oidc:
    enabled: true
    clientId: "your-client-id"
    wellKnownUrl: "https://your-provider/.well-known/openid-configuration"
    redirectUrl: "your-redirect-url"  # One URL, or several separated by spaces
    # Optional configurations
    scopes: "openid email profile"  # Space-separated list
    nameClaim: "name"  # Claim to use for user name
    title: "OIDC"  # Provider title
    description: "OpenID Connect (OIDC) Authorization Code Flow with PKCE"  # Provider description
    audiences: ""  # Extra accepted `aud` values, space-separated
    userIdClaim: ""  # Claim used as the user id; empty = "sub"
    allowedTenants: ""  # Microsoft Entra tenant ids, space-separated; empty = all
    providerId: ""  # Provider id in GET /credentials/oidc, e.g. "CDSE"; empty = "oidc"
    grantTypes: ""  # Flows the client registration allows, space-separated or a list; empty = PKCE, device code and refresh
```

For Microsoft Entra ID with work, school and personal accounts, use the
multi-tenant `common` well-known URL
(`https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration`).
See `ci/planetarycomputer-values.yaml` and the
[OpenID Connect guide](../../../docs/src/openid-connect.md).

### Resource Configuration

```yaml
resources:
  limits:
    cpu: 1
    memory: 2Gi
  requests:
    cpu: 1
    memory: 2Gi
```

### Autoscaling Configuration

```yaml
autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 80
```

## Dependencies

* PostgreSQL (optional): Version 16.6.6 from Bitnami charts repository

## Contributing

This chart is maintained by [Development Seed](https://github.com/developmentseed).
