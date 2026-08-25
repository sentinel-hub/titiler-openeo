"""The signing decision's journey from configuration to the read path.

`test_signing.py` covers the signer itself and `test_reader_signing.py` covers
`reader.py`'s end of the seam. This file covers the middle: that ingest stamps
the deployment's signer key onto every item, and that the stamp -- not a
threaded parameter -- is what survives into the lazily-evaluated mosaic task,
which runs on a worker thread no request context reaches
(docs/adr/0005-asset-href-signing.md S2.2).
"""

from unittest.mock import patch

import pystac
import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo.models.auth import User
from titiler.openeo.signing import ITEM_SIGNER_KEY
from titiler.openeo.stacapi import LoadCollection, LoadStac, stacApiBackend

PC_STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_KEY = "planetary-computer"

ITEM = {
    "type": "Feature",
    "id": "test-item",
    "stac_version": "1.0.0",
    "stac_extensions": [
        "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
    ],
    "bbox": [0, 0, 1, 1],
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    },
    "properties": {
        "datetime": "2021-01-01T00:00:00Z",
        "proj:crs": "EPSG:4326",
        "proj:transform": [0.01, 0.0, 0.0, 0.0, -0.01, 0.0],
    },
    "assets": {
        "B01": {
            "href": "https://example.com/B01.tif",
            "type": "image/tiff; application=geotiff",
        }
    },
    "links": [],
}


# ---------------------------------------------------------------------------
# Ingest stamps the items
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("signer_key", [PC_KEY, None], ids=["configured", "unset"])
def test_get_items_stamps_every_item_with_the_configured_key(monkeypatch, signer_key):
    """`_get_items` is the single funnel every `load_collection` read passes
    through, so the deployment's decision is applied there and nowhere below."""
    monkeypatch.setattr(
        stacApiBackend, "get_items", lambda *a, **k: [Item.from_dict(ITEM)]
    )
    loader = LoadCollection(
        stac_api=stacApiBackend(url=PC_STAC_API), signer_key=signer_key
    )

    items = loader._get_items("test")

    assert len(items) == 1
    if signer_key:
        assert items[0].properties[ITEM_SIGNER_KEY] == signer_key
    else:
        assert ITEM_SIGNER_KEY not in items[0].properties


def test_an_unstamped_item_is_indistinguishable_from_before_signing_existed(
    monkeypatch,
):
    monkeypatch.setattr(
        stacApiBackend, "get_items", lambda *a, **k: [Item.from_dict(ITEM)]
    )
    loader = LoadCollection(stac_api=stacApiBackend(url="https://example.com"))

    assert loader._get_items("test")[0].to_dict()["properties"] == ITEM["properties"]


# ---------------------------------------------------------------------------
# The lazy task boundary
# ---------------------------------------------------------------------------


def _mock_image(*args, **kwargs):
    import numpy

    return (
        ImageData(numpy.zeros((1, 4, 4), dtype="uint8"), assets=["B01"]),
        [],
    )


@pytest.mark.parametrize("signer_key", [PC_KEY, None], ids=["configured", "unset"])
def test_the_stamp_reaches_the_mosaic_task(monkeypatch, signer_key):
    """The task runs lazily on a worker thread, so what it needs must travel on
    the items themselves -- and `signer` must be gone from the kwargs, or a
    credential resolved at ingest would be reused past its expiry."""
    monkeypatch.setattr(
        stacApiBackend, "get_items", lambda *a, **k: [Item.from_dict(ITEM)]
    )
    loader = LoadCollection(
        stac_api=stacApiBackend(url=PC_STAC_API), signer_key=signer_key
    )

    with patch(
        "titiler.openeo.stacapi.mosaic_reader", side_effect=_mock_image
    ) as mosaic:
        stack = loader.load_collection(
            id="test",
            spatial_extent=BoundingBox(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            bands=["B01"],
            width=4,
            height=4,
            named_parameters={"_openeo_user": User(user_id="alice")},
        )
        # RasterStack is lazy; force the task to run.
        dict(stack)

    assert "signer" not in mosaic.call_args.kwargs

    (mosaic_items,) = (mosaic.call_args.args[0],)
    if signer_key:
        assert all(i.properties[ITEM_SIGNER_KEY] == signer_key for i in mosaic_items)
    else:
        assert all(ITEM_SIGNER_KEY not in i.properties for i in mosaic_items)


# ---------------------------------------------------------------------------
# load_stac
# ---------------------------------------------------------------------------


def test_load_stac_accepts_named_parameters():
    """The process registry passes it positionally by name, so the signature
    must carry it."""
    import inspect

    assert "named_parameters" in inspect.signature(LoadStac().load_stac).parameters


def test_load_stac_stamps_a_single_item(monkeypatch):
    """This path never passes through `LoadCollection._get_items`, so it does
    its own stamping -- otherwise a single-item URL would silently read
    unsigned."""
    loader = LoadStac(signer_key=PC_KEY)
    monkeypatch.setattr(
        LoadStac, "_load_stac_object", lambda self, url: Item.from_dict(ITEM)
    )

    captured = {}

    def _capture(self, items, *a, **k):
        captured["items"] = items
        return "stack"

    monkeypatch.setattr(LoadStac, "_process_spatial_extent", _capture)

    loader.load_stac(
        url="https://example.com/item.json",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
    )

    assert captured["items"][0]["properties"][ITEM_SIGNER_KEY] == PC_KEY


def test_load_stac_passes_its_signer_to_the_delegate():
    """A Collection/Catalog URL hands off to LoadCollection -- which must not
    silently lose the credential path."""
    loader = LoadStac(signer_key=PC_KEY)

    captured = {}

    class _Delegate:
        def __init__(self, stac_api, signer_key=None):
            captured["signer_key"] = signer_key

        def load_collection(self, **kwargs):
            captured["named_parameters"] = kwargs.get("named_parameters")
            return "stack"

    collection = pystac.Collection(
        id="c",
        description="d",
        extent=pystac.Extent(
            pystac.SpatialExtent([[0, 0, 1, 1]]),
            pystac.TemporalExtent([[None, None]]),
        ),
    )
    collection.set_root(collection)
    collection.set_self_href("https://example.com/collection.json")

    with patch("titiler.openeo.stacapi.LoadCollection", _Delegate):
        user = {"_openeo_user": User(user_id="alice")}
        assert (
            loader._handle_collection_or_catalog(collection, named_parameters=user)
            == "stack"
        )

    assert captured["signer_key"] == PC_KEY
    assert captured["named_parameters"] == user
