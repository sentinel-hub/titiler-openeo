# Service Authorization

TiTiler OpenEO implements a flexible service authorization mechanism that controls access to services based on their configuration. Each service can be configured with different access levels through the `scope` parameter.

## Scopes

Services can be configured with one of three scopes:

- `private` (default): Only the service owner can access the service
- `restricted`: Any authenticated user can access, with optional user-specific restrictions
- `public`: No authentication required, anyone can access the service

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

## Best Practices

1. Always set an appropriate scope for your services
2. Use `private` scope by default for maximum security
3. For restricted services, explicitly list authorized users
4. Consider using `public` scope only for non-sensitive data
5. Regularly audit service configurations and access patterns
