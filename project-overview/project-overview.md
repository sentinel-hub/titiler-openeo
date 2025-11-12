# Project Overview

## About openEO by TiTiler

openEO by TiTiler is a fast and lightweight implementation of the openEO API, developed by [Development Seed](https://developmentseed.org/). It provides efficient management of raster-based processes using the TiTiler engine.

## Context

openEO serves as an abstraction layer for Earth Observation (EO) processing and has gained significant traction within the community. Several data hubs now offer openEO as a service, notably the [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/analyse/openeo), [Terrascope](https://terrascope.be), and [EODC](https://openeo.cloud/). Additionally, [EOEPCA+](https://eoepca.readthedocs.io/projects/processing/en/latest/design/processing-engine/openeo/), with its processing building block, is furthering the deployment of openEO.

## Features

The main features include:

- Built on top of FastAPI
- Cloud Optimized GeoTIFF support
- SpatioTemporal Asset Catalog support
- Multiple projections support via morecantile
- JPEG / PNG / Geotiff / JSON / CSV output format support
- XYZ secondary service support
- Automatic OpenAPI documentation

## API Support

The application implements the [openEO API (L1A and L1C)](https://openeo.org/documentation/1.0/developers/profiles/api.html#api-profiles) profiles:

- **Synchronous Processing (L1A)**: For direct processing and downloading of data
- **Secondary Web Services (L1C)**: For data visualization with dynamic tiling

## Project Goals

The primary objectives of this project are:

1. Provide a lightweight and fast backend for openEO services
2. Focus on efficient raster data processing
3. Enable dynamic tiling and visualization capabilities
4. Maintain compatibility with STAC API services
5. Deliver high-performance synchronous processing
