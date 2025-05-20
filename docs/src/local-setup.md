# Local Setup

This guide explains how to set up openEO by TiTiler locally.

## Installation

Clone the repository and install the dependencies:

```bash
git clone https://github.com/sentinel-hub/titiler-openeo.git
cd titiler-openeo
python -m pip install -e .
```

## Configuration

### Environment Setup

The application can be configured using different environment files:

1. EOAPI Configuration (default)
```bash
cp .env.eoapi .env
export $(cat .env | xargs)
```

This sets up:
```bash
TITILER_OPENEO_STAC_API_URL="https://stac.eoapi.dev"
TITILER_OPENEO_SERVICE_STORE_URL="services/eoapi.json"
```

2. CDSE Configuration
```bash
cp .env.cdse .env
export $(cat .env | xargs)
```

This configures:
```bash
TITILER_OPENEO_STAC_API_URL="https://stac.dataspace.copernicus.eu/v1"
TITILER_OPENEO_SERVICE_STORE_URL="services/copernicus.json"
```

For CDSE, additional environment variables are required for efficient data access:
```bash
AWS_S3_ENDPOINT=eodata.dataspace.copernicus.eu
AWS_ACCESS_KEY_ID=<your_access_key>
AWS_SECRET_ACCESS_KEY=<your_secret_key>
AWS_VIRTUAL_HOSTING=FALSE
CPL_VSIL_CURL_CACHE_SIZE=200000000
GDAL_HTTP_MULTIPLEX=TRUE
GDAL_CACHEMAX=500
GDAL_INGESTED_BYTES_AT_OPEN=50000
GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES
VSI_CACHE_SIZE=5000000
VSI_CACHE=TRUE
```

## Running the Application

Start the server:
```bash
uvicorn titiler.openeo.main:app --host 0.0.0.0 --port 8080
```

The API will be available at `http://localhost:8080`

## Using the openEO Editor

To use the openEO Web Editor with your local instance:

1. Start the openEO Web Editor:
```bash
docker pull mundialis/openeo-web-editor:latest
docker run -p 8081:80 mundialis/openeo-web-editor:latest
```

2. Access the editor at `http://localhost:8081`

3. Configure the editor:
   - Set backend URL to `http://localhost:8080`
   - Login with the default basic auth credentials:
     - Username: `test`
     - Password: `test`
