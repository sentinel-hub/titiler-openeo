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
  `auth.basic.users` (username and password as quoted strings) or use OIDC.
  `helm upgrade` fails until you do.
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
| `image.tag` | Container image tag (defaults to `titiler-openeo-{{ .Chart.AppVersion }}` when unset) | `titiler-openeo-0.12.0` |
| `image.pullPolicy` | Container image pull policy | `IfNotPresent` |
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
