# Dockerfile for running titiler application with uvicorn server
ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim

# ref: https://github.com/rasterio/rasterio-wheels/issues/136, https://github.com/docker-library/python/issues/989
RUN apt update && apt install -y libexpat1

RUN python -m pip install uvicorn

# Copy files and install titiler.openeo
WORKDIR /tmp

COPY titiler/ titiler/
COPY pyproject.toml pyproject.toml
COPY README.md README.md

RUN python -m pip install --no-cache-dir --upgrade ".[pystac]"
RUN rm -rf /tmp/titiler pyproject.toml README.md

ENV HOST 0.0.0.0
ENV PORT 80
CMD uvicorn titiler.openeo.main:app --host ${HOST} --port ${PORT}
