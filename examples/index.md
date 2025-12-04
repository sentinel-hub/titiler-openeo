# Examples

This section provides practical examples of using openEO by TiTiler for various Earth Observation tasks.

## Web Editor Examples

The openEO Web Editor provides a graphical interface for interacting with the API. To get started:

1. Start the services:

```bash
docker compose up
```

1. Access the editor at http://localhost:8080 and set the backend URL to http://localhost:8081

2. Authenticate using the instructions in the [Admin Guide](../admin-guide.md#authentication)

## Jupyter Notebook Examples

We provide several Jupyter notebooks demonstrating different use cases:

### [Manhattan Satellite Imagery](../notebooks/manhattan.ipynb)

Learn how to:

- Connect to the openEO backend
- Load and process Sentinel-2 imagery using default resolution (1024 pixels width)
- Control output resolution with explicit width/height parameters
- Create true-color RGB visualizations
- Apply color enhancements for better visualization

### [NDVI Time Series Analysis](../notebooks/ndvi_time_series.ipynb)

Explore how to:

- Calculate vegetation indices (NDVI)
- Extract time series data for specific areas
- Control memory usage with appropriate resolution settings
- Analyze temporal patterns in vegetation
- Visualize results using matplotlib

### Resolution Control Examples

The examples demonstrate different approaches to managing resolution:

1. **Default Resolution**:
```python
loadcol = load_collection_and_reduce(
    "SENTINEL2_L2A",
    spatial_extent=bbox,
    temporal_extent=["2021-01-01", "2021-12-31"],
    bands=["B04", "B08"]
)
# Uses default width of 1024 pixels for memory efficiency
```

2. **Custom Resolution**:
```python
loadcol = load_collection_and_reduce(
    "SENTINEL2_L2A",
    spatial_extent=bbox,
    temporal_extent=["2021-01-01", "2021-12-31"],
    bands=["B04", "B08"],
    width=2048,  # Explicitly control resolution
)
```

## Running the Notebooks

To run the notebooks locally:

1. Install the development dependencies:

```bash
python -m pip install -e ".[dev]"
```

1. Start Jupyter:

```bash
jupyter notebook docs/notebooks
```

1. Open the desired notebook and follow the instructions
