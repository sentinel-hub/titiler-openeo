"""Models for user-defined processes (UDP) with relaxed ID validation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .openapi import Link, ProcessGraph


class UserProcess(BaseModel):
    """User-defined process representation without strict ID pattern."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Identifier for the user-defined process")
    summary: Optional[str] = None
    description: Optional[str] = None
    parameters: Optional[List] = None
    returns: Optional[Dict[str, Any]] = None
    categories: List[str] = Field(default_factory=list)
    deprecated: bool = False
    experimental: bool = False
    process_graph: Dict[str, ProcessGraph] = Field(
        ..., description="Process graph definition"
    )
    exceptions: Optional[Dict[str, Any]] = None
    examples: Optional[List[Dict[str, Any]]] = None
    links: Optional[List[Dict[str, Any]]] = None


class ProcessGraphValidation(BaseModel):
    """Model for process graph validation endpoint (id is optional)."""

    model_config = ConfigDict(extra="ignore")

    process_graph: Dict[str, ProcessGraph] = Field(
        ..., description="Process graph definition"
    )
    parameters: Optional[List] = None


class UserProcesses(BaseModel):
    """Collection wrapper for UDP listings."""

    processes: List[UserProcess]
    links: List[Link]
