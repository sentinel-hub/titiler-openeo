# openEO by TiTiler Helm Chart

This Helm chart deploys openEO by TiTiler on a Kubernetes cluster. It provides a flexible deployment configuration with support for different database backends and service configurations.

## Prerequisites

* Kubernetes 1.16+
* Helm 3.0+
* PostgreSQL (optional, can be deployed as a subchart)

## Installation

Add the repository and install the chart:

```bash
helm install titiler-openeo . -n your-namespace
```

## Deploying with CDSE (Copernicus Data Space Ecosystem)

To deploy an instance working with CDSE:

1. Obtain your S3 access credentials (access key and secret key) by following the instructions at ['Access to EO data via S3'](https://documentation.dataspace.copernicus.eu/APIs/S3.html) in the CDSE documentation.

2. Create a values file (e.g., `values-cdse.yaml`) with CDSE-specific configurations:

```yaml
env:
  TITILER_OPENEO_STAC_API_URL: "https://stac.dataspace.copernicus.eu/v1"
  TITILER_OPENEO_SERVICE_STORE_URL: "services/copernicus.json"
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
| `image.tag` | Container image tag | `dev` |
| `image.pullPolicy` | Container image pull policy | `IfNotPresent` |
| `replicaCount` | Number of replicas | `1` |

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

- PostgreSQL (optional): Version 16.6.6 from Bitnami charts repository

## Contributing

This chart is maintained by [Development Seed](https://github.com/developmentseed).
