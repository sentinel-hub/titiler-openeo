"""The `reader_cls` seam (issue #379, ADR 0011).

A backend that needs another asset reader subclasses `SimpleSTACReader` and
passes the subclass once, to `LoadCollection`/`LoadStac`. These tests pin
down that the class reaches every place a reader is built -- `_reader`, the
dimension estimate, and both `LoadStac` paths -- and that the default is
still looked up at call time (several test modules monkeypatch
`titiler.openeo.reader.SimpleSTACReader`).
"""

from datetime import datetime

import numpy
import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo import reader as reader_module
from titiler.openeo import stacapi as stacapi_module
from titiler.openeo.reader import SimpleSTACReader, _estimate_output_dimensions, _reader
from titiler.openeo.stacapi import LoadCollection, LoadStac, stacApiBackend


def _item_dict(item_id: str = "item", dt: str = "2021-01-01T00:00:00Z") -> dict:
    return {
        "type": "Feature",
        "id": item_id,
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
            "datetime": dt,
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


class RecordingReader(SimpleSTACReader):
    """A backend's reader: counts constructions, reads without I/O."""

    built: list = []

    def __attrs_post_init__(self) -> None:
        """Record which item this reader was built for."""
        type(self).built.append(self.input.id)
        super().__attrs_post_init__()

    def part(self, bbox, **kwargs):
        """Return a blank image of the requested size."""
        return ImageData(
            numpy.ma.zeros(
                (1, kwargs.get("height", 10), kwargs.get("width", 10)), dtype="uint8"
            ),
            assets=kwargs.get("assets", ["B01"]),
            crs=kwargs.get("dst_crs", "EPSG:4326"),
            bounds=tuple(bbox),
        )


@pytest.fixture(autouse=True)
def _reset_recorder():
    RecordingReader.built = []
    yield
    RecordingReader.built = []


BBOX = BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# reader.py
# ---------------------------------------------------------------------------


def test_reader_builds_the_given_class():
    img = _reader(
        _item_dict(), (0, 0, 1, 1), reader_cls=RecordingReader, assets=["B01"]
    )
    assert isinstance(img, ImageData)
    assert RecordingReader.built == ["item"]


def test_reader_cls_is_not_forwarded_to_part():
    """`reader_cls` is consumed by `_reader`; `part()` never sees it."""
    seen = {}

    class Spy(RecordingReader):
        def part(self, bbox, **kwargs):
            seen.update(kwargs)
            return super().part(bbox, **kwargs)

    _reader(_item_dict(), (0, 0, 1, 1), reader_cls=Spy, assets=["B01"])
    assert "reader_cls" not in seen


def test_reader_default_is_looked_up_at_call_time(monkeypatch):
    """A monkeypatched module global must still win when no class is given."""
    monkeypatch.setattr(reader_module, "SimpleSTACReader", RecordingReader)
    _reader(_item_dict(), (0, 0, 1, 1), assets=["B01"])
    assert RecordingReader.built == ["item"]


def test_estimate_builds_one_reader_per_item_with_the_given_class():
    items = [
        Item.from_dict(_item_dict("a")),
        Item.from_dict(_item_dict("b", "2021-01-02T00:00:00Z")),
    ]
    dims = _estimate_output_dimensions(
        items, BBOX, ["B01"], reader_cls=RecordingReader, check_max_pixels=False
    )
    assert sorted(RecordingReader.built) == ["a", "b"]
    assert dims["width"] == 100 and dims["height"] == 100
    assert dims["output_bbox"] == dims["bbox"] == [0, 0, 1, 1]


# ---------------------------------------------------------------------------
# stacapi.py
# ---------------------------------------------------------------------------


def _loader(monkeypatch, reader_cls=None) -> LoadCollection:
    items = [Item.from_dict(_item_dict())]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)
    return LoadCollection(
        stac_api=stacApiBackend(url="https://example.com"), reader_cls=reader_cls
    )


def test_load_collection_reads_with_its_reader_cls_without_patching(monkeypatch):
    """The whole path -- estimate and read -- uses the backend's class."""
    loader = _loader(monkeypatch, reader_cls=RecordingReader)
    stack = loader.load_collection(id="c", spatial_extent=BBOX, bands=["B01"])
    img = next(iter(stack.values()))
    assert isinstance(img, ImageData)
    # Once for the estimate, once for the read.
    assert RecordingReader.built == ["item", "item"]


