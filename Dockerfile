# Dockerfile for running titiler application with uvicorn server
ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim

RUN apt update && apt upgrade -y

# ref: https://github.com/rasterio/rasterio-wheels/issues/136, https://github.com/docker-library/python/issues/989
RUN apt install -y libexpat1

RUN python -m pip install uvicorn gunicorn uvicorn worker

# Copy files and install titiler.openeo
WORKDIR /tmp

COPY titiler/ titiler/
COPY pyproject.toml pyproject.toml
COPY README.md README.md

RUN python -m pip install --no-cache-dir --upgrade ".[pystac]"
RUN rm -rf /tmp/titiler pyproject.toml README.md

RUN mkdir /data

WORKDIR /app
