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

Increments 2 and 3 (issue #348) wire up both readers -- every band this
registry describes is now readable end to end. Each `BandSource.bands` entry
is a `(name_template, quantity)` pair: `quantity` is the method name to call
on the reader's underlying LUT object (`CalibrationLUT`/`NoiseLUT` in
`sar/annotation.py`), threaded through via the reader's `quantity`
constructor kwarg (`CalibrationBandReader`/`BandReader.part`).
"""

import re

from .readers import CalibrationBandReader, NoiseBandReader
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
    # 0002 S2.5. `quantity` is the CalibrationLUT method to call for each.
    BandSource(
        collection=_S1_GRD_COLLECTION,
        media_types=_XML,
        roles=_METADATA,
        asset=re.compile(r"schema-calibration-(?P<pol>[a-z]{2})"),
        bands=(
            ("{pol}_sigma0_lut", "sigma_nought"),
            ("{pol}_beta0_lut", "beta_nought"),
            ("{pol}_gamma0_lut", "gamma"),
            ("{pol}_dn_lut", "dn"),
            ("{pol}_ellipsoid_incidence_angle", "ellipsoid_incidence_angle"),
        ),
        sibling="{pol}",
        reader=CalibrationBandReader,
    ),
    # One noise annotation yields the thermal-noise LUT (annotation.NoiseLUT).
    # NoiseBandReader has exactly one quantity, so it ignores `quantity`.
    BandSource(
        collection=_S1_GRD_COLLECTION,
        media_types=_XML,
        roles=_METADATA,
        asset=re.compile(r"schema-noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
        sibling="{pol}",
        reader=NoiseBandReader,
    ),
]
