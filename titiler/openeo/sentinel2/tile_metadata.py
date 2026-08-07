"""Sentinel-2 MTD_TL.xml (per-granule tile metadata) parsing.

Parses the view/sun geometry needed for `viewZenithMean`/`viewAzimuthMean`/
`sunZenithAngles`/`sunAzimuthAngles` (docs/adr/0004-sentinel2-view-sun-angle-
bands.md). Every Sentinel-2 product ships this file at
`GRANULE/<granule>/MTD_TL.xml`; all three catalogues this project targets
publish it as a STAC asset (`granule_metadata` on CDSE/Earth Search,
`granule-metadata` on Planetary Computer) with the identical ESA-produced
XML content -- verified live, 2026-08-07, by downloading and parsing Earth
Search's copy directly.

The root and its immediate children (`General_Info`, `Geometric_Info`, ...)
carry a PSD-version-specific XML namespace (e.g.
`https://psd-15.sentinel2.eo.esa.int/...`); their own descendants
(`Tile_Geocoding`, `Tile_Angles`, ...) do not. Elements are matched by local
tag name only, ignoring namespace, so a future processing baseline's
namespace bump does not require a code change here -- mirroring
`sar/annotation.py`'s own handling of its noise-schema version split.

XML is parsed with `defusedxml` rather than the stdlib `xml.etree`, for the
same reason as `sar/annotation.py` (billion-laughs payload expansion,
ADR 0001 S9.2).
"""

from dataclasses import dataclass
from threading import Condition
from typing import List, Optional
from xml.etree.ElementTree import Element

import numpy as np
from cachetools import LRUCache, cached
from cachetools.keys import hashkey
from defusedxml import ElementTree as ET

from ..grid2d import Grid2D
from ..sar.fetcher import AssetFetcher, get_default_fetcher
from ..settings import Sentinel2Settings

__all__ = [
    "TileGeocoding",
    "TileAngles",
    "TileMetadata",
    "parse_tile_metadata",
    "get_tile_metadata",
]

_settings = Sentinel2Settings()


def _local_name(tag: str) -> str:
    """Strip a `{namespace}` prefix, if any, leaving the bare tag name."""
    return tag.rsplit("}", 1)[-1]


def _child(elem: Element, name: str) -> Element:
    """First direct child matching `name` by local tag name (namespace-agnostic).

    Raises a clear error naming the missing element, mirroring
    `sar/annotation.py`'s `_text` helper.
    """
    for c in elem:
        if _local_name(c.tag) == name:
            return c
    raise ValueError(f"MTD_TL.xml element <{elem.tag}> is missing <{name}>")


def _child_with_attr(
    elem: Element, name: str, attr_name: str, attr_value: str
) -> Element:
    for c in elem:
        if _local_name(c.tag) == name and c.attrib.get(attr_name) == attr_value:
            return c
    raise ValueError(
        f"MTD_TL.xml element <{elem.tag}> has no <{name} {attr_name}={attr_value!r}>"
    )


def _children(elem: Element, name: str) -> List[Element]:
    return [c for c in elem if _local_name(c.tag) == name]


def _text(elem: Element) -> str:
    """`elem.text`, or a clear error naming the empty element.

    `Element.text` is `Optional[str]` even for elements a real, valid
    MTD_TL.xml never leaves empty (e.g. `<ULX>600000</ULX>`) -- this makes
    that guarantee explicit rather than asserting it at every call site.
    """
    if elem.text is None:
        raise ValueError(f"MTD_TL.xml element <{elem.tag}> has no text content")
    return elem.text


@dataclass(frozen=True)
class TileGeocoding:
    """This tile's own affine georeferencing -- Sentinel-2 imagery is not
    GCP-referenced, so this (plus a CRS reprojection) is all a reader needs
    to place a destination pixel within the tile's own angle grids."""

    crs: str
    ulx: float
    uly: float


@dataclass(frozen=True)
class TileAngles:
    """`sun_grid` values: `{"zenith": (23, 23), "azimuth": (23, 23)}`, degrees,
    in the same (row, col) axes as `Grid2D.lines`/`.pixels` (metres offset
    from the tile's own upper-left corner). `mean_view_zenith`/
    `mean_view_azimuth` are the mean-across-bands scalars (ADR 0004 S1.3) --
    no per-pixel signal is available at this granularity."""

    sun_grid: Grid2D
    mean_view_zenith: float
    mean_view_azimuth: float


@dataclass(frozen=True)
class TileMetadata:
    geocoding: TileGeocoding
    angles: TileAngles


