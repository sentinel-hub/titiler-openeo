# Service Authorization

TiTiler OpenEO implements a flexible service authorization mechanism that controls access to the **served instance** of a secondary web service (the XYZ/WMTS/WMS tile endpoint published as the service's `url`). Each service can be configured with different access levels for that instance through the `scope` parameter.

**Important scope of this feature:** `scope` governs only the tile-serving endpoint (`GET /services/xyz/{service_id}/tiles/{z}/{x}/{y}`, i.e. what `service.url` points to). It does **not** apply to `GET /services/{service_id}` or any other `/services*` management endpoint — those always require Bearer authentication, matching the openEO spec exactly (`security: [Bearer: []]`, with no anonymous variant, unlike `GET /service_types`). This distinction between the always-private control plane (`/services/{service_id}`) and the back-end-defined data plane (`service.url`) is intentional in the spec, not an oversight — see [ADR 0003](../adr/0003-service-access-control.md) for the full writeup, including an earlier, incorrect attempt to make the metadata endpoint follow `scope` as well (reverted).

**Note on the openEO spec:** the openEO API specification does not define any access-control property for secondary web services at all — `configuration.scope` is entirely a TiTiler OpenEO extension governing only how titiler-openeo happens to serve tiles. Whether this is worth proposing upstream, and if so in what form, is an open question currently being discussed with the openEO maintainers; see [ADR 0003](../adr/0003-service-access-control.md) for the current status.

## Scopes

Services can be configured with one of three scopes:

- `private`: Only the service owner can fetch tiles from the service
- `restricted`: Any authenticated user can fetch tiles, with optional user-specific restrictions
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
   - 403 Forbidden - For insufficient permissions
5. If access is granted, the request proceeds to service execution

## User Injection

If the service call is authenticated, the authenticated user will be injected into the process graph as a named parameter `_openeo_user`. Thus any process graph parameter can reference the authenticated user by using `from_parameter: "_openeo_user"`.

## Best Practices

1. Always set an appropriate scope for your services
2. Use `private` scope by default for maximum security
3. For restricted services, explicitly list authorized users
4. Consider using `public` scope only for non-sensitive data
5. Regularly audit service configurations and access patterns
