"""The href-signing seam inside reader.py (docs/adr/0005-asset-href-signing.md S2.6).

`titiler.openeo.signing` is unit-tested on its own in `test_signing.py`. These
tests are about the *threading*: that a signer handed to `SimpleSTACReader`
reaches every href the reader opens, and -- more importantly -- that `None`
leaves every one of those hrefs exactly as it was before signing existed.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pystac
import pytest
from pystac import Item

from titiler.openeo import reader
from titiler.openeo.reader import (
    SimpleSTACReader,
    _item_has_untrustworthy_proj,
    _resolve_asset_href,
)

HREF = "https://example.com/B02.tif"
ALTERNATE = "s3://bucket/B02.tif"


def _tag(href: str) -> str:
    """A signer that is obvious in an assertion."""
    return f"{href}?signed=1"


def _item(assets: dict, properties: dict | None = None) -> Item:
    return Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "item",
            "bbox": [0, 40, 6, 46],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 40], [6, 40], [6, 46], [0, 46], [0, 40]]],
            },
            "properties": {"datetime": "2025-01-01T00:00:00Z", **(properties or {})},
            "assets": assets,
        }
    )


# ---------------------------------------------------------------------------
# _resolve_asset_href
# ---------------------------------------------------------------------------


def test_resolve_asset_href_is_unchanged_without_a_signer():
    asset = pystac.Asset(href=HREF)
    assert _resolve_asset_href(asset) == HREF
    assert _resolve_asset_href(asset, None) == HREF


def test_resolve_asset_href_applies_the_signer():
    asset = pystac.Asset(href=HREF)
    assert _resolve_asset_href(asset, _tag) == f"{HREF}?signed=1"


def test_signer_runs_after_the_alternate_is_chosen():
    """The credential must land on the href actually opened, not the one the
    alternate replaced."""
    asset = pystac.Asset(
        href=HREF,
        extra_fields={"alternate": {"s3": {"href": ALTERNATE}}},
    )

    with patch("titiler.openeo.reader.STAC_ALTERNATE_KEY", "s3"):
        assert _resolve_asset_href(asset, _tag) == f"{ALTERNATE}?signed=1"


# ---------------------------------------------------------------------------
# _item_has_untrustworthy_proj -- ADR 0005 S1.1
# ---------------------------------------------------------------------------


def test_untrustworthy_proj_check_signs_the_href_it_opens():
    item = _item(
        {"vv": {"href": HREF}},
        {"sar:instrument_mode": "IW"},
    )

    with patch.object(reader, "_is_asset_gcp_referenced", return_value=False) as probe:
        _item_has_untrustworthy_proj(item, ["vv"], _tag)

    probe.assert_called_once_with(f"{HREF}?signed=1")


def test_untrustworthy_proj_check_honours_the_alternate_key():
    """Regression: this path used the raw href and so could open a different
    variant than the one whose pixels are read (ADR 0005 S1.1)."""
    item = _item(
        {"vv": {"href": HREF, "alternate": {"s3": {"href": ALTERNATE}}}},
        {"sar:instrument_mode": "IW"},
    )

    with patch("titiler.openeo.reader.STAC_ALTERNATE_KEY", "s3"):
        with patch.object(
            reader, "_is_asset_gcp_referenced", return_value=False
        ) as probe:
            _item_has_untrustworthy_proj(item, ["vv"])

    probe.assert_called_once_with(ALTERNATE)


def test_untrustworthy_proj_check_still_skips_non_sar_items():
    """The `_item_looks_like_sar` gate must keep ordinary items free of I/O."""
    item = _item({"B02": {"href": HREF}})

    with patch.object(reader, "_is_asset_gcp_referenced") as probe:
        assert _item_has_untrustworthy_proj(item, ["B02"], _tag) is False

    probe.assert_not_called()


# ---------------------------------------------------------------------------
# SimpleSTACReader
# ---------------------------------------------------------------------------


def test_reader_defaults_to_no_signer():
    with SimpleSTACReader(_item({"B02": {"href": HREF}})) as src:
        assert src.signer is None
        assert src._get_asset_info("B02")["url"] == HREF


def test_reader_signs_a_real_asset_url():
    with SimpleSTACReader(_item({"B02": {"href": HREF}}), signer=_tag) as src:
        assert src._get_asset_info("B02")["url"] == f"{HREF}?signed=1"


def test_reader_signs_derived_band_and_sibling_hrefs():
    """A band-source band reads an annotation asset *and* its sibling; both
    hrefs must carry the credential (ADR 0005 S2.6).

    Uses the real Planetary Computer fixture for its genuine blob hrefs. The
    fixture carries no `collection`, which the band-source registry matches on,
    so it is supplied here -- the same id the live item has.
    """
    raw = json.loads(
        (Path("tests/fixtures/sar/items/planetary_computer.json")).read_text()
    )
    item = pystac.Item.from_dict({**raw, "collection": "sentinel-1-grd"})

    with SimpleSTACReader(item, signer=_tag) as src:
        assert src._derived_bands, "fixture should resolve band-source bands"
        band = next(iter(src._derived_bands))
        info = src._get_derived_asset_info(band)

    assert info["url"].endswith("?signed=1")
    assert info["reader_options"]["sibling_href"].endswith("?signed=1")


# ---------------------------------------------------------------------------
# _reader -- the kwargs boundary
# ---------------------------------------------------------------------------


class _FakeSrc:
    """Records the kwargs `part()` was given."""

    def __init__(self, item, **kwargs):
        self.kwargs = kwargs
        self.part_kwargs = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def part(self, bbox, **kwargs):
        self.part_kwargs = kwargs
        raise RuntimeError("stop here")


@pytest.mark.parametrize("signer", [None, _tag])
def test_reader_consumes_signer_and_does_not_forward_it_to_part(monkeypatch, signer):
    built = {}

    def factory(item, **kwargs):
        src = _FakeSrc(item, **kwargs)
        built["src"] = src
        return src

    monkeypatch.setattr(reader, "SimpleSTACReader", factory)

    item = {"id": "x", "properties": {"datetime": "2025-01-01T00:00:00Z"}}
    with pytest.raises(RuntimeError, match="stop here"):
        reader._reader(item, (0, 0, 1, 1), assets=["B02"], signer=signer)

    assert built["src"].kwargs == {"signer": signer}
    assert built["src"].part_kwargs == {"assets": ["B02"]}