def _circular_mean_degrees(angles_deg: List[float]) -> float:
    """Mean of angles that wrap at 360 degrees.

    A naive arithmetic mean is wrong near the wrap boundary (mean of 359 and
    1 is 0, not 180). Degrades to the arithmetic mean when values don't
    wrap: verified against a real tile's 13-band view azimuth list, circular
    mean 102.9626524696764 vs arithmetic 102.96270254490969 -- a ~1e-4 degree
    difference (docs/adr/0004-sentinel2-view-sun-angle-bands.md S1.3).

    A mean that lands exactly on the wrap boundary (e.g. angles [359, 1])
    can compute as ``360.0`` rather than ``0.0``: ``arctan2`` returns a
    representable value an epsilon below zero, and ``x % 360.0`` for such an
    epsilon rounds to ``360.0`` in float64, since 360.0's own precision at
    that magnitude can't represent the difference. Both denote the same
    angle, but ``360.0`` is a surprising value for a "degrees" field to
    return, so it is normalized to ``0.0``.
    """
    radians = np.radians(angles_deg)
    result = float(
        np.degrees(np.arctan2(np.mean(np.sin(radians)), np.mean(np.cos(radians))))
        % 360.0
    )
    return 0.0 if result == 360.0 else result


def _parse_angle_grid(el: Element) -> "tuple[float, float, np.ndarray]":
    """One `Zenith` or `Azimuth` element -> (row_step, col_step, values[R, C])."""
    row_step = float(_text(_child(el, "ROW_STEP")))
    col_step = float(_text(_child(el, "COL_STEP")))
    values_list = _child(el, "Values_List")
    rows = [_text(row).split() for row in _children(values_list, "VALUES")]
    return row_step, col_step, np.array(rows, dtype="f8")


def parse_tile_metadata(xml: bytes) -> TileMetadata:
    """Parse one `MTD_TL.xml`.

    Raises `ValueError` naming the missing element for anything required
    and absent, mirroring `sar/annotation.py`'s `_text` helper.
    """
    root = ET.fromstring(xml)
    geometric_info = _child(root, "Geometric_Info")

    geocoding_el = _child(geometric_info, "Tile_Geocoding")
    crs = _text(_child(geocoding_el, "HORIZONTAL_CS_CODE"))
    geoposition = _child_with_attr(geocoding_el, "Geoposition", "resolution", "10")
    ulx = float(_text(_child(geoposition, "ULX")))
    uly = float(_text(_child(geoposition, "ULY")))

    tile_angles = _child(geometric_info, "Tile_Angles")
    sun_grid_el = _child(tile_angles, "Sun_Angles_Grid")
    z_row_step, z_col_step, zenith = _parse_angle_grid(_child(sun_grid_el, "Zenith"))
    a_row_step, a_col_step, azimuth = _parse_angle_grid(_child(sun_grid_el, "Azimuth"))
    if (z_row_step, z_col_step) != (
        a_row_step,
        a_col_step,
    ) or zenith.shape != azimuth.shape:
        raise ValueError(
            "MTD_TL.xml Sun_Angles_Grid Zenith/Azimuth grids disagree in step or shape"
        )

    lines = z_row_step * np.arange(zenith.shape[0])
    pixels = z_col_step * np.arange(zenith.shape[1])
    sun_grid = Grid2D(lines, pixels, {"zenith": zenith, "azimuth": azimuth})

    mv_list = _child(tile_angles, "Mean_Viewing_Incidence_Angle_List")
    entries = _children(mv_list, "Mean_Viewing_Incidence_Angle")
    if not entries:
        raise ValueError("MTD_TL.xml Mean_Viewing_Incidence_Angle_List has no entries")
    zeniths = [float(_text(_child(e, "ZENITH_ANGLE"))) for e in entries]
    azimuths = [float(_text(_child(e, "AZIMUTH_ANGLE"))) for e in entries]

    return TileMetadata(
        geocoding=TileGeocoding(crs=crs, ulx=ulx, uly=uly),
        angles=TileAngles(
            sun_grid=sun_grid,
            mean_view_zenith=float(np.mean(zeniths)),
            mean_view_azimuth=_circular_mean_degrees(azimuths),
        ),
    )


_tile_metadata_cache: LRUCache = LRUCache(maxsize=_settings.tile_metadata_cache_maxsize)
_tile_metadata_cache_condition = Condition()


@cached(
    _tile_metadata_cache,
    key=lambda href, fetcher=None: hashkey(href),
    condition=_tile_metadata_cache_condition,
)
def get_tile_metadata(
    href: str, fetcher: Optional[AssetFetcher] = None
) -> TileMetadata:
    """Fetch and parse a granule's `MTD_TL.xml`, cached by href.

    Mirrors `sar/annotation.py`'s `get_calibration`/`get_noise` exactly --
    `condition=` makes concurrent misses for the same href single-flight.
    """
    fetcher = fetcher or get_default_fetcher()
    return parse_tile_metadata(fetcher.fetch(href))
