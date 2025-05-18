"""Service authorization management for titiler-openeo."""

from typing import Any, Dict, Optional

from attrs import define
from fastapi import HTTPException

from titiler.openeo.auth import User


@define
class ServiceAuthorizationManager:
    """Handles service-specific authorization logic."""
    
    def authorize(
        self,
        service: Dict[str, Any],
        user: Optional[User],
    ) -> None:
        """
        Authorize access to a service based on its configuration.
        Raises HTTPException if access is denied.

        Args:
            service: Service configuration dictionary
            user: Optional authenticated user

        Raises:
            HTTPException: If access is denied based on service configuration
        """
        configuration = service.get("configuration", {})
        scope = configuration.get("scope", "private")

        if scope == "private":
            if not user or user.user_id != service.get("user_id"):
                raise HTTPException(401, "Authentication required for private service")
                
        elif scope == "restricted":
            if not user:
                raise HTTPException(401, "Authentication required for restricted service")
            
            authorized_users = configuration.get("authorized_users")
            if authorized_users is not None and user.user_id not in authorized_users:
                raise HTTPException(403, "User not authorized to access this service")
        
        # For scope == "public", no authentication needed
