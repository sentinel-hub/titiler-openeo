"""Tests for pixel selection reducers in math.py and reduce.py."""

import functools

import numpy as np
import pytest
from openeo_pg_parser_networkx import OpenEOProcessGraph
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.core import (
    extract_process_graph_process_id,
)
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.math import (
    count,
    firstpixel,
    highestpixel,
    lastbandhight,
    lastbandlow,
    lowestpixel,
    stdev,
)
from titiler.openeo.processes.implementations.reduce import (
    PIXEL_SELECTION_REDUCERS,
    _get_pixel_selection_method_name,
    apply_pixel_selection,
)


class TestExtractProcessGraphProcessId:
    """Tests for extract_process_graph_process_id helper function."""

    def test_extract_from_direct_openeo_process_graph(self):
        """Test extraction when callable is directly an OpenEOProcessGraph."""
        # Create a single-node process graph for 'first'
        pg_data = {
            "process_graph": {
                "first1": {
                    "process_id": "first",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        # The OpenEOProcessGraph itself is callable
        result = extract_process_graph_process_id(pg)
        assert result == "first"

    def test_extract_from_partial_with_graph_in_args(self):
        """Test extraction from functools.partial with graph in positional args."""
        pg_data = {
            "process_graph": {
                "first1": {
                    "process_id": "first",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        # Create a partial with the graph as first positional arg
        def wrapper(graph, data):
            return graph(data)

        partial_func = functools.partial(wrapper, pg)
        result = extract_process_graph_process_id(partial_func)
        assert result == "first"

    def test_extract_from_partial_with_graph_in_kwargs(self):
        """Test extraction from functools.partial with graph in keyword args."""
        pg_data = {
            "process_graph": {
                "median1": {
                    "process_id": "median",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        # Create a partial with the graph as keyword arg
        def wrapper(data, graph=None):
            return graph(data) if graph else data

        partial_func = functools.partial(wrapper, graph=pg)
        result = extract_process_graph_process_id(partial_func)
        assert result == "median"

    def test_extract_from_multi_node_graph_returns_none(self):
        """Test that multi-node process graphs return None."""
        # Create a multi-node process graph
        pg_data = {
            "process_graph": {
                "load1": {
                    "process_id": "load_collection",
                    "arguments": {"id": "test"},
                },
                "reduce1": {
                    "process_id": "reduce_dimension",
                    "arguments": {"data": {"from_node": "load1"}},
                    "result": True,
                },
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        result = extract_process_graph_process_id(pg)
        assert result is None

    def test_extract_from_partial_with_multi_node_graph_returns_none(self):
        """Test that partial wrapping multi-node graph returns None."""
        pg_data = {
            "process_graph": {
                "add1": {"process_id": "add", "arguments": {"x": 1, "y": 2}},
                "multiply1": {
                    "process_id": "multiply",
                    "arguments": {"x": {"from_node": "add1"}, "y": 3},
                    "result": True,
                },
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        partial_func = functools.partial(lambda g, d: g(d), pg)
        result = extract_process_graph_process_id(partial_func)
        assert result is None

    def test_extract_from_regular_function_returns_none(self):
        """Test that regular functions return None."""

        def my_reducer(data):
            return data

        result = extract_process_graph_process_id(my_reducer)
        assert result is None

    def test_extract_from_lambda_returns_none(self):
        """Test that lambda functions return None."""
        result = extract_process_graph_process_id(lambda x: x)
        assert result is None

    def test_extract_from_partial_without_process_graph_returns_none(self):
        """Test that regular functools.partial returns None."""

        def my_func(x, y):
            return x + y

        partial_func = functools.partial(my_func, y=10)
        result = extract_process_graph_process_id(partial_func)
        assert result is None

    def test_extract_mean_process_graph(self):
        """Test extraction of 'mean' process."""
        pg_data = {
            "process_graph": {
                "mean1": {
                    "process_id": "mean",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        result = extract_process_graph_process_id(pg)
        assert result == "mean"

    @pytest.mark.parametrize(
        "process_id",
        ["first", "mean", "median", "min", "max", "sum", "count", "sd", "variance"],
    )
    def test_extract_various_single_node_processes(self, process_id):
        """Test extraction works for various single-node process types."""
        pg_data = {
            "process_graph": {
                f"{process_id}1": {
                    "process_id": process_id,
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        result = extract_process_graph_process_id(pg)
        assert result == process_id

    def test_extract_from_nested_partial(self):
        """Test extraction from nested functools.partial."""
        pg_data = {
            "process_graph": {
                "first1": {
                    "process_id": "first",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        # Nested partial
        def inner(graph, data):
            return graph(data)

        def outer(func, data):
            return func(data)

        inner_partial = functools.partial(inner, pg)
        outer_partial = functools.partial(outer, inner_partial)

        # Should not find it in nested partial (only checks first level)
        result = extract_process_graph_process_id(outer_partial)
        assert result is None

    def test_extract_from_closure_with_raw_dict(self):
        """Test extraction from closure containing node data dict with process_id."""
        from uuid import UUID

        from openeo_pg_parser_networkx.pg_schema import ParameterReference

        # The actual node data structure found in closure
        node_data = {
            "process_id": "first",
            "resolved_kwargs": {"data": ParameterReference(from_parameter="data")},
            "node_name": "first1",
            "process_graph_uid": UUID("045adeb6-1122-4f38-9bca-a2e9ffe4b203"),
            "result": True,
        }

        # Create a closure that captures the node data
        def make_wrapper(data):
            def wrapper(input_data):
                # The data dict is captured in the closure
                return input_data

            return wrapper

        wrapped = make_wrapper(node_data)
        partial_func = functools.partial(wrapped)

        result = extract_process_graph_process_id(partial_func)
        assert result == "first"

    def test_extract_from_closure_with_wrapped_dict(self):
        """Test extraction from closure with wrapped pg_data dict."""
        pg_data = {
            "process_graph": {
                "first1": {
                    "process_id": "first",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }

        def make_wrapper(raw_pg_data):
            def wrapper(data):
                return data

            return wrapper

        wrapped = make_wrapper(pg_data)
        partial_func = functools.partial(wrapped)

        result = extract_process_graph_process_id(partial_func)
        assert result == "first"

    def test_extract_from_closure_with_mean_dict(self):
        """Test extraction from closure with mean process node data."""
        from uuid import uuid4

        from openeo_pg_parser_networkx.pg_schema import ParameterReference

        node_data = {
            "process_id": "mean",
            "resolved_kwargs": {"data": ParameterReference(from_parameter="data")},
            "node_name": "mean1",
            "process_graph_uid": uuid4(),
            "result": True,
        }

        def make_wrapper(data):
            def wrapper(input_data):
                return input_data

            return wrapper

        wrapped = make_wrapper(node_data)
        partial_func = functools.partial(wrapped)

        result = extract_process_graph_process_id(partial_func)
        assert result == "mean"

    def test_extract_with_none_input(self):
        """Test extraction handles None gracefully."""
        # Should not raise, just return None
        result = extract_process_graph_process_id(None)  # type: ignore
        assert result is None

    def test_extract_with_class_callable(self):
        """Test extraction with a class that implements __call__."""

        class MyCallable:
            def __call__(self, data):
                return data

        result = extract_process_graph_process_id(MyCallable())
        assert result is None


class TestPixelSelectionReducersMapping:
    """Tests for the PIXEL_SELECTION_REDUCERS mapping."""

    def test_pixel_selection_reducers_contains_expected_keys(self):
        """Test that PIXEL_SELECTION_REDUCERS contains all expected reducers."""
        expected = {
            "firstpixel",
            "first",  # Added for process graph support
            "mean",
            "median",
            "sd",
            "stdev",
            "count",
            "highestpixel",
            "lowestpixel",
            "lastbandlow",
            "lastbandhight",
        }
        assert set(PIXEL_SELECTION_REDUCERS.keys()) == expected

    def test_get_pixel_selection_method_name_with_valid_reducer(self):
        """Test _get_pixel_selection_method_name returns correct method name."""
        # Test with a function that has __name__ in the mapping
        assert _get_pixel_selection_method_name(stdev) == "stdev"
        assert _get_pixel_selection_method_name(count) == "count"
        assert _get_pixel_selection_method_name(highestpixel) == "highest"
        assert _get_pixel_selection_method_name(lowestpixel) == "lowest"
        assert _get_pixel_selection_method_name(firstpixel) == "first"
        assert _get_pixel_selection_method_name(lastbandlow) == "lastbandlow"
        assert _get_pixel_selection_method_name(lastbandhight) == "lastbandhight"

    def test_get_pixel_selection_method_name_with_unknown_reducer(self):
        """Test _get_pixel_selection_method_name returns None for unknown reducers."""

        def custom_reducer(data):
            return data

        assert _get_pixel_selection_method_name(custom_reducer) is None

    def test_get_pixel_selection_method_name_with_no_name(self):
        """Test _get_pixel_selection_method_name handles functions without __name__."""
        # Lambda functions have __name__ as '<lambda>'
        assert _get_pixel_selection_method_name(lambda x: x) is None

    def test_get_pixel_selection_method_name_with_process_graph(self):
        """Test _get_pixel_selection_method_name works with OpenEOProcessGraph."""
        # Create a single-node 'first' process graph
        pg_data = {
            "process_graph": {
                "first1": {
                    "process_id": "first",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        # Should recognize 'first' as a pixel selection method
        assert _get_pixel_selection_method_name(pg) == "first"

    def test_get_pixel_selection_method_name_with_mean_process_graph(self):
        """Test _get_pixel_selection_method_name works with mean OpenEOProcessGraph."""
        pg_data = {
            "process_graph": {
                "mean1": {
                    "process_id": "mean",
                    "arguments": {"data": {"from_parameter": "data"}},
                    "result": True,
                }
            }
        }
        pg = OpenEOProcessGraph(pg_data=pg_data)

        # Should recognize 'mean' as a pixel selection method
        assert _get_pixel_selection_method_name(pg) == "mean"


class TestPixelSelectionReducersWithRasterStack:
    """Tests for pixel selection reducers with RasterStack input."""

    @pytest.fixture
    def raster_stack(self):
        """Create a sample RasterStack for testing."""
        images = {}
        for i, date in enumerate(["2021-01-01", "2021-01-02", "2021-01-03"]):
            # Create data where each date has increasing values
            data = np.ma.array(
                np.ones((1, 10, 10), dtype=np.float32) * (i + 1),
                mask=np.zeros((1, 10, 10), dtype=bool),
            )
            images[date] = ImageData(data, band_names=["band1"])
        return RasterStack.from_images(images)

    @pytest.fixture
    def raster_stack_with_masks(self):
        """Create a RasterStack with partial masking for testing."""
        images = {}
        for i, date in enumerate(["2021-01-01", "2021-01-02", "2021-01-03"]):
            data = np.ones((1, 10, 10), dtype=np.float32) * (i + 1)
            mask = np.zeros((1, 10, 10), dtype=bool)
            # Mask different regions for each date
            if i == 0:
                mask[:, :5, :] = True  # First date: mask left half
            elif i == 1:
                mask[:, 5:, :] = True  # Second date: mask right half
            # Third date: no mask
            images[date] = ImageData(np.ma.array(data, mask=mask), band_names=["band1"])
        return RasterStack.from_images(images)

    def test_highestpixel_with_raster_stack(self, raster_stack):
        """Test highestpixel returns highest values from RasterStack."""
        result = highestpixel(raster_stack)
        # Highest value should be 3.0 (from 2021-01-03)
        assert np.allclose(result, 3.0)
        assert result.shape == (1, 10, 10)

    def test_lowestpixel_with_raster_stack(self, raster_stack):
        """Test lowestpixel returns lowest values from RasterStack."""
        result = lowestpixel(raster_stack)
        # Lowest value should be 1.0 (from 2021-01-01)
        assert np.allclose(result, 1.0)
        assert result.shape == (1, 10, 10)

    def test_firstpixel_with_raster_stack(self, raster_stack_with_masks):
        """Test firstpixel fills masked areas with first valid values."""
        result = firstpixel(raster_stack_with_masks)
        assert result.shape == (1, 10, 10)
        # Should have no fully masked areas
        assert not np.all(result == 0)

    def test_count_with_raster_stack(self, raster_stack):
        """Test count returns pixel counts from RasterStack."""
        result = count(raster_stack)
        # All pixels should have count of 3 (no masking)
        assert np.allclose(result, 3)
        assert result.shape == (1, 10, 10)

    def test_stdev_with_raster_stack(self, raster_stack):
        """Test stdev calculates standard deviation from RasterStack."""
        result = stdev(raster_stack)
        # Values are 1, 2, 3 -> std should be approximately 0.816 (ddof=0) or 1.0 (ddof=1)
        assert result.shape == (1, 10, 10)
        # Just verify it's a reasonable positive value
        assert np.all(result >= 0)


class TestPixelSelectionReducersWithArrays:
    """Tests for pixel selection reducers with array inputs."""

    def test_highestpixel_with_array(self):
        """Test highestpixel with numpy array input."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[2, 3], [4, 5]]])
        result = highestpixel(data)
        expected = np.array([[5, 6], [7, 8]])
        np.testing.assert_array_equal(result, expected)

    def test_lowestpixel_with_array(self):
        """Test lowestpixel with numpy array input."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[2, 3], [4, 5]]])
        result = lowestpixel(data)
        expected = np.array([[1, 2], [3, 4]])
        np.testing.assert_array_equal(result, expected)

    def test_highestpixel_with_masked_array(self):
        """Test highestpixel with masked array input."""
        data = np.ma.array(
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            mask=[[[False, False], [False, False]], [[False, False], [False, False]]],
        )
        result = highestpixel(data)
        expected = np.array([[5, 6], [7, 8]])
        np.testing.assert_array_equal(result, expected)

    def test_lowestpixel_with_masked_array(self):
        """Test lowestpixel with masked array input."""
        data = np.ma.array(
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            mask=[[[False, False], [False, False]], [[False, False], [False, False]]],
        )
        result = lowestpixel(data)
        expected = np.array([[1, 2], [3, 4]])
        np.testing.assert_array_equal(result, expected)

    def test_count_with_masked_array(self):
        """Test count with masked array input."""
        data = np.ma.array(
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            mask=[[[True, False], [False, False]], [[False, True], [False, False]]],
        )
        result = count(data)
        # First pixel (0,0): one masked -> count 1
        # Second pixel (0,1): one masked -> count 1
        # Third pixel (1,0): none masked -> count 2
        # Fourth pixel (1,1): none masked -> count 2
        expected = np.array([[1, 1], [2, 2]])
        np.testing.assert_array_equal(result, expected)

    def test_count_with_regular_array(self):
        """Test count with regular numpy array."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
        result = count(data)
        # All pixels should have count of 2 (no masking)
        expected = np.full((2, 2), 2)
        np.testing.assert_array_equal(result, expected)

    def test_firstpixel_with_masked_array(self):
        """Test firstpixel fills masked values."""
        data = np.ma.array(
            [[[0, 2], [3, 0]], [[5, 0], [0, 8]]],
            mask=[[[True, False], [False, True]], [[False, True], [True, False]]],
        )
        result = firstpixel(data)
        # First valid values should be: 5, 2, 3, 8
        expected = np.ma.array([[5, 2], [3, 8]])
        np.testing.assert_array_equal(result.data, expected.data)

    def test_firstpixel_with_regular_array(self):
        """Test firstpixel with regular array returns first element."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
        result = firstpixel(data)
        expected = np.array([[1, 2], [3, 4]])
        np.testing.assert_array_equal(result, expected)


class TestLastBandSelectors:
    """Tests for lastbandlow and lastbandhight reducers."""

    @pytest.fixture
    def multiband_stack(self):
        """Create a RasterStack with multi-band images for last band tests."""
        images = {}
        # Create 3 dates with 2-band images
        # The last band value determines which pixel gets selected
        for i, date in enumerate(["2021-01-01", "2021-01-02", "2021-01-03"]):
            # Band 1: data values, Band 2: decision values
            band1 = np.ones((10, 10), dtype=np.float32) * (i + 1) * 10
            band2 = np.ones((10, 10), dtype=np.float32) * (3 - i)  # 3, 2, 1
            data = np.ma.array(
                np.stack([band1, band2]),
                mask=np.zeros((2, 10, 10), dtype=bool),
            )
            images[date] = ImageData(data, band_names=["data", "decision"])
        return RasterStack.from_images(images)

    def test_lastbandlow_with_raster_stack(self, multiband_stack):
        """Test lastbandlow selects based on lowest last band value."""
        result = lastbandlow(multiband_stack)
        # Result from apply_pixel_selection returns the selected pixel data
        # The result shape depends on the band count of input images
        assert result.shape[1:] == (10, 10)

    def test_lastbandhight_with_raster_stack(self, multiband_stack):
        """Test lastbandhight selects based on highest last band value."""
        result = lastbandhight(multiband_stack)
        # Result from apply_pixel_selection returns the selected pixel data
        assert result.shape[1:] == (10, 10)

    def test_lastbandlow_with_array(self):
        """Test lastbandlow with numpy array input."""
        # Shape: (temporal, bands, height, width)
        data = np.array(
            [
                [[[10, 20]], [[3, 3]]],  # First: data=10,20, decision=3
                [[[30, 40]], [[1, 1]]],  # Second: data=30,40, decision=1
            ]
        )
        result = lastbandlow(data)
        # Result depends on implementation - just verify it runs
        assert result is not None

    def test_lastbandhight_with_array(self):
        """Test lastbandhight with numpy array input."""
        # Shape: (temporal, bands, height, width)
        data = np.array(
            [
                [[[10, 20]], [[3, 3]]],  # First: data=10,20, decision=3
                [[[30, 40]], [[1, 1]]],  # Second: data=30,40, decision=1
            ]
        )
        result = lastbandhight(data)
        # Result depends on implementation - just verify it runs
        assert result is not None

    def test_lastbandlow_with_1d_array(self):
        """Test lastbandlow handles 1D arrays."""
        data = np.array([1, 2, 3])
        result = lastbandlow(data)
        np.testing.assert_array_equal(result, data)

    def test_lastbandhight_with_1d_array(self):
        """Test lastbandhight handles 1D arrays."""
        data = np.array([1, 2, 3])
        result = lastbandhight(data)
        np.testing.assert_array_equal(result, data)


class TestReducerTypeErrors:
    """Tests for type error handling in reducers."""

    def test_highestpixel_invalid_type(self):
        """Test highestpixel raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            highestpixel("invalid")

    def test_lowestpixel_invalid_type(self):
        """Test lowestpixel raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            lowestpixel("invalid")

    def test_count_invalid_type(self):
        """Test count raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            count("invalid")

    def test_lastbandlow_invalid_type(self):
        """Test lastbandlow raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            lastbandlow("invalid")

    def test_lastbandhight_invalid_type(self):
        """Test lastbandhight raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            lastbandhight("invalid")

    def test_firstpixel_invalid_type(self):
        """Test firstpixel raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            firstpixel("invalid")


class TestApplyPixelSelectionMethods:
    """Tests for apply_pixel_selection with all supported methods."""

    @pytest.fixture
    def sample_stack(self):
        """Create a simple RasterStack for testing."""
        images = {}
        for i, date in enumerate(["2021-01-01", "2021-01-02"]):
            data = np.ma.array(
                np.ones((1, 5, 5), dtype=np.float32) * (i + 1),
                mask=np.zeros((1, 5, 5), dtype=bool),
            )
            images[date] = ImageData(data, band_names=["band1"])
        return RasterStack.from_images(images)

    @pytest.mark.parametrize(
        "method",
        ["first", "highest", "lowest", "mean", "median", "stdev", "count"],
    )
    def test_apply_pixel_selection_methods(self, sample_stack, method):
        """Test apply_pixel_selection with various methods."""
        result = apply_pixel_selection(sample_stack, pixel_selection=method)
        assert isinstance(result, dict)
        assert "data" in result
        assert isinstance(result["data"], ImageData)
        assert result["data"].metadata["pixel_selection_method"] == method

    def test_apply_pixel_selection_lastbandlow(self):
        """Test apply_pixel_selection with lastbandlow method."""
        images = {}
        for i, date in enumerate(["2021-01-01", "2021-01-02"]):
            # Multi-band data for lastband methods
            data = np.ma.array(
                np.ones((2, 5, 5), dtype=np.float32) * (i + 1),
                mask=np.zeros((2, 5, 5), dtype=bool),
            )
            images[date] = ImageData(data, band_names=["band1", "band2"])
        stack = RasterStack.from_images(images)

        result = apply_pixel_selection(stack, pixel_selection="lastbandlow")
        assert isinstance(result, dict)
        assert "data" in result

    def test_apply_pixel_selection_lastbandhight(self):
        """Test apply_pixel_selection with lastbandhight method."""
        images = {}
        for i, date in enumerate(["2021-01-01", "2021-01-02"]):
            data = np.ma.array(
                np.ones((2, 5, 5), dtype=np.float32) * (i + 1),
                mask=np.zeros((2, 5, 5), dtype=bool),
            )
            images[date] = ImageData(data, band_names=["band1", "band2"])
        stack = RasterStack.from_images(images)

        result = apply_pixel_selection(stack, pixel_selection="lastbandhight")
        assert isinstance(result, dict)
        assert "data" in result
