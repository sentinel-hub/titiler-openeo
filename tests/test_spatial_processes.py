"""Tests for spatial process implementations."""

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.formats import encode
from titiler.openeo.processes.implementations.spatial import (
    aggregate_spatial,
    resample_spatial,
)


@pytest.fixture
def sample_raster_stack():
    """Create a sample RasterStack with geographic information for testing."""
    # Create a 3-band image with CRS and bounds
    data1 = np.ma.array(
        np.random.randint(0, 256, size=(3, 20, 20), dtype=np.uint8),
        mask=np.zeros((3, 20, 20), dtype=bool),
    )
    img1 = ImageData(
        data1,
        band_descriptions=["red", "green", "blue"],
        crs=CRS.from_epsg(4326),  # WGS84
        bounds=(-180, -90, 180, 90),  # World bounds
    )

    # Create a second image
    data2 = np.ma.array(
        np.random.randint(0, 256, size=(3, 20, 20), dtype=np.uint8),
        mask=np.zeros((3, 20, 20), dtype=bool),
    )
    img2 = ImageData(
        data2,
        band_descriptions=["red", "green", "blue"],
        crs=CRS.from_epsg(4326),  # WGS84
        bounds=(-180, -90, 180, 90),  # World bounds
    )

    # Return a RasterStack with two samples
    return {"2021-01-01": img1, "2021-01-02": img2}


@pytest.mark.skip(reason="Requires proper GDAL driver setup")
def test_resample_spatial(sample_raster_stack):
    """Test resampling the spatial dimensions of a RasterStack."""
    # Test reprojection to Web Mercator
    result = resample_spatial(
        data=sample_raster_stack,
        projection=3857,  # Web Mercator
        resolution=1000,  # 1km resolution
        align=None,
        method="nearest",
    )

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with the new CRS
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.crs.to_epsg() == 3857
        # Band count should remain the same
        assert img_data.count == 3


@pytest.mark.skip(reason="Requires proper GDAL driver setup")
def test_resample_spatial_with_near_method(sample_raster_stack):
    """Test resampling with 'near' method (OpenEO alias for 'nearest')."""
    # Test that 'near' method works (OpenEO alias for 'nearest')
    result = resample_spatial(
        data=sample_raster_stack,
        projection=3857,  # Web Mercator
        resolution=1000,  # 1km resolution
        align=None,
        method="near",
    )

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with the new CRS
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.crs.to_epsg() == 3857
        # Band count should remain the same
        assert img_data.count == 3


def test_resample_spatial_method_validation():
    """Test that resample_spatial validates resampling methods correctly."""
    # Create minimal test data
    data = np.ma.array(
        np.random.randint(0, 256, size=(1, 10, 10), dtype=np.uint8),
        mask=np.zeros((1, 10, 10), dtype=bool),
    )
    img = ImageData(
        data,
        band_descriptions=["band1"],
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 1, 1),
    )
    raster_stack = {"test": img}

    # Test that valid method strings are accepted
    valid_methods = [
        "nearest",
        "near",
        "bilinear",
        "cubic",
        "cubicspline",
        "lanczos",
        "average",
        "mode",
    ]

    for method in valid_methods:
        try:
            # Just test that the function accepts these method strings
            # The actual reprojection may fail due to GDAL setup, but we're testing parameter validation
            resample_spatial(
                data=raster_stack,
                projection=4326,
                resolution=0.1,
                align=None,
                method=method,
            )
        except NotImplementedError:
            # align parameter not implemented is fine
            pass
        except Exception as e:
            # GDAL errors are expected in test environment
            # We only care that the method parameter is accepted
            if "Unsupported resampling method" in str(e):
                pytest.fail(f"Method '{method}' should be supported but was rejected")

    # Test that invalid method raises an error
    with pytest.raises(ValueError, match="Unsupported resampling method"):
        resample_spatial(
            data=raster_stack,
            projection=4326,
            resolution=0.1,
            align=None,
            method="invalid_method",
        )


