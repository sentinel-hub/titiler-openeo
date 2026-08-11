"""The signer's journey from the authenticated request to the read path.

`test_signing.py` covers the signer itself and `test_reader_signing.py` covers
`reader.py`'s end of the seam. This file covers the middle: that
`load_collection`/`load_stac` build a signer from the `_openeo_user` named
parameter, and that it survives into the lazily-evaluated mosaic task, which
runs on a worker thread no request context reaches
(docs/adr/0005-asset-href-signing.md S2.5).
"""

import re
from unittest.mock import patch

import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo.models.auth import User
from titiler.openeo.signing import SignerRule, rules_for_catalogue
from titiler.openeo.stacapi import LoadCollection, LoadStac, stacApiBackend

PC_STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"

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
# _signer_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loader_cls", [LoadCollection, LoadStac])
def test_no_rules_means_no_signer(loader_cls):
    loader = (
        loader_cls(stac_api=stacApiBackend(url="https://example.com"))
        if loader_cls is LoadCollection
        else loader_cls()
    )
    assert loader._signer_for({"_openeo_user": User(user_id="alice")}) is None
    assert loader._signer_for(None) is None


def test_signer_is_built_from_the_authenticated_user():
    """The user reaches the factory. Uses a synthetic rule rather than the
    shipped one -- `SignerRule.factory` is a direct reference captured at import,
    so the registry is passed in, exactly as `derive_bands` takes its own."""
    seen = []

    rule = SignerRule(
        host=re.compile(r"example\.com$"),
        factory=lambda user: (seen.append(user), lambda href: href)[1],
    )
    loader = LoadCollection(
        stac_api=stacApiBackend(url="https://example.com"), signer_rules=(rule,)
    )

    user = User(user_id="alice")
    assert loader._signer_for({"_openeo_user": user}) is not None

    assert seen == [user]


def test_signer_is_still_built_for_an_unauthenticated_request():
    """Public Planetary Computer needs no user; a missing one is not an error."""
    loader = LoadCollection(
        stac_api=stacApiBackend(url=PC_STAC_API),
        signer_rules=rules_for_catalogue(PC_STAC_API),
    )
    assert loader._signer_for(None) is not None
    assert loader._signer_for({}) is not None


# ---------------------------------------------------------------------------
# The lazy task boundary
# ---------------------------------------------------------------------------


def _mock_image(*args, **kwargs):
    import numpy

    return (
        ImageData(numpy.zeros((1, 4, 4), dtype="uint8"), assets=["B01"]),
        [],
    )


@pytest.mark.parametrize("with_rules", [True, False])
def test_signer_reaches_the_mosaic_task(monkeypatch, with_rules):
    """The task runs lazily on a worker thread, so the signer must be captured
    in its closure -- not looked up when it runs."""
    monkeypatch.setattr(
        LoadCollection, "_get_items", lambda *a, **k: [Item.from_dict(ITEM)]
    )

    rules = rules_for_catalogue(PC_STAC_API) if with_rules else ()
    loader = LoadCollection(
        stac_api=stacApiBackend(url=PC_STAC_API), signer_rules=rules
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

    signer = mosaic.call_args.kwargs["signer"]
    if with_rules:
        assert callable(signer)
    else:
        assert signer is None


def test_load_stac_accepts_named_parameters():
    """`load_stac` had no `named_parameters` before this seam; the process
    registry passes it positionally by name, so the signature must carry it."""
    import inspect

    assert "named_parameters" in inspect.signature(LoadStac().load_stac).parameters


def test_load_stac_passes_its_rules_to_the_delegate():
    """A Collection/Catalog URL hands off to LoadCollection -- which must not
    silently lose the credential path."""
    rules = rules_for_catalogue(PC_STAC_API)
    loader = LoadStac(signer_rules=rules)

    captured = {}

    class _Delegate:
        def __init__(self, stac_api, signer_rules=()):
            captured["rules"] = signer_rules

        def load_collection(self, **kwargs):
            captured["named_parameters"] = kwargs.get("named_parameters")
            return "stack"

    import pystac

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

    assert captured["rules"] == rules
    assert captured["named_parameters"] == user
