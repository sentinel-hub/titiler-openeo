"""Tests for the mask process."""

from datetime import datetime

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.spatial import IncompatibleDataCubes, mask

EPSG_4326 = CRS.from_epsg(4326)
BOUNDS = (0.0, 0.0, 4.0, 4.0)


def _img(array, band_names=None):
    """Build an ImageData from a numpy/masked array."""
    if not isinstance(array, np.ma.MaskedArray):
        array = np.ma.MaskedArray(array, mask=np.zeros_like(array, dtype=bool))
    count = array.shape[0]
    return ImageData(
        array,
        crs=EPSG_4326,
        bounds=BOUNDS,
        band_descriptions=band_names or [f"b{i}" for i in range(count)],
    )


def _stack(images):
    """Build a RasterStack from a dict of datetime -> ImageData."""
    return RasterStack.from_images(images)


class TestMaskBasic:
    """Core masking behaviour."""

    def test_replace_with_nodata(self):
        """Non-zero mask pixels are masked out when replacement is None."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 5.0)})
        m = _stack(
            {datetime(2020, 1, 1): _img(np.array([[[0, 1], [0, 0]]], dtype="uint8"))}
        )
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        # Pixel (0,1) is masked, others keep their value.
        assert out.mask[0, 0, 1]
        assert not out.mask[0, 0, 0]
        np.testing.assert_array_equal(out.data[0, 0, 0], 5.0)

    def test_replace_with_value(self):
        """Non-zero mask pixels are replaced by the given value."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 5.0)})
        m = _stack(
            {datetime(2020, 1, 1): _img(np.array([[[1, 0], [0, 1]]], dtype="uint8"))}
        )
        result = mask(data, m, replacement=-1)
        out = result[datetime(2020, 1, 1)].array
        np.testing.assert_array_equal(out.data[0], [[-1, 5], [5, -1]])
        assert not out.mask.any()

    def test_zero_mask_leaves_data_unchanged(self):
        """An all-zero mask replaces nothing."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 7.0)})
        m = _stack({datetime(2020, 1, 1): _img(np.zeros((1, 2, 2), dtype="uint8"))})
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        assert not out.mask.any()
        np.testing.assert_array_equal(out.data[0], 7.0)

    def test_boolean_mask(self):
        """Boolean True pixels are masked."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 3.0)})
        m = _stack(
            {datetime(2020, 1, 1): _img(np.array([[[True, False], [False, True]]]))}
        )
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        np.testing.assert_array_equal(out.mask[0], [[True, False], [False, True]])

    def test_masked_mask_pixels_are_inactive(self):
        """No-data pixels in the mask do not trigger replacement."""
        marr = np.ma.MaskedArray(
            np.array([[[1, 1], [0, 0]]], dtype="uint8"),
            mask=np.array([[[True, False], [False, False]]]),
        )
        data = _stack({datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 5.0)})
        m = _stack({datetime(2020, 1, 1): _img(marr)})
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        # (0,0) mask value is non-zero but no-data -> not masked.
        assert not out.mask[0, 0, 0]
        # (0,1) is a valid non-zero -> masked.
        assert out.mask[0, 0, 1]


