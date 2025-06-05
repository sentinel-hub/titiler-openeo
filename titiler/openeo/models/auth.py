"""titiler.openeo auth models."""

from typing import Optional

from pydantic import BaseModel

class User(BaseModel, extra="allow"):
    """User Model."""

    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None

class BasicAuthUser(User):
    """Basic Auth User Model."""

    password: str
