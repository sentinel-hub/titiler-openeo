"""Service authorization management for titiler-openeo.

This module implements a flexible authorization system for OpenEO services.
It provides a centralized way to manage service access based on configurable
scopes and user permissions.

Authorization scopes:
    - private: Only the service owner can access
    - restricted: Any authenticated user can access, with optional user restrictions
    - public: No authentication required

Example:
    ```python
    service = services_store.get_service(service_id)
    auth_manager = ServiceAuthorizationManager()
    auth_manager.authorize(service, user)  # Raises HTTPException if access denied
    ```

See `docs/src/authorization.md` for detailed documentation.
"""

from typing import Any, Dict, Optional

from attrs import define
from fastapi import HTTPException

from titiler.openeo.auth import User


@define
class ServiceAuthorizationManager:
    """Handles service-specific authorization logic.

    This class provides a centralized way to manage service access control.
    It validates user access based on service configuration and throws
    appropriate HTTP exceptions for unauthorized access.

    The authorization process considers:
    - Service scope (private, restricted, public)
    - User authentication status
    - User-specific permissions for restricted services

    Service Configuration Example:
        ```json
        {
            "configuration": {
                "scope": "restricted",
                "authorized_users": ["user1", "user2"]
            }
        }
        ```
    """

    def authorize(
        self,
        service: Dict[str, Any],
        user: Optional[User],
    ) -> None:
        """Authorize access to a service based on its configuration.

        This method implements the core authorization logic for service access.
        It evaluates the service's scope and user context to determine if
        access should be granted.

        Args:
            service: Service configuration dictionary containing:
                - configuration.scope: Service access scope
                - configuration.authorized_users: Optional list of allowed users
                - user_id: Service owner ID
            user: Optional authenticated user with user_id attribute

        Raises:
            HTTPException:
                - 401 Unauthorized if authentication is required but missing
                - 403 Forbidden if user doesn't have sufficient permissions

        Example:
            ```python
            service = {
                "user_id": "owner123",
                "configuration": {
                    "scope": "restricted",
                    "authorized_users": ["user1", "user2"]
                }
            }
            auth_manager.authorize(service, current_user)
            ```
        """
        configuration = service.get("configuration", {})
        scope = configuration.get("scope", "public")

        if scope == "private":
            if not user or user.user_id != service.get("user_id"):
                raise HTTPException(401, "Authentication required for private service")

        elif scope == "restricted":
            if not user:
                raise HTTPException(
                    401, "Authentication required for restricted service"
                )

            authorized_users = configuration.get("authorized_users")
            if authorized_users is not None and user.user_id not in authorized_users:
                raise HTTPException(403, "User not authorized to access this service")

        # For scope == "public", no authentication needed
