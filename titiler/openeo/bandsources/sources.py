"""Shipped band-source entries.

Sentinel-1 GRD only, for now. Verified live against CDSE, Earth Search and
Planetary Computer (2026-08-06, docs/adr/0002-band-sources.md S1.2): all three
publish `schema-calibration-<pol>` and `schema-noise-<pol>` as
`application/xml` with role `metadata`, and all three currently use the
collection id `sentinel-1-grd`.

The band names mirror openEO's calibration vocabulary rather than ESA's XML
tag names (`sigmaNought`, `betaNought`, `gamma`, `dn`), and are polarisation-
prefixed throughout -- a flat `sigma0_lut` would collide the moment an item
has more than one polarisation, which every target catalogue's GRD items do.

Increment 2 (issue #348) wires up the noise entry's `reader` -- `<pol>_noise_lut`
is readable end to end. The calibration entry's `reader` stays `None` for now
(increment 3): `derive_bands` already advertises its five bands in
`cube:dimensions` (discovery, increment 1), but `resolve_band` cannot produce
them yet, so requesting one still raises a clear error at read time rather
than a wrong one.
"""

import re

from .readers import NoiseBandReader
from .registry import BandSource

__all__ = ["BAND_SOURCES"]

#: All three target catalogues use this id today; `search` (not `fullmatch`)
#: so a future deployment-specific suffix (e.g. a regional variant) still
#: matches.
_S1_GRD_COLLECTION = re.compile(r"sentinel-1-grd")

_XML = frozenset({"application/xml"})
_METADATA = frozenset({"metadata"})

BAND_SOURCES = [
    # One calibration annotation yields four LUT vectors plus the incidence
    # angle recovered from two of them (annotation.CalibrationLUT) -- ADR
    # 0002 S2.5.
    BandSource(
        collection=_S1_GRD_COLLECTION,
        media_types=_XML,
        roles=_METADATA,
        asset=re.compile(r"schema-calibration-(?P<pol>[a-z]{2})"),
        bands=(
            "{pol}_sigma0_lut",
            "{pol}_beta0_lut",
            "{pol}_gamma0_lut",
            "{pol}_dn_lut",
            "{pol}_ellipsoid_incidence_angle",
        ),
        sibling="{pol}",
    ),
    # One noise annotation yields the thermal-noise LUT (annotation.NoiseLUT).
    BandSource(
        collection=_S1_GRD_COLLECTION,
        media_types=_XML,
        roles=_METADATA,
        asset=re.compile(r"schema-noise-(?P<pol>[a-z]{2})"),
        bands=("{pol}_noise_lut",),
        sibling="{pol}",
        reader=NoiseBandReader,
    ),
]
