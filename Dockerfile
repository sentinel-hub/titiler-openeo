# syntax=docker/dockerfile:1

# Wolfi's apk repository is open: no Chainguard subscription is needed. `latest`
# is the only free tag, so the digest is the version pin and Dependabot's docker
# ecosystem moves it. Keep it fresh: the packages below are unpinned and built
# against Wolfi's current glibc, while this rootfs keeps the glibc it shipped
# with. A stale digest fails docker/smoke-test.sh.
FROM cgr.dev/chainguard/wolfi-base:latest@sha256:be019c1c74fe69204a42c4e10bc4d886a49bf5403ffa74374ef7baa408fc9a26 AS base

# After FROM on purpose: an ARG before the first FROM is only in scope for FROM lines.
ARG PYTHON_VERSION=3.14

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Unpinned on purpose: Wolfi removes old versions, so `=<version>` pins break the
# build within days. libstdc++ is needed by the rasterio, pyproj and numexpr
# wheels, which link it without vendoring it. Python's mpdecimal pulls it in
# today; it is listed so that a change there cannot drop it. libpq-18 is the
# libpq psycopg2 is built against below; keep its major in step with
# postgresql-18-dev.
RUN apk add --no-cache python-${PYTHON_VERSION} libstdc++ libpq-18

# A stage rather than COPY --from=<image>, because Dependabot only reads FROM lines.
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

# Build stage
FROM base AS builder

LABEL stage=builder

# To build psycopg2 from source: a compiler, Python headers, pg_config and the
# libpq headers.
RUN apk add --no-cache gcc python-${PYTHON_VERSION}-dev postgresql-18-dev

COPY --from=uv /uv /usr/local/bin/

# The apk Python, not one uv downloads: apk patches it and Trivy can see it.
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /tmp/app
COPY pyproject.toml uv.lock ./
# psycopg2-binary from its sdist (hash in uv.lock), linked to the apk libpq
# instead of the libpq and OpenSSL its wheel bundles, as psycopg advises for
# production. The extra stays psycopg2-binary for pip users.
RUN uv sync --locked --no-dev --extra server --extra oidc --extra postgres --no-install-project \
    --no-binary-package psycopg2-binary
COPY README.md ./
COPY titiler/ titiler/
RUN uv pip install --no-deps .

# Runtime stage
FROM base

LABEL org.opencontainers.image.source="https://github.com/developmentseed/titiler-openeo"
LABEL org.opencontainers.image.description="TiTiler OpenEO API"
LABEL org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:${PATH}"

COPY --from=builder /opt/venv /opt/venv

# Owned by wolfi-base's nonroot user (uid 65532), as the chart's fsGroup expects.
RUN mkdir -p /data /config && chown 65532:65532 /data /config
COPY --chown=65532:65532 log_config.yaml /config/log_config.yaml

WORKDIR /app
# Numeric, so Kubernetes can verify runAsNonRoot. Binding port 80 as non-root
# needs the chart's net.ipv4.ip_unprivileged_port_start sysctl (Docker allows it
# by default).
USER 65532

VOLUME /data
VOLUME /config

CMD ["uvicorn", "titiler.openeo.main:app", "--host", "0.0.0.0", "--port", "80", "--log-config", "/config/log_config.yaml"]

EXPOSE 80
