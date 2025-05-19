# openEO by TiTiler

TiTiler backend for openEO

![TiTiler OpenEO](docs/src/img/image.png)

## Overview

[`titiler-openeo`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/main.py) is a TiTiler backend implementation for openEO developed by [![Development Seed](docs/src/img/ds-logo-hor.svg){: style="height:25px"}](https://developmentseed.org/) and [![Sinergise](docs/src/img/sinergise-logo.png){: style="height:25px"}](https://www.sinergise.com/).

The main goal of this project is to provide a light and fast backend for openEO services and processes using the TiTiler engine.
This simplicity comes with some specific implementation choices like the type of data managed by the backend.
It is focused on image raster data that can be processed on-the-fly and served as tiles or as light dynamic raw data.
A concept note is available [here](https://sentinel-hub.github.io/titiler-openeo/concepts/) to describe in more detail the implementation choices.

The application provides with a minimal [openEO API (L1A and L1C)](https://openeo.org/documentation/1.0/developers/profiles/api.html#api-profiles).

## Features

- [STAC API integration](https://sentinel-hub.github.io/titiler-openeo/concepts/#collections-and-stac-integration) with external STAC services
- [Synchronous processing](https://sentinel-hub.github.io/titiler-openeo/concepts/#data-model) capabilities
- Various output formats (e.g., JPEG, PNG)
- Multiple [supported processes](https://sentinel-hub.github.io/titiler-openeo/concepts/#data-reduction)
- Dynamic tiling services
- FastAPI-based application
- Middleware for CORS, compression, and caching
- Optimized [RasterStack data model](https://sentinel-hub.github.io/titiler-openeo/raster-stack/) for consistent processing
- [LazyRasterStack implementation](https://sentinel-hub.github.io/titiler-openeo/raster-stack/#lazyrasterstack) for improved performance

## Installation

To install [`titiler-openeo`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/main.py), clone the repository and install the dependencies:

```bash
git clone https://github.com/sentinel-hub/titiler-openeo.git
cd titiler-openeo
python -m pip install -e .
```

## Usage

To run the application, use the following command:

```bash
cp .env.eoapi .env
export $(cat .env | xargs)
uvicorn titiler.openeo.main:app --host 0.0.0.0 --port 8081
```

## Configuration

Configuration settings can be provided via environment variables. The following settings are available:

- TITILER_OPENEO_STAC_API_URL: URL of the STAC API with the collections to be used
- TITILER_OPENEO_SERVICE_STORE_URL: URL of the openEO service store json file

In this repository, 2 `.env` sample files are provided:

- [`.env.eoapi`](https://github.com/sentinel-hub/titiler-openeo/blob/main/.env.eoapi) that uses the [Earth Observation API (EOAPI)](https://eoapi.dev/).

  ```bash
  TITILER_OPENEO_STAC_API_URL="https://stac.eoapi.dev"
  TITILER_OPENEO_SERVICE_STORE_URL="services/eoapi.json"
  ```

- [`.env.cdse`](https://github.com/sentinel-hub/titiler-openeo/blob/main/.env.cdse) that uses the [Copernicus Data Space Ecosystem (CDSE)](https://dataspace.copernicus.eu/)

  ```bash
  TITILER_OPENEO_STAC_API_URL="https://stac.dataspace.copernicus.eu/v1"
  TITILER_OPENEO_SERVICE_STORE_URL="services/copernicus.json"
  ```

  In order to access asset object store and to retrieve data efficiently, it requires to set additional **environment variables**:

  ```bash
  AWS_S3_ENDPOINT=eodata.dataspace.copernicus.eu # CDSE S3 endpoint URL
  AWS_ACCESS_KEY_ID=<your_access_key> # S3 access key
  AWS_SECRET_ACCESS_KEY=<your_secret_key> # S3 secret key
  AWS_VIRTUAL_HOSTING=FALSE # Disable virtual hosting
  CPL_VSIL_CURL_CACHE_SIZE=200000000 # Global LRU cache size
  GDAL_HTTP_MULTIPLEX=TRUE # Enable HTTP multiplexing
  GDAL_CACHEMAX=500 # Set GDAL cache size
  GDAL_INGESTED_BYTES_AT_OPEN=50000 # Open a larger bytes range when reading
  GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES # Merge consecutive ranges
  VSI_CACHE_SIZE=5000000 # Set VSI cache size
  VSI_CACHE=TRUE # Enable VSI cache
  ```

visit ['Access to EO data via S3'](https://documentation.dataspace.copernicus.eu/APIs/S3.html) for information on how to access the Copernicus Data Space Ecosystem (CDSE) data via S3.

## Development

To set up a development environment, install the development dependencies:

```bash
python -m pip install -e ".[test,dev]"
pre-commit install
```

### Running Tests

To run the tests, use the following command:

```bash
python -m pytest
```

### Use the openEO editor

To use the openEO editor, you can use Docker Compose to start all services:

```bash
docker compose up
```

This will start:
- API service at http://localhost:8081
- openEO Web Editor at http://localhost:8080
- Keycloak at http://localhost:8082

Access the editor at http://localhost:8080 and set the backend URL to http://localhost:8081.

For authentication setup and testing, see the [Admin Guide](https://sentinel-hub.github.io/titiler-openeo/admin-guide/#authentication).

## License

See [LICENSE](https://github.com/sentinel-hub/titiler-openeo/blob/main/LICENSE)

## Authors

Created by [Development Seed](<http://developmentseed.org>) and [Sinergise](https://www.sinergise.com/).

See [contributors](https://github.com/sentinel-hub/titiler-openeo/graphs/contributors) for a listing of individual contributors.

## Changes

See [CHANGES.md](https://github.com/sentinel-hub/titiler-openeo/blob/main/CHANGES.md).
