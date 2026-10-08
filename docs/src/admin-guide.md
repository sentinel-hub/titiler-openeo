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

##### Scale and offset

By default, `load_collection` and `load_stac` apply the scale/offset of each band, so that bands are physical values (for example, Sentinel-2 reflectance from 0 to 1) and not raw DN. There are three sources:

| Source | Where the values come from |
| --- | --- |
| `stac` | The `raster:scale`/`raster:offset` of the asset (or of `raster:bands`). |
| `cog` | The scale/offset in the GeoTIFF header. |
| `sentinel2-boa` | Plugin for `sentinel-2-l2a`: `BOA_QUANTIFICATION_VALUE` and `BOA_ADD_OFFSET` in the product metadata (`MTD_MSIL2A.xml`, asset `product_metadata` or `product-metadata`). Also `AOT` and `WVP`. |

The sources are an ordered list. For each band, the first source that has a value wins, so a band is scaled one time only. A source that does not apply to an item is skipped. For example, `sentinel2-boa` is skipped for an item that is not Sentinel-2 L2A or has no product metadata asset. Bands with no value in any listed source (for example, Sentinel-2 `SCL`) do not change and keep their integer type.

A plugin fetches its file only for a band that it knows, and only when no earlier source has a value for that band. The file is cached for each product (`TITILER_OPENEO_SENTINEL2_PRODUCT_METADATA_CACHE_MAXSIZE`, default 128). If a plugin applies but cannot fetch or read its file, the read fails, so raw DN is never returned without an error.

```bash
# Master switch. "false" keeps raw DN for all collections.
TITILER_OPENEO_PROCESSING_APPLY_SCALE_OFFSET=true
# Global order (JSON list or comma list). [] = no scale/offset.
TITILER_OPENEO_PROCESSING_SCALE_OFFSET_SOURCES='["stac", "sentinel2-boa", "cog"]'
# Per-collection order (JSON). A list here replaces the global order for that collection.
TITILER_OPENEO_PROCESSING_SCALE_OFFSET_COLLECTIONS='{"sentinel-2-l2a": ["sentinel2-boa"], "my-raw-collection": []}'
```

Recommended settings for Sentinel-2 L2A, checked on 2026-10-08:

| Catalogue and collection | STAC values | COG header | Setting |
| --- | --- | --- | --- |
| Microsoft Planetary Computer `sentinel-2-l2a` | none | 1/0 | `{"sentinel-2-l2a": ["sentinel2-boa"]}` |
| CDSE `sentinel-2-l2a` | correct | not checked | `{"sentinel-2-l2a": ["stac"]}` |
| Element84 Earth Search `sentinel-2-c1-l2a` | correct | correct | the default |
| Element84 Earth Search `sentinel-2-l2a` | **wrong on many items** | 1/0 | do not use; use `sentinel-2-c1-l2a` |

On the same clear area and dates, Planetary Computer with `sentinel2-boa` and Earth Search `sentinel-2-c1-l2a` with the default order gave a B04 median reflectance within 0.002 of each other, for a baseline 05.12 product (2026) and for a 2021 product.

Earth Search's legacy `sentinel-2-l2a` collection subtracted the 1000 DN offset from the pixels of many items, but its STAC still declares `offset: -0.1`. Its `earthsearch:boa_offset_applied` flag is also wrong on some items (Element84/earth-search#9, #66, #71). No metadata source is reliable for that collection: STAC values give negative reflectance, and the product XML (`sentinel2-boa`) has the same problem. Its `product_metadata` is also in a requester-pays bucket.

The collection is found from the `collection` field of each STAC item. Items with no `collection` field use the global order. An unknown or repeated source name stops the service at startup. See [ADR 0009](../adr/0009-scale-offset-sources.md).

#### Store Settings ([`StoreSettings`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py))

Connection pool for SQL stores such as PostgreSQL. They do not apply to SQLite, JSON or DuckDB. These settings need titiler-openeo newer than 0.18.2. Older versions ignore them, and each process then keeps two default pools of up to 15 connections each.

The services, UDP and tile stores share one pool per database URL and per worker process. Each process can open up to `POOL_SIZE + MAX_OVERFLOW` connections. A deployment runs `WEB_CONCURRENCY` worker processes per pod, so keep the worst case below the database's `max_connections` (less the 3 slots Postgres reserves for superusers):

```text
max pods × WEB_CONCURRENCY × (POOL_SIZE + MAX_OVERFLOW) < max_connections - 3
```

Max pods is the largest number of pods that can run at the same time: `autoscaling.maxReplicas` when autoscaling is on, else `replicaCount`, plus the extra pods of a rolling update (25% surge by default). For example, 10 pods with a 25% surge, 1 worker each and `max_connections = 100`: 13 × 1 × 7 = 91 < 97, so `POOL_SIZE=3` and `MAX_OVERFLOW=4`.

```bash
TITILER_OPENEO_STORE_POOL_SIZE=5
TITILER_OPENEO_STORE_MAX_OVERFLOW=10
TITILER_OPENEO_STORE_POOL_TIMEOUT=5       # Seconds to wait for a free connection
TITILER_OPENEO_STORE_POOL_RECYCLE=-1      # Seconds before a connection is replaced; -1 disables, 0 is rejected
TITILER_OPENEO_STORE_POOL_PRE_PING=false  # Test a connection before use
```

If the login record of an authenticated request cannot reach the database (for example, no free connection within `POOL_TIMEOUT`), the request gets `503 Service Unavailable` with a `Retry-After` header, not `401`.

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
