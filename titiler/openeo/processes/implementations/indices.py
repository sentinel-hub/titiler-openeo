"""titiler.openeo.processes indices."""

import re
from datetime import datetime
from typing import Dict, List, Optional, Union

import numpy

from .data_model import ImageData, RasterStack
from .math import normalized_difference

__all__ = ["ndvi", "ndwi"]

BandIdentifier = Union[int, str]

# rio-tiler labels each band read from an asset as ``<asset>_b<n>`` (see
# reader.SimpleSTACReader). For the common one-band-per-asset case that turns a
# requested band ``B08_10m`` into a cube band ``B08_10m_b1``, so band-name
# lookups must tolerate that trailing ``_b<n>`` suffix.
_BAND_SUFFIX_RE = re.compile(r"_b\d+$")


def _resolve_band_index(data: ImageData, band: BandIdentifier) -> int:
    """Resolve a band identifier to a 1-based band index.

    Accepts either a 1-based integer index (or its digit-string form) or a band
    name that is matched against ``data.band_descriptions``. The openEO spec for
    ``ndvi`` expects band *names* (e.g. ``"B08_10m"``), while the historic
    implementation took integer indices; both are supported here.

    Matching is progressive: exact name, then case-insensitive, then against the
    band name with a rio-tiler ``_b<n>`` suffix stripped (so ``B08_10m`` matches
    the read band ``B08_10m_b1``). A suffix match is only accepted when it is
    unambiguous.
    """
    # Integer (or digit string) => already a 1-based index.
    if isinstance(band, bool):  # bool is a subclass of int; reject it explicitly
        raise ValueError(f"Invalid band identifier: {band!r}")
    if isinstance(band, int):
        return band
    if isinstance(band, str) and band.isdigit():
        return int(band)

    names = data.band_descriptions or []

    # Exact match, then case-insensitive.
    if band in names:
        return names.index(band) + 1
    lowered = [n.lower() for n in names]
    if band.lower() in lowered:
        return lowered.index(band.lower()) + 1

    # Suffix-tolerant match: compare against band names with the rio-tiler
    # ``_b<n>`` suffix removed. Only accept when exactly one band matches.
    target = band.lower()
    matches = [
        i for i, n in enumerate(names) if _BAND_SUFFIX_RE.sub("", n).lower() == target
    ]
    if len(matches) == 1:
        return matches[0] + 1
    if len(matches) > 1:
        ambiguous = [names[i] for i in matches]
        raise ValueError(
            f"Band '{band}' is ambiguous; candidates: {ambiguous}. "
            "Specify the full band name."
        )

    raise ValueError(f"Band '{band}' not found. Available bands: {names or 'unknown'}")


def _normalized_difference_image(
    data: ImageData,
    first: BandIdentifier,
    second: BandIdentifier,
    name: str,
    target_band: Optional[str],
) -> ImageData:
    """Compute a normalized difference index for a single ImageData.

    When ``target_band`` is ``None`` the bands dimension is dropped and the
    result is a single-band image named ``name``. When ``target_band`` is set,
    the computed band is appended to the existing bands under that label.
    """
    first_idx = _resolve_band_index(data, first)
    second_idx = _resolve_band_index(data, second)

    firstb = data.array[first_idx - 1]
    secondb = data.array[second_idx - 1]
    nd = normalized_difference(firstb, secondb)

    if target_band is None:
        return ImageData(
            nd,
            assets=data.assets,
            crs=data.crs,
            bounds=data.bounds,
            band_descriptions=[name],
        )

    existing = list(data.band_descriptions or [])
    if target_band in existing:
        raise ValueError(f"A band named '{target_band}' already exists.")

    array = numpy.ma.concatenate([data.array, nd[numpy.newaxis, ...]], axis=0)
    band_descriptions: List[str] = existing + [target_band]
    return ImageData(
        array,
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_descriptions=band_descriptions,
    )


def ndwi(
    data: RasterStack,
    nir: BandIdentifier,
    swir: BandIdentifier,
    target_band: Optional[str] = None,
) -> RasterStack:
    """Apply NDWI to RasterStack.

    Args:
        data: RasterStack to process
        nir: NIR band, as a 1-based index or a band name
        swir: SWIR band, as a 1-based index or a band name
        target_band: If set, keep the bands dimension and append the NDWI band
            under this name; otherwise drop the bands dimension.

    Returns:
        RasterStack with NDWI results
    """
    result: Dict[datetime, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _normalized_difference_image(
            img_data, nir, swir, "ndwi", target_band
        )
    return RasterStack.from_images(result)


def ndvi(
    data: RasterStack,
    nir: BandIdentifier,
    red: BandIdentifier,
    target_band: Optional[str] = None,
) -> RasterStack:
    """Apply NDVI to RasterStack.

    Args:
        data: RasterStack to process
        nir: NIR band, as a 1-based index or a band name
        red: Red band, as a 1-based index or a band name
        target_band: If set, keep the bands dimension and append the NDVI band
            under this name; otherwise drop the bands dimension.

    Returns:
        RasterStack (Dict[datetime, ImageData]) containing NDVI results
    """
    result: Dict[datetime, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _normalized_difference_image(
            img_data, nir, red, "ndvi", target_band
        )
    return RasterStack.from_images(result)
