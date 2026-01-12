# Administrator Guide

This guide provides information for system administrators managing an openEO by TiTiler deployment. The implementation details can be found in the codebase, particularly in [`titiler/openeo/settings.py`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py) for configuration options.

## System Requirements

### Environment Variables

openEO by TiTiler is configured through environment variables. Key configuration areas include:

#### API Settings ([`ApiSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L89))

```bash
TITILER_OPENEO_API_NAME="openEO by TiTiler"
TITILER_OPENEO_API_CORS_ORIGINS="*"
TITILER_OPENEO_API_CORS_ALLOW_METHODS="GET,POST,PUT,PATCH,DELETE,OPTIONS"
TITILER_OPENEO_API_ROOT_PATH=""
TITILER_OPENEO_API_DEBUG=false
```

#### Backend Settings ([`BackendSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L127))

```bash
TITILER_OPENEO_STAC_API_URL="https://your-stac-api"
TITILER_OPENEO_STORE_URL="path-to-services-config"
TITILER_OPENEO_TILE_STORE_URL="optional-tile-store-url"
```

#### Processing Settings ([`ProcessingSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L182))

```bash
TITILER_OPENEO_PROCESSING_MAX_PIXELS=100000000
TITILER_OPENEO_PROCESSING_MAX_ITEMS=20
```

#### Cache Settings ([`CacheSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L196))

```bash
TITILER_OPENEO_CACHE_TTL=300
TITILER_OPENEO_CACHE_MAXSIZE=512
TITILER_OPENEO_CACHE_DISABLE=false
```

## Authentication

openEO by TiTiler supports two authentication methods:

1. Basic Authentication (default)
   - Configured through [`AuthSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L61)
   - Set `TITILER_OPENEO_AUTH_METHOD=basic`
   - Configure users in environment:

     ```bash
     TITILER_OPENEO_AUTH_USERS='{"user1": {"password": "pass1", "roles": ["user"]}}'
     ```

2. OpenID Connect
   - See [OpenID Connect Configuration](openid-connect.md) for details

## Performance Tuning

### Cache Configuration

The caching system can be tuned through the following settings:

- `TITILER_OPENEO_CACHE_TTL`: Time-to-live for cached items (seconds)
- `TITILER_OPENEO_CACHE_MAXSIZE`: Maximum number of items in cache
- `TITILER_OPENEO_CACHE_DISABLE`: Disable caching entirely

### Processing Limits

To prevent resource exhaustion:

- `TITILER_OPENEO_PROCESSING_MAX_PIXELS`: Maximum allowed pixels for image processing
- `TITILER_OPENEO_PROCESSING_MAX_ITEMS`: Maximum number of items (STAC items from a API search) in a request

## Monitoring

### API Endpoints

The application provides several endpoints for monitoring:

- `/health`: Health check endpoint
- `/docs`: OpenAPI documentation
- `/redoc`: Alternative API documentation

### Logging

Logging configuration is managed through `log_config.yaml`. The default configuration includes:

- Console output
- JSON formatting
- Different log levels for different components

## Security

### CORS Configuration

Configure CORS settings through:

```bash
TITILER_OPENEO_API_CORS_ORIGINS="domain1.com,domain2.com"
TITILER_OPENEO_API_CORS_ALLOW_METHODS="GET,POST,PUT,PATCH,DELETE,OPTIONS"
```

### Cache Control

Configure cache control headers:

```bash
TITILER_OPENEO_API_CACHE_STATIC="public, max-age=3600"
TITILER_OPENEO_API_CACHE_DYNAMIC="no-cache"
TITILER_OPENEO_API_CACHE_DEFAULT="no-store"
```

## Troubleshooting

### Common Issues

1. Authentication Failures
   - Check authentication method configuration
   - Verify user credentials or OIDC settings
   - Check token format and expiration

2. Performance Issues
   - Review cache settings
   - Check processing limits
   - Monitor system resources

3. CORS Issues
   - Verify CORS origins configuration
   - Check allowed methods
   - Review client requests

### Debug Mode

Enable debug mode for detailed logging:

```bash
TITILER_OPENEO_API_DEBUG=true
```

## Maintenance

### Backup Considerations

1. Configuration
   - Environment variables
   - Service configurations
   - Authentication settings

2. Data
   - Tile store data if used
   - Cache contents if persistent

### Updates

When updating openEO by TiTiler:

1. Review the changelog
2. Backup configuration
3. Test in a staging environment
4. Plan for downtime if needed
5. Update the application
6. Verify functionality

For implementation details, refer to the [source code](https://github.com/sentinel-hub/titiler-openeo/tree/main/titiler/openeo).
