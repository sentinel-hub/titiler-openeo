# Kubernetes Deployment Guide

This guide explains how to deploy openEO by TiTiler on Kubernetes using Helm. The implementation is available in the [`deployment/k8s`](https://github.com/sentinel-hub/titiler-openeo/tree/main/deployment/k8s) directory.

## Prerequisites

- Kubernetes 1.16+
- Helm 3.0+
- PostgreSQL (optional, can be deployed as a subchart)

## Quick Start

For local testing with Minikube:

```bash
# Start Minikube
minikube start

# Set context
kubectl config use-context minikube

# Install using Helm
cd deployment/k8s
helm upgrade --install openeo-titiler .

# Enable ingress (if needed)
minikube addons enable ingress

# Get service URL
minikube service ingress-nginx-controller -n ingress-nginx --url | head -n 1
```

## Configuration

For detailed configuration options, refer to the [Helm chart README](https://github.com/sentinel-hub/titiler-openeo/blob/main/deployment/k8s/charts/README.md). The README provides comprehensive documentation on:

- Global parameters
- Database configuration (JSON, DuckDB, PostgreSQL)
- Persistence settings
- Ingress configuration
- Resource management
- Autoscaling options

### CDSE Integration

For deploying with Copernicus Data Space Ecosystem (CDSE), follow the instructions in the [CDSE deployment section](https://github.com/sentinel-hub/titiler-openeo/blob/main/deployment/k8s/charts/README.md#deploying-with-cdse-copernicus-data-space-ecosystem) of the Helm chart documentation.

## Production Deployment Considerations

When deploying to production:

1. Configure appropriate resource limits and requests
2. Enable and configure persistent storage
3. Set up proper ingress with TLS
4. Configure authentication
5. Tune environment variables for performance
6. Enable monitoring and logging
7. **Run PostgreSQL via a production-grade operator** — the bundled
   `postgresql.*` StatefulSet is a single-replica convenience install and is
   not suitable for production. See [Production PostgreSQL with
   CloudNativePG](kubernetes-cloudnativepg.md) for the recommended HA setup
   with backups and point-in-time recovery.

For specific configuration values and examples, refer to the [configuration section](https://github.com/sentinel-hub/titiler-openeo/blob/main/deployment/k8s/charts/README.md#configuration) in the Helm chart documentation.

## Health Endpoints

The application exposes two Kubernetes-friendly health endpoints. The bundled
Helm chart wires them to the pod's probes by default.

- `GET /healthz` — **liveness**. Dependency-free; returns `200 {"status":"ok"}`
  as long as the FastAPI process is responsive. Used as `livenessProbe.httpGet.path`
  so a transient backend outage never triggers an infinite restart loop.
- `GET /readyz` — **readiness**. Runs a bounded (≤2 s) check against every
  configured backend: services store, optional tile store, STAC API, and the
  OIDC well-known endpoint when OIDC auth is enabled. Returns `200` only when
  every check passes; otherwise `503` with a structured body listing which
  check failed. Used as `readinessProbe.httpGet.path` so an unhealthy pod is
  removed from the Service's endpoints until it recovers.

To avoid hammering backends, the chart's `readinessProbe` uses
`periodSeconds: 60`. The per-check timeout can be tuned with
`TITILER_OPENEO_HEALTH_CHECK_TIMEOUT` (default `2.0`).

## Troubleshooting

### Common Issues

1. Pod Startup Failures
   - Check resource limits
   - Verify storage configuration
   - Check logs: `kubectl logs -l app=openeo-titiler`

2. Database Connection Issues
   - Verify database configuration
   - Check connectivity to external database
   - Validate persistent volume claims

3. Ingress Problems
   - Verify ingress controller is running
   - Check ingress configuration
   - Validate TLS certificates

For more details on configuration options and deployment scenarios, see the [Helm chart documentation](https://github.com/sentinel-hub/titiler-openeo/blob/main/deployment/k8s/charts/README.md).
