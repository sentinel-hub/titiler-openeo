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

#### Asset Signing ([`SigningSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py))

Which signer this deployment's private assets need. Empty — the default — means
no signing. Setting an unregistered name fails loudly at first use.

```bash
TITILER_OPENEO_ASSET_SIGNER=""  # "planetary-computer" for Microsoft Planetary Computer
```

> **Changed in 0.18.0.** Signing used to switch on by itself when
> `TITILER_OPENEO_STAC_API_URL` named `planetarycomputer.microsoft.com`. It is
> now configured explicitly
> ([#377](https://github.com/sentinel-hub/titiler-openeo/issues/377)), so
> deployments upgrading from 0.17.x must set this variable or their private asset
> reads will fail with HTTP 409.

#### Planetary Computer Settings ([`PlanetaryComputerSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py))

Only consulted when `TITILER_OPENEO_ASSET_SIGNER` is `"planetary-computer"`. All
optional — the defaults work with no credentials. See
[Microsoft Planetary Computer](planetary-computer.md).

```bash
TITILER_OPENEO_PC_SUBSCRIPTION_KEY=""  # Optional; raises SAS API rate limits
TITILER_OPENEO_PC_SAS_URL="https://planetarycomputer.microsoft.com/api/sas/v1"
TITILER_OPENEO_PC_EXPIRY_MARGIN=300  # Seconds before expiry to mint a new token
TITILER_OPENEO_PC_TIMEOUT=10  # Per-request timeout when minting a token
```

#### Processing Settings ([`ProcessingSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L182))

```bash
TITILER_OPENEO_PROCESSING_MAX_PIXELS=100000000
TITILER_OPENEO_PROCESSING_MAX_ITEMS=20
```

#### Store Settings ([`StoreSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py))

Connection pool for SQL stores such as PostgreSQL. They do not apply to SQLite, JSON or DuckDB.

The services store and the UDP store share one pool per process. Each process can open up to `POOL_SIZE + MAX_OVERFLOW` connections, so keep `replicas x (POOL_SIZE + MAX_OVERFLOW)` below the database's `max_connections`.

```bash
TITILER_OPENEO_STORE_POOL_SIZE=5
TITILER_OPENEO_STORE_MAX_OVERFLOW=10
TITILER_OPENEO_STORE_POOL_TIMEOUT=30      # Seconds to wait for a free connection
TITILER_OPENEO_STORE_POOL_RECYCLE=-1      # Seconds before a connection is replaced; -1 disables
TITILER_OPENEO_STORE_POOL_PRE_PING=false  # Test a connection before use
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

### User Sessions

The backend records one row per session, not one per request, so you can count active users. A session starts once:

- **Basic auth:** at `/credentials/basic`, where the token is issued.
- **OIDC:** on the first request of an identity-provider session, identified by the token's `sid` claim. The `sid` claim stays the same when the client refreshes its token, so a refresh is not a new session. The identity provider must emit `sid`; without it no OIDC session is recorded and the backend logs one warning.

Ordinary requests never write to these tables. If the database is unreachable, the failure is logged and the request is not rejected.

Two tables hold the data. `user_sessions` is append-only, with one row per session: `user_id`, `provider`, `session_id`, `started_at`. `user_tracking` keeps one row per user and provider (`first_login`, `last_login`, `login_count`, `email`, `name`). `login_count` counts sessions.

Monthly active users, on PostgreSQL:

```sql
SELECT date_trunc('month', started_at) AS month,
       count(DISTINCT user_id)         AS active_users
FROM user_sessions
GROUP BY 1
ORDER BY 1;
```

A session is counted in the month it started. The end of a session is not stored, so a long session that crosses a month boundary is not counted in the second month.

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
TITILER_OPENEO_API_CACHE_TILES="public, max-age=3600"
TITILER_OPENEO_API_CACHE_TILES_PRIVATE="private, max-age=3600"
TITILER_OPENEO_API_CACHE_TILE_ERRORS="private, max-age=60"
TITILER_OPENEO_API_CACHE_DYNAMIC="no-cache"
TITILER_OPENEO_API_CACHE_DEFAULT="no-store"
```

- `CACHE_STATIC`: For static resources like CSS, JS files
- `CACHE_TILES`: For XYZ tiles (`/services/xyz/`) of `public` services, allowing browsers and shared caches to cache tiles
- `CACHE_TILES_PRIVATE`: For XYZ tiles of `private` and `restricted` services. **This replaces `CACHE_TILES` for these tiles.** It must not allow shared caches (no `public`, no `s-maxage`). If you make `CACHE_TILES` stricter (for example `no-store`), set this one too
- `CACHE_TILE_ERRORS`: For XYZ tile `400` and `404` responses that are the same for every caller (zoom level out of range, no data), on `public` services only. Keep it short and `private`. It is not used when `CACHE_TILES` contains `no-store`
- `CACHE_DYNAMIC`: For dynamic API endpoints that need fresh data
- `CACHE_DEFAULT`: Default policy for other successful responses

All other error responses get `no-store`, whatever these settings are. Tiles whose process graph reads the caller (`_openeo_user`, `_openeo_tile_store`) always get `no-store`.

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
