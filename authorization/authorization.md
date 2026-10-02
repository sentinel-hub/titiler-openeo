# Service Authorization

TiTiler OpenEO implements a flexible service authorization mechanism that controls access to the **served instance** of a secondary web service (the XYZ/WMTS/WMS tile endpoint published as the service's `url`). Each service can be configured with different access levels for that instance through the `scope` parameter.

**Important scope of this feature:** `scope` governs who can fetch tiles from the tile-serving endpoint (`GET /services/xyz/{service_id}/tiles/{z}/{x}/{y}`, i.e. what `service.url` points to). It never makes `GET /services/{service_id}` or any other `/services*` management endpoint anonymous — those always require Bearer authentication, matching the openEO spec exactly (`security: [Bearer: []]`, with no anonymous variant, unlike `GET /service_types`). This distinction between the always-private control plane (`/services/{service_id}`) and the back-end-defined data plane (`service.url`) is intentional in the spec, not an oversight — see [ADR 0003](../adr/0003-service-access-control.md) for the full writeup, including an earlier, incorrect attempt to make the metadata endpoint follow `scope` as well (reverted).

`GET /services/{service_id}` also checks ownership: the owner can always read the service; another authenticated user can read it only if its scope is `public` (for example, to show a shared service). Other users get `403`, **including the users that a `restricted` service lets fetch tiles**: they can use the service's `url`, but they cannot read its definition. A non-owner never sees `authorized_users`. Anonymous requests still get `401` for every scope ([ADR 0003 §8](../adr/0003-service-access-control.md#8-amendment-2026-09-29--ownership-check-on-get-servicesservice_id)). `PATCH` and `DELETE` are for the owner only.

**Note on the openEO spec:** the openEO API specification does not define any access-control property for secondary web services at all — `configuration.scope` is entirely a TiTiler OpenEO extension governing only how titiler-openeo happens to serve tiles. Whether this is worth proposing upstream, and if so in what form, is an open question currently being discussed with the openEO maintainers; see [ADR 0003](../adr/0003-service-access-control.md) for the current status.

## Scopes

Services can be configured with one of three scopes:

- `private`: Only the service owner can fetch tiles from the service
- `restricted`: Any authenticated user can fetch tiles, with optional user-specific restrictions (`authorized_users`). The owner always has access, even if not in the list; `"authorized_users": []` gives access to the owner only
- `public` (current default — see the note below): No authentication required to fetch tiles

**Note on the default:** the current default is `public`, which contradicts the "use `private` by default" guidance in [Best Practices](#best-practices) below. This is a known inconsistency, tracked in [ADR 0003](../adr/0003-service-access-control.md#5-consequences); flipping the default is a deployment-visible behaviour change and will ship as an explicit, settings-controlled opt-in rather than silently.

## Configuration

Authorization is configured through the service configuration object when creating or updating a service:

```json
{
  "configuration": {
    "scope": "restricted",
    "authorized_users": ["user1", "user2"]  // Optional: specific users for restricted scope
  }
}
```

### Configuration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `scope` | string | Access scope: `private`, `restricted`, or `public` |
| `authorized_users` | array | Optional list of user IDs allowed to access a restricted service |

### Validation

- `scope` is case-insensitive and is stored in lowercase (`"Private"` is stored as `"private"`).
- A `scope` that is not `private`, `restricted` or `public` is refused with `400`. The same applies to an `authorized_users` value that is not a list of strings.
- `"scope": null` is refused with `400`: a missing scope means `public`, so a service cannot lose its scope by an update.
- `authorized_users` is accepted only with `"scope": "restricted"`, on create and on update. When an update changes the scope away from `restricted`, the stored `authorized_users` is removed.
- If a stored service has a `scope` value that is not valid (for example, a row written outside the API), the service is treated as `private`.

### Updates (`PATCH`)

`configuration` is merged key by key. The keys in the request replace the stored keys, and all other stored keys stay. For example, `{"configuration": {"tile_size": 512}}` keeps the stored `scope`. To remove a key, set it to `null` (except `scope`, see above). A key that the request leaves out is never removed. This differs from the openEO spec, where a client sends the complete `configuration` object; a client that does so gets the same result, except that it must send `null` to remove a key.

Only the fields in the request are written, so two updates of different fields do not overwrite each other. A new `process` gets the same validation as on creation.

### Disabled services

A service with `"enabled": false` serves no tiles: the tile endpoint returns `404`.

### Caching

- Tiles of a `public` service use the `TITILER_OPENEO_API_CACHE_TILES` policy (default `public, max-age=3600`).
- Tiles of a `private` or `restricted` service use `TITILER_OPENEO_API_CACHE_TILES_PRIVATE` (default `private, max-age=3600`), so shared caches do not store them. They also carry `Vary: Authorization`, so two users of one browser do not share cached tiles.
- Tiles of any service whose process graph reads `_openeo_user` or `_openeo_tile_store` are never stored (`no-store`), whatever the scope: the answer can differ for each caller, and for tile assignment a cached answer would replay a claim or release without reaching the server.

Errors are never stored (`no-store`), except a zoom level out of range (`400`) or no data (`404`) on a `public` service whose graph does not read the caller. These are the same for every caller and use `TITILER_OPENEO_API_CACHE_TILE_ERRORS`. See the [admin guide](admin-guide.md#cache-control).

## Implementation

The authorization mechanism is implemented in two main components:

1. `ServiceAuthorizationManager` class (`titiler/openeo/services/auth.py`):
   - Encapsulates authorization logic
   - Validates access based on service configuration and user context
   - Throws appropriate HTTP exceptions for unauthorized access

2. Service endpoints:
   - Retrieve service configuration
   - Use ServiceAuthorizationManager to enforce access control
   - Pass authorized requests to the service implementation

## Example Usage

For example:

```json
{
  "configuration": {
    "scope": "restricted",
    "authorized_users": ["user1", "user2"],
  }
}
```

The behavior of the injected user parameter depends on how it's defined in the process's JSON schema:

1. When the parameter schema defines `"type": "string"`:

```json
{
  "parameters": {
    "user_id": {
      "type": "string",
      "description": "User identifier"
    }
  }
}
```

The process will receive just the user ID string, even when using from_parameter:

```json
{
  "process_graph": {
    "example1": {
      "process_id": "example_process",
      "arguments": {
        "user_id": {
          "from_parameter": "_openeo_user"  // Will extract just the user_id
        }
      }
    }
  }
}
```

2. When the parameter schema defines a User object type:

```json
{
  "parameters": {
    "user": {
      "type": "object",
      "description": "User object with full properties"
    }
  }
}
```

The process will receive the complete User object:

```json
{
  "process_graph": {
    "example1": {
      "process_id": "example_process",
      "arguments": {
        "user": {
          "from_parameter": "_openeo_user"  // Will provide the full User object
        }
      }
    }
  }
}
```

```python
from titiler.openeo.services.auth import ServiceAuthorizationManager

# In your service endpoint:
service = services_store.get_service(service_id)
auth_manager = ServiceAuthorizationManager()
auth_manager.authorize(service, user)  # Raises HTTPException if access denied
```

## Authorization Flow

1. Client requests a service endpoint
2. Service configuration is retrieved from the store
3. ServiceAuthorizationManager validates access based on:
   - Service scope
   - User authentication status
   - User authorization (for restricted services)
4. If access is denied:
   - 401 Unauthorized - For missing authentication
   - 403 Forbidden - For insufficient permissions (an authenticated user who is not the owner of a `private` service, or who is not in `authorized_users` of a `restricted` service)
5. If access is granted, the request proceeds to service execution

## User Injection

If the service call is authenticated, the authenticated user will be injected into the process graph as a named parameter `_openeo_user`. Thus any process graph parameter can reference the authenticated user by using `from_parameter: "_openeo_user"`.

## Best Practices

1. Always set an appropriate scope for your services
2. Use `private` scope by default for maximum security
3. For restricted services, explicitly list authorized users
4. Consider using `public` scope only for non-sensitive data
5. Regularly audit service configurations and access patterns
