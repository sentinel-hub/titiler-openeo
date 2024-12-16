# titiler-openeo

TiTiler backend for openEO

## Overview

[`titiler-openeo`](titiler/openeo/main.py ) is a TiTiler backend implementation for openEO.

The main goal of this project is to provide a light and fast backend for openEO services and processes using the TiTiler engine.
This simplicity comes with some specific implementation choice like the type of data managed by the backend.
It is focused on image raster data that can be processed on-the-fly and served as tiles or as light dynamic raw data.

The application provides with a minimal [openEO API (L1A and L1C)](https://openeo.org/documentation/1.0/developers/profiles/api.html#api-profiles).

## Features

- STAC API integration with external STAC services
- Synchronous processing
- Various output formats (e.g., JPEG, PNG, COG)
- Multiple supported processes
- Dynamic tiling services
- FastAPI-based application
- Middleware for CORS, compression, and caching

## Installation

To install [`titiler-openeo`](titiler/openeo/main.py ), clone the repository and install the dependencies:

```bash
git clone https://github.com/developmentseed/titiler-openeo.git
cd titiler-openeo
python -m pip install -e .
```

## Usage

To run the application, use the following command:

```bash
cp .env.example .env
uvicorn titiler.openeo.main:app --host 0.0.0.0 --port 8080
```

## Configuration

Configuration settings can be provided via environment variables or a .env file. The following settings are available:

- TITILER_OPENEO_STAC_API_URL: URL of the STAC API with the collections to be used

Example `.env` file:

TITILER_OPENEO_SERVICE_STORE_URL="https://stac.dataspace.copernicus.eu/v1"

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

To use the openEO editor, start the server as described in #usage section.
Then, run the following command:

```bash
docker pull mundialis/openeo-web-editor:latest
docker run -p 8081:80 mundialis/openeo-web-editor:latest
```

Then, open the editor in your browser at http://localhost:8081.
In the editor, set the openEO backend URL to http://localhost:8080.
Login with the following credentials:

- Username: `anynymous`
- Password: `test`

## License

This project is licensed under the MIT license. See the [LICENSE](LICENSE) file for more information.