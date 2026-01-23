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

For specific configuration values and examples, refer to the [configuration section](https://github.com/sentinel-hub/titiler-openeo/blob/main/deployment/k8s/charts/README.md#configuration) in the Helm chart documentation.

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