def test_load_collection_forwards_reader_cls_to_mosaic_reader(monkeypatch):
    captured = {}

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        captured.update(kwargs)
        return ImageData(numpy.ma.zeros((1, 5, 5), dtype="uint8")), None

    monkeypatch.setattr(stacapi_module, "mosaic_reader", mock_mosaic_reader)
    loader = _loader(monkeypatch, reader_cls=RecordingReader)
    stack = loader.load_collection(id="c", spatial_extent=BBOX, bands=["B01"])
    next(iter(stack.values()))
    assert captured["reader_cls"] is RecordingReader


def test_load_collection_default_reader_cls_is_none(monkeypatch):
    loader = _loader(monkeypatch)
    assert loader.reader_cls is None


def test_load_stac_forwards_reader_cls_to_load_collection(monkeypatch):
    import pystac

    built = {}

    class FakeLoadCollection:
        def __init__(self, **kwargs):
            built.update(kwargs)

        def load_collection(self, **kwargs):
            return "stack"

    collection = pystac.Collection(
        id="c",
        description="d",
        extent=pystac.Extent(
            pystac.SpatialExtent([[0, 0, 1, 1]]),
            pystac.TemporalExtent([[None, None]]),
        ),
    )
    collection.set_self_href("https://example.com/c.json")
    collection.set_root(collection)
    monkeypatch.setattr(LoadStac, "_load_stac_object", lambda self, url: collection)
    monkeypatch.setattr(stacapi_module, "LoadCollection", FakeLoadCollection)

    assert (
        LoadStac(reader_cls=RecordingReader).load_stac(url="https://example.com/c.json")
        == "stack"
    )
    assert built["reader_cls"] is RecordingReader


def test_load_stac_single_item_reads_with_its_reader_cls(monkeypatch):
    item = Item.from_dict(_item_dict())
    monkeypatch.setattr(LoadStac, "_load_stac_object", lambda self, url: item)

    stack = LoadStac(reader_cls=RecordingReader).load_stac(
        url="https://example.com/item.json", spatial_extent=BBOX, width=10, height=10
    )
    assert isinstance(stack.first, ImageData)
    assert RecordingReader.built == ["item"]


# ---------------------------------------------------------------------------
# The two other things a backend used to copy `load_collection` for
# ---------------------------------------------------------------------------


class PipeLoadCollection(LoadCollection):
    """A backend whose band notation is ``asset|band`` (titiler-eopf-like)."""

    def _bands_to_assets(self, bands):
        """Parse ``asset|band`` into a rio-tiler asset dict."""
        return [
            {"name": b.split("|")[0], "bands": [b.split("|")[1]]} for b in bands or []
        ]


def test_bands_to_assets_override_reaches_mosaic_reader_only(monkeypatch):
    """The override's result is what is read; `bands` still labels the output."""
    captured = {}

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        captured.update(kwargs)
        return ImageData(numpy.ma.zeros((1, 5, 5), dtype="uint8")), None

    monkeypatch.setattr(stacapi_module, "mosaic_reader", mock_mosaic_reader)
    items = [Item.from_dict(_item_dict())]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    loader = PipeLoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    stack = loader.load_collection(
        id="c", spatial_extent=BBOX, bands=["B01|b1"], width=5, height=5
    )
    next(iter(stack.values()))

    assert captured["assets"] == [{"name": "B01", "bands": ["b1"]}]
    assert stack.band_names == ["B01|b1"]