class TestMaskBroadcasting:
    """Band and temporal broadcasting."""

    def test_single_band_mask_applied_to_all_bands(self):
        """A single-band mask is broadcast across all data bands."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((3, 2, 2)) * 5.0)})
        m = _stack(
            {datetime(2020, 1, 1): _img(np.array([[[1, 0], [0, 0]]], dtype="uint8"))}
        )
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        # Pixel (0,0) masked for every band.
        assert out.mask[:, 0, 0].all()
        assert not out.mask[:, 0, 1].any()

    def test_per_band_mask(self):
        """A multi-band mask masks each band independently."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((2, 2, 2)) * 5.0)})
        mask_arr = np.array([[[1, 0], [0, 0]], [[0, 0], [0, 1]]], dtype="uint8")
        m = _stack({datetime(2020, 1, 1): _img(mask_arr)})
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        assert out.mask[0, 0, 0]
        assert not out.mask[1, 0, 0]
        assert out.mask[1, 1, 1]
        assert not out.mask[0, 1, 1]

    def test_single_mask_broadcast_over_time(self):
        """A single-timestamp mask is broadcast across all data timestamps."""
        data = _stack(
            {
                datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 1.0),
                datetime(2020, 2, 1): _img(np.ones((1, 2, 2)) * 2.0),
            }
        )
        m = _stack(
            {datetime(2030, 1, 1): _img(np.array([[[1, 0], [0, 0]]], dtype="uint8"))}
        )
        result = mask(data, m)
        for key in data.keys():
            assert result[key].array.mask[0, 0, 0]

    def test_per_timestamp_mask(self):
        """Matching timestamps mask each label independently."""
        data = _stack(
            {
                datetime(2020, 1, 1): _img(np.ones((1, 2, 2)) * 1.0),
                datetime(2020, 2, 1): _img(np.ones((1, 2, 2)) * 2.0),
            }
        )
        m = _stack(
            {
                datetime(2020, 1, 1): _img(np.array([[[1, 0], [0, 0]]], dtype="uint8")),
                datetime(2020, 2, 1): _img(np.array([[[0, 0], [0, 1]]], dtype="uint8")),
            }
        )
        result = mask(data, m)
        assert result[datetime(2020, 1, 1)].array.mask[0, 0, 0]
        assert result[datetime(2020, 2, 1)].array.mask[0, 1, 1]


class TestMaskAlignment:
    """Spatial resampling of the mask."""

    def test_mask_resized_to_data_grid(self):
        """A coarser mask is resampled to the data resolution."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((1, 4, 4)) * 5.0)})
        # 2x2 mask, top-left quadrant active.
        m = _stack(
            {datetime(2020, 1, 1): _img(np.array([[[1, 0], [0, 0]]], dtype="uint8"))}
        )
        result = mask(data, m)
        out = result[datetime(2020, 1, 1)].array
        # Top-left 2x2 block masked after nearest-neighbour upsampling.
        assert out.mask[0, :2, :2].all()
        assert not out.mask[0, 2:, 2:].any()


class TestMaskValidation:
    """Error and edge cases."""

    def test_incompatible_band_count(self):
        """A mask with an incompatible band count raises."""
        data = _stack({datetime(2020, 1, 1): _img(np.ones((3, 2, 2)))})
        m = _stack({datetime(2020, 1, 1): _img(np.ones((2, 2, 2), dtype="uint8"))})
        with pytest.raises(IncompatibleDataCubes):
            mask(data, m)

    def test_missing_temporal_label(self):
        """A multi-label mask missing a data label raises."""
        data = _stack(
            {
                datetime(2020, 1, 1): _img(np.ones((1, 2, 2))),
                datetime(2020, 2, 1): _img(np.ones((1, 2, 2))),
            }
        )
        m = _stack(
            {
                datetime(2020, 1, 1): _img(np.ones((1, 2, 2), dtype="uint8")),
                datetime(2099, 1, 1): _img(np.ones((1, 2, 2), dtype="uint8")),
            }
        )
        with pytest.raises(IncompatibleDataCubes):
            mask(data, m)

    def test_empty_data_returned_as_is(self):
        """Masking empty data returns it unchanged."""
        empty = RasterStack(tasks=[], timestamp_fn=lambda asset: asset["datetime"])
        m = _stack({datetime(2020, 1, 1): _img(np.ones((1, 2, 2), dtype="uint8"))})
        assert len(mask(empty, m)) == 0

    def test_preserves_existing_nodata(self):
        """Existing no-data pixels in data stay masked."""
        arr = np.ma.MaskedArray(
            np.ones((1, 2, 2)) * 5.0,
            mask=np.array([[[True, False], [False, False]]]),
        )
        data = _stack({datetime(2020, 1, 1): _img(arr)})
        m = _stack({datetime(2020, 1, 1): _img(np.zeros((1, 2, 2), dtype="uint8"))})
        result = mask(data, m)
        assert result[datetime(2020, 1, 1)].array.mask[0, 0, 0]
