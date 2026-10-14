"""The band labels of a stack stay the load_collection band names (issue #458).

A reader returns images whose band descriptions are rio-tiler's
``<asset>_b<index>``. A process that rebuilds a stack from its images (for
example ``resample_spatial``) reads the labels back from these descriptions, so
the stack must stamp its own band names on every image it produces.
"""

from datetime import datetime

import numpy as np
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.filter import filter_bands
from titiler.openeo.processes.implementations.spatial import resample_spatial

KEY = datetime(2023, 1, 20)
BOUNDS = (0.0, 0.0, 0.01, 0.01)


def _reader_image():
    """An image as the reader returns it: rio-tiler band descriptions."""
    return ImageData(
        np.ma.array(np.stack([np.full((10, 10), 1.0), np.full((10, 10), 2.0)])),
        bounds=BOUNDS,
        crs=CRS.from_epsg(4326),
        band_descriptions=["B03_b1", "B04_b1"],
    )


def _loaded_stack():
    return RasterStack(
        tasks=[(_reader_image, {"datetime": KEY})],
        timestamp_fn=lambda asset: asset["datetime"],
        width=10,
        height=10,
        bounds=BOUNDS,
        dst_crs=CRS.from_epsg(4326),
        band_names=["B03", "B04"],
    )


def test_images_carry_stack_band_names():
    stack = _loaded_stack()
    assert stack[KEY].band_descriptions == ["B03", "B04"]
    ref = _loaded_stack().get_image_ref(KEY)
    assert ref.realize().band_descriptions == ["B03", "B04"]


def test_mismatched_count_keeps_image_descriptions():
    stack = RasterStack(
        tasks=[(_reader_image, {"datetime": KEY})],
        timestamp_fn=lambda asset: asset["datetime"],
        band_names=["B03"],
    )
    assert stack[KEY].band_descriptions == ["B03_b1", "B04_b1"]


def test_from_images_band_names_override_labels_images():
    img = _reader_image()
    stack = RasterStack.from_images({KEY: img}, band_names=["B03", "B04"])
    assert stack[KEY].band_descriptions == ["B03", "B04"]
    # The source image is shared, so it is not changed in place.
    assert img.band_descriptions == ["B03_b1", "B04_b1"]


def test_filter_bands_after_resample_spatial():
    resampled = resample_spatial(_loaded_stack(), resolution=0.002)
    assert resampled.band_names == ["B03", "B04"]
    filtered = filter_bands(resampled, bands=["B04"])
    np.testing.assert_array_equal(filtered[KEY].array[0], 2.0)
    assert filtered[KEY].band_descriptions == ["B04"]
