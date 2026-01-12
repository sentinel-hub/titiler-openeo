# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.11
ARG SETUPTOOLS_SCM_PRETEND_VERSION

# Build stage
FROM python:${PYTHON_VERSION}-slim AS builder

# Set version for setuptools_scm
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TITILER_OPENEO=${SETUPTOOLS_SCM_PRETEND_VERSION}

# Set build labels
LABEL stage=builder
LABEL org.opencontainers.image.source="https://github.com/developmentseed/titiler-openeo"
LABEL org.opencontainers.image.description="TiTiler OpenEO API"
LABEL org.opencontainers.image.licenses="MIT"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libexpat1 curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Configure uv-managed virtual environment
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# Install application
WORKDIR /tmp/app
COPY titiler/ titiler/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra server --extra oidc --extra postgres && \
    uv pip install PyYAML && \
    uv pip install --no-deps .

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
    libexpat1 fonts-dejavu \
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
