"""The href-signing seam inside reader.py (docs/adr/0005-asset-href-signing.md S2.6).

`titiler.openeo.signing` is unit-tested on its own in `test_signing.py`. These
tests are about how the reader *gets* its signer: from the key the item was
stamped with at ingest, resolved at construction time -- and -- more importantly
-- that an unstamped item leaves every href exactly as it was before signing
existed.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy
import pystac
import pytest
from pystac import Item
from rasterio.errors import RasterioIOError

from titiler.openeo import reader, signing
from titiler.openeo.reader import (
    SimpleSTACReader,
    _item_has_untrustworthy_proj,
    _resolve_asset_href,
)
from titiler.openeo.signing import get_signer, stamp_signer_key

HREF = "https://example.com/B02.tif"
ALTERNATE = "s3://bucket/B02.tif"

#: Key the tests below stamp items with, registered by the fixture.
TEST_KEY = "test-signer"


def _tag(href: str) -> str:
    """A signer that is obvious in an assertion."""
    return f"{href}?signed=1"


@pytest.fixture(autouse=True)
def register_test_signer():
    """Make `TEST_KEY` resolvable, and keep the memoised resolver clean."""
    signing.SIGNERS[TEST_KEY] = lambda: _tag
    get_signer.cache_clear()
    yield
    signing.SIGNERS.pop(TEST_KEY, None)
    get_signer.cache_clear()


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
    """An unstamped item -- every deployment that needs no signing."""
    with SimpleSTACReader(_item({"B02": {"href": HREF}})) as src:
        assert src.signer is None
        assert src._get_asset_info("B02")["url"] == HREF


def test_reader_signs_a_real_asset_url():
    item = stamp_signer_key(_item({"B02": {"href": HREF}}), TEST_KEY)
    with SimpleSTACReader(item) as src:
        assert src._get_asset_info("B02")["url"] == f"{HREF}?signed=1"


def test_reader_takes_no_signer_argument():
    """The signer is derived from the item, never passed in (issue #377). A
    caller that still tries to hand one over should fail loudly rather than
    have it silently ignored."""
    with pytest.raises(TypeError):
        SimpleSTACReader(_item({"B02": {"href": HREF}}), signer=_tag)


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
    item = stamp_signer_key(
        pystac.Item.from_dict({**raw, "collection": "sentinel-1-grd"}), TEST_KEY
    )

    with SimpleSTACReader(item) as src:
        assert src._derived_bands, "fixture should resolve band-source bands"
        band = next(iter(src._derived_bands))
        info = src._get_derived_asset_info(band)

    assert info["url"].endswith("?signed=1")
    assert info["reader_options"]["sibling_href"].endswith("?signed=1")


# ---------------------------------------------------------------------------
# _reader -- the kwargs boundary and the retry contract (ADR 0005 S3.1)
# ---------------------------------------------------------------------------


class _FakeSrc:
    """Records the kwargs it was built with and the ones `part()` was given."""

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


def test_reader_passes_no_signer_to_the_reader_or_to_part(monkeypatch):
    """`signer` is gone from this boundary entirely: the reader reads it off the
    item, and `part()` never saw it in the first place."""
    built = {}

    def factory(item, **kwargs):
        src = _FakeSrc(item, **kwargs)
        built["src"] = src
        return src

    monkeypatch.setattr(reader, "SimpleSTACReader", factory)

    item = {"id": "x", "properties": {"datetime": "2025-01-01T00:00:00Z"}}
    with pytest.raises(RuntimeError, match="stop here"):
        reader._reader(item, (0, 0, 1, 1), assets=["B02"])

    assert built["src"].kwargs == {}
    assert built["src"].part_kwargs == {"assets": ["B02"]}


def test_every_retry_rebuilds_the_reader_so_an_expired_token_is_reminted(monkeypatch):
    """The regression guard for stamping a *key* rather than a signed href.

    A credential resolved once at ingest would be reused by every retry; because
    the retry loop rebuilds the reader, and construction is what resolves the
    signer, an attempt after a `RasterioIOError` signs afresh (ADR 0005 S3.1).
    """
    constructions = []
    img = SimpleNamespace(
        width=1, height=1, count=1, data=numpy.zeros((1, 1, 1), dtype="uint8")
    )

    class _FlakySrc(_FakeSrc):
        def part(self, bbox, **kwargs):
            constructions.append(self.signer_at_build)
            if len(constructions) < 3:
                raise RasterioIOError("transient")
            return img

    def factory(item, **kwargs):
        src = _FlakySrc(item, **kwargs)
        # What the real reader does in __attrs_post_init__, per construction.
        src.signer_at_build = signing.signer_for_item(item)
        return src

    monkeypatch.setattr(reader, "SimpleSTACReader", factory)
    monkeypatch.setattr(reader.time, "sleep", lambda _: None)

    item = stamp_signer_key(
        {"id": "x", "properties": {"datetime": "2025-01-01T00:00:00Z"}}, TEST_KEY
    )
    assert reader._reader(item, (0, 0, 1, 1)) is img

    # Three attempts, each having resolved the signer for itself.
    assert len(constructions) == 3
    assert all(signer is _tag for signer in constructions)
