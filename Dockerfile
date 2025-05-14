# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.11

# Build stage
FROM python:${PYTHON_VERSION}-slim AS builder

# Set build labels
LABEL stage=builder
LABEL org.opencontainers.image.source="https://github.com/developmentseed/titiler-openeo"
LABEL org.opencontainers.image.description="TiTiler OpenEO API"
LABEL org.opencontainers.image.licenses="MIT"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libexpat1 && \
    rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install application
WORKDIR /tmp
COPY titiler/ titiler/
COPY pyproject.toml .
COPY README.md .
RUN pip install --no-cache-dir --upgrade uvicorn PyYAML ".[pystac,oidc,postgres]"

# Runtime stage
FROM python:${PYTHON_VERSION}-slim

# Set runtime labels
LABEL org.opencontainers.image.source="https://github.com/developmentseed/titiler-openeo"
LABEL org.opencontainers.image.description="TiTiler OpenEO API"
LABEL org.opencontainers.image.licenses="MIT"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libexpat1 \
    curl && \
    rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user
RUN useradd -m -s /bin/bash titiler && \
    mkdir -p /data /config
COPY log_config.yaml /config/log_config.yaml
RUN chown -R titiler:titiler /data /config

WORKDIR /app
USER titiler

# Create data directory
VOLUME /data
# Create config directory and copy default config
VOLUME /config

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl --fail http://localhost:8000/api || exit 1

# Set default command
CMD ["uvicorn", "titiler.openeo.main:app", "--host", "0.0.0.0", "--port", "80", "--log-config", "/config/log_config.yaml"]

# Expose port
EXPOSE 80
