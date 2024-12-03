"""ABC base backend."""

import abc
from typing import Dict, List

from attrs import define


@define(kw_only=True)
class BaseBackend(metaclass=abc.ABCMeta):
    """ABC Class defining STAC Backends."""

    @abc.abstractmethod
    def get_collections(self, **kwargs) -> List[Dict]:
        """Return List of STAC Collections."""
        ...

    @abc.abstractmethod
    def get_collection(self, collection_id: str, **kwargs) -> Dict:
        """Return STAC Collection"""
        ...

    @abc.abstractmethod
    def get_items(self, collection_id: str, **kwargs) -> List[Dict]:
        """Return List of STAC Items."""
        ...

    @abc.abstractmethod
    def get_lifespan(self):
        """FastAPI lifespan method."""
        ...