@pytest.mark.parametrize("empty_day", [1, 2])
def test_empty_date_group_is_skipped_not_fatal(monkeypatch, empty_day):
    """A date whose items yield no pixels drops out of the stack with a
    warning, and its label goes with it: an empty first or middle date never
    lends its label to the next date's pixels in a GeoTIFF export."""
    from rio_tiler.errors import EmptyMosaicError

    from titiler.openeo.processes.implementations.formats import _handle_raster_geotiff

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        day = int(items[0].id)
        if day == empty_day:
            raise EmptyMosaicError("nothing here")
        # Each date's pixels carry its day, to check the labels.
        return ImageData(numpy.ma.ones((1, 5, 5), dtype="uint8") * day), None

    monkeypatch.setattr(stacapi_module, "mosaic_reader", mock_mosaic_reader)
    items = [
        Item.from_dict(_item_dict(str(day), f"2021-01-0{day}T00:00:00Z"))
        for day in (1, 2, 3)
    ]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    loader = LoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    stack = loader.load_collection(
        id="c", spatial_extent=BBOX, bands=["B01"], width=5, height=5
    )

    # Keys are known up front (lazy stack); the skipped date leaves on read.
    assert len(stack.keys()) == 3
    img = _handle_raster_geotiff(stack)
    kept = [day for day in (1, 2, 3) if day != empty_day]

    assert len(stack) == len(stack.keys()) == len(kept)
    assert [k.day for k in stack.keys()] == kept
    assert img.metadata["original_keys"] == [f"2021-01-0{day} 00:00:00" for day in kept]
    for band, day in enumerate(kept):
        assert img.band_descriptions[band] == f"2021-01-0{day} 00:00:00"
        assert int(img.array[band, 0, 0]) == day
    with pytest.raises(KeyError):
        stack[datetime(2021, 1, empty_day)]
    assert int(stack.first.array[0, 0, 0]) == kept[0]


def test_each_task_run_gets_a_new_pixel_selector(monkeypatch):
    """The selector holds the mosaic it builds: one kept in the task closure
    would keep a released slice alive and start a re-run with old pixels."""
    selectors = []

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        selectors.append(kwargs["pixel_selection"])
        return ImageData(numpy.ma.zeros((1, 5, 5), dtype="uint8")), None

    monkeypatch.setattr(stacapi_module, "mosaic_reader", mock_mosaic_reader)
    items = [Item.from_dict(_item_dict())]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    loader = LoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    stack = loader.load_collection(
        id="c", spatial_extent=BBOX, bands=["B01"], width=5, height=5
    )
    (key,) = stack.keys()
    stack[key]
    stack.release(key)
    stack[key]

    assert len(selectors) == 2
    assert selectors[0] is not selectors[1]


def test_hook_selection_drives_the_dimension_estimate(monkeypatch):
    """Without explicit dimensions, a parsed band selection gets the native
    grid of the asset it reads (100x100 here), not the 1024x1024 fallback."""
    captured = {}

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        captured.update(kwargs)
        return ImageData(numpy.ma.zeros((1, 5, 5), dtype="uint8")), None

    monkeypatch.setattr(stacapi_module, "mosaic_reader", mock_mosaic_reader)
    items = [Item.from_dict(_item_dict())]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    loader = PipeLoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    stack = loader.load_collection(
        id="c", spatial_extent=BBOX, bands=["B01|b1"], width=None, height=None
    )
    next(iter(stack.values()))

    assert (captured["width"], captured["height"]) == (100, 100)


def test_hook_selection_counts_for_the_pixel_limit(monkeypatch):
    """Two output bands read from one asset count as two bands."""
    from titiler.openeo.errors import OutputLimitExceeded

    items = [Item.from_dict(_item_dict())]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)
    # Above one band (10x10x1x1), below two.
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_PIXELS", "150")

    loader = PipeLoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    with pytest.raises(OutputLimitExceeded):
        loader.load_collection(
            id="c",
            spatial_extent=BBOX,
            bands=["B01|b1", "B01|b2"],
            width=10,
            height=10,
        )


def test_reader_accepts_asset_dicts_from_bands_to_assets(monkeypatch):
    """The post-read steps key on asset names, so rio-tiler asset dicts
    (what a backend's `_bands_to_assets` produces) must work end to end:
    here the asset-level `raster:scale`/`raster:offset` of `B01` is applied."""
    monkeypatch.setattr(reader_module.processing_settings, "apply_scale_offset", True)
    monkeypatch.setattr(
        reader_module.processing_settings, "scale_offset_sources", ["stac"]
    )
    item = _item_dict()
    item["assets"]["B01"].update({"raster:scale": 0.5, "raster:offset": 1.0})

    img = _reader(
        item,
        (0, 0, 1, 1),
        reader_cls=RecordingReader,
        assets=[{"name": "B01", "bands": ["b1"]}],
    )
    assert img.array.dtype == "float32"
    assert float(img.array[0, 0, 0]) == 1.0
