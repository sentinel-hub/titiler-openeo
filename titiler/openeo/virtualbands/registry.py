"""Registry binding virtual band plugins to collections.

Plugins are discovered through Python entry points in the
``titiler.openeo.virtual_bands`` group and bound to collections via a
configuration mapping::

    {
      "<collection_id>": [
        {"plugin": "<entry_point_name>", "options": {...}},
        ...
      ]
    }
"""

from importlib.metadata import entry_points
from typing import Dict, List, NamedTuple, Optional

from .base import BandMetadata, VirtualBandPlugin

__all__ = ["VirtualBandRegistry", "SplitBands", "ENTRY_POINT_GROUP"]

ENTRY_POINT_GROUP = "titiler.openeo.virtual_bands"


class SplitBands(NamedTuple):
    """Result of splitting requested bands against a collection's plugins."""

    real: List[str]
    """Requested bands that are real assets (in requested order)."""

    virtual: List[str]
    """Requested bands that are virtual (in requested order)."""

    support: List[str]
    """Real bands required to compute the requested virtual bands but not
    themselves requested for output (deduplicated, deterministic order)."""


def _load_plugin_classes() -> Dict[str, type]:
    """Load virtual band plugin classes registered via entry points."""
    classes: Dict[str, type] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        classes[ep.name] = ep.load()
    return classes


class VirtualBandRegistry:
    """Maps collection ids to the virtual band plugins bound to them."""

    def __init__(self, plugins: Optional[Dict[str, List[VirtualBandPlugin]]] = None):
        """Initialize with a mapping of collection id -> plugin instances."""
        self._plugins: Dict[str, List[VirtualBandPlugin]] = plugins or {}

    @classmethod
    def empty(cls) -> "VirtualBandRegistry":
        """Return a registry with no plugins."""
        return cls({})

    @classmethod
    def from_config(cls, config: Optional[Dict]) -> "VirtualBandRegistry":
        """Build a registry from a configuration mapping.

        Args:
            config: Mapping of collection id to a list of
                ``{"plugin": name, "options": {...}}`` entries. ``None`` or an
                empty mapping yields an empty registry.

        Raises:
            ValueError: If a referenced plugin name is not registered as an
                entry point.
        """
        if not config:
            return cls.empty()

        available = _load_plugin_classes()
        plugins: Dict[str, List[VirtualBandPlugin]] = {}
        for collection_id, entries in config.items():
            instances: List[VirtualBandPlugin] = []
            for entry in entries:
                name = entry["plugin"]
                if name not in available:
                    raise ValueError(
                        f"Unknown virtual band plugin '{name}' for collection "
                        f"'{collection_id}'. Registered plugins: "
                        f"{sorted(available)}"
                    )
                options = entry.get("options", {})
                instances.append(available[name](**options))
            plugins[collection_id] = instances
        return cls(plugins)

    def has_plugins(self, collection_id: str) -> bool:
        """Whether any plugin is bound to the collection."""
        return bool(self._plugins.get(collection_id))

    def plugins_for(self, collection_id: str) -> List[VirtualBandPlugin]:
        """Return the plugins bound to the collection."""
        return self._plugins.get(collection_id, [])

    def provided_band_metadata(self, collection_id: str) -> List[BandMetadata]:
        """Return BandMetadata for every virtual band of the collection."""
        meta: List[BandMetadata] = []
        for plugin in self.plugins_for(collection_id):
            meta.extend(plugin.provided_bands())
        return meta

    def virtual_band_names(self, collection_id: str) -> List[str]:
        """Return virtual band names for the collection (deterministic order)."""
        return [b.name for b in self.provided_band_metadata(collection_id)]

    def plugin_for_band(
        self, collection_id: str, name: str
    ) -> Optional[VirtualBandPlugin]:
        """Return the plugin that provides band ``name``, if any."""
        for plugin in self.plugins_for(collection_id):
            if any(b.name == name for b in plugin.provided_bands()):
                return plugin
        return None

    def split(self, collection_id: str, requested_bands: List[str]) -> SplitBands:
        """Partition ``requested_bands`` into real, virtual, and support bands.

        Order of ``real`` and ``virtual`` follows ``requested_bands``. ``support``
        lists real bands required by the requested virtual bands that are not
        themselves in ``requested_bands``.
        """
        virtual_names = set(self.virtual_band_names(collection_id))

        real: List[str] = []
        virtual: List[str] = []
        for band in requested_bands:
            if band in virtual_names:
                virtual.append(band)
            else:
                real.append(band)

        support: List[str] = []
        seen = set(real)
        for name in virtual:
            plugin = self.plugin_for_band(collection_id, name)
            if plugin is None:
                continue
            for req in plugin.required_bands():
                if req not in seen:
                    support.append(req)
                    seen.add(req)

        return SplitBands(real=real, virtual=virtual, support=support)
