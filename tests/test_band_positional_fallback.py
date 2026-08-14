"""`bands` positional fallback in SimpleSTACReader._get_options.

`_get_options` builds a name -> index map from the asset's STAC band metadata,
preferring `eo:common_name`, then `common_name`, then `name`, and falling back
to the band's 1-based position when a band object carries none of those.

The fallback key must be a string: `bands` values reach this code as strings
(the openEO `bands` argument, rio-tiler's asset options), so an integer key can
never match one and the fallback would be unreachable.
"""

import pystac
import pytest

from titiler.openeo.reader import SimpleSTACReader


def _reader() -> SimpleSTACReader:
    """A reader instance without running __attrs_post_init__ (no I/O needed)."""
    return SimpleSTACReader.__new__(SimpleSTACReader)


def _asset(bands: list[dict]) -> pystac.Asset:
    return pystac.Asset(
        href="s3://example/asset.tif",
        media_type="image/tiff",
        extra_fields={"bands": bands},
    )


def test_unnamed_bands_resolve_by_position():
    """A band object with no name at all is selectable by its 1-based position."""
    metadata = _asset([{"description": "first"}, {"description": "second"}])

    _, method_options = _reader()._get_options(
        {"name": "data", "bands": ["2"]}, metadata
    )

    assert method_options["indexes"] == [2]


def test_named_bands_still_win_over_position():
    """Names take precedence; the positional fallback only fills the gaps."""
    metadata = _asset(
        [
            {"name": "red", "eo:common_name": "red"},
            {"description": "unnamed"},
        ]
    )

    _, method_options = _reader()._get_options(
        {"name": "data", "bands": ["red", "2"]}, metadata
    )

    assert method_options["indexes"] == [1, 2]


def test_unknown_band_still_raises():
    """The fallback does not turn an unknown band name into a silent match."""
    metadata = _asset([{"description": "first"}])

    with pytest.raises(ValueError, match="not found in asset metadata"):
        _reader()._get_options({"name": "data", "bands": ["nope"]}, metadata)