def test_aggregate_spatial(sample_raster_stack):
    """Test aggregating statistics over a geometry."""
    # Create a simple geometry (bounding box)
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]],
    }

    # Mean reducer
    def mean_reducer(data, **kwargs):
        if isinstance(data, np.ndarray):
            return float(np.mean(data))
        return None

    # Test basic aggregation
    result = aggregate_spatial(
        data=sample_raster_stack, geometries=geometry, reducer=mean_reducer
    )

    # Result should be a GeoJSON FeatureCollection
    assert isinstance(result, dict)
    assert result["type"] == "FeatureCollection"
    assert "features" in result
    assert len(result["features"]) > 0

    # Check that each feature has values
    feature = result["features"][0]
    assert "properties" in feature
    assert "values" in feature["properties"]

    # Values should contain results for each date in the stack
    values = feature["properties"]["values"]
    assert len(values) == len(sample_raster_stack)
    for key, img in sample_raster_stack.items():
        assert key in values
        # The bands dimension is kept: one mean for each band, by band name
        assert list(values[key]) == ["red", "green", "blue"]
        for band_idx, band in enumerate(["red", "green", "blue"]):
            assert values[key][band] == pytest.approx(
                float(np.mean(img.array[band_idx]))
            )

    # Test a more complex case with target_dimension
    def mean_reducer_with_metadata(data, **kwargs):
        if isinstance(data, np.ndarray):
            return float(np.mean(data))
        return None

    result_with_metadata = aggregate_spatial(
        data=sample_raster_stack,
        geometries=geometry,
        reducer=mean_reducer_with_metadata,
        target_dimension="stats",
    )

    # Check the structure with target_dimension
    feature = result_with_metadata["features"][0]
    values = feature["properties"]["values"]

    # Values should now contain metadata for each date
    for key in sample_raster_stack.keys():
        assert key in values
        # With target_dimension, each value should be a dict with metadata
        for band in ["red", "green", "blue"]:
            band_result = values[key][band]
            assert isinstance(band_result["value"], float)
            assert band_result["total_count"] == 400
            assert band_result["valid_count"] == 400

    # Test with a feature collection
    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]
                    ],
                },
                "properties": {"name": "Feature 1"},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-90, -45], [90, -45], [90, 45], [-90, 45], [-90, -45]]
                    ],
                },
                "properties": {"name": "Feature 2"},
            },
        ],
    }

    result_features = aggregate_spatial(
        data=sample_raster_stack, geometries=feature_collection, reducer=mean_reducer
    )

    # Should have results for both features
    assert len(result_features["features"]) == 2

    # Check that original properties are preserved
    assert result_features["features"][0]["properties"]["name"] == "Feature 1"
    assert result_features["features"][1]["properties"]["name"] == "Feature 2"

    # Both should have values for each date
    for feature in result_features["features"]:
        values = feature["properties"]["values"]
        assert len(values) == len(sample_raster_stack)
        for key in sample_raster_stack.keys():
            assert key in values


WORLD = {
    "type": "Polygon",
    "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]],
}


def _mean(data, **kwargs):
    return np.ma.mean(data)


def test_aggregate_spatial_single_band_is_scalar():
    """A cube with one band gives one scalar per date, not a dict."""
    img = ImageData(
        np.ma.array(np.full((1, 4, 4), 2.0)),
        band_descriptions=["ndvi"],
        crs=CRS.from_epsg(4326),
        bounds=(-180, -90, 180, 90),
    )
    result = aggregate_spatial(
        data={"2021-01-01": img}, geometries=WORLD, reducer=_mean
    )
    assert result["features"][0]["properties"]["values"] == {"2021-01-01": 2.0}


def test_aggregate_spatial_all_bands_to_csv():
    """Each band is reduced; a band with no valid pixel gives an empty CSV cell."""
    data = np.ma.array(
        np.stack([np.full((4, 4), 1.0), np.full((4, 4), 3.0)]),
        mask=np.stack([np.zeros((4, 4), bool), np.ones((4, 4), bool)]),
    )
    img = ImageData(
        data,
        band_descriptions=["B03", "B04"],
        crs=CRS.from_epsg(4326),
        bounds=(-180, -90, 180, 90),
    )
    result = aggregate_spatial(
        data={"2023-01-05": img}, geometries=WORLD, reducer=_mean
    )
    values = result["features"][0]["properties"]["values"]["2023-01-05"]
    assert values["B03"] == 1.0
    assert values["B04"] is np.ma.masked

    csv_lines = encode(result, "CSV").data.decode().splitlines()
    assert csv_lines == ["date,feature_index,B03,B04", "2023-01-05,0,1.0,"]
