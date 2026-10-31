# syntax=docker/dockerfile:1

# Wolfi's apk repository is open: no Chainguard subscription is needed. `latest`
# is the only free tag, so the digest is the version pin and Dependabot's docker
# ecosystem moves it. Keep it fresh: the packages below are unpinned and built
# against Wolfi's current glibc, while this rootfs keeps the glibc it shipped
# with. A stale digest fails docker/smoke-test.sh.
FROM cgr.dev/chainguard/wolfi-base:latest@sha256:360896efb0753a5701c48f860e17130c205eae5a264797aa3e7ea126ee2923a7 AS base

# After FROM on purpose: an ARG before the first FROM is only in scope for FROM lines.
ARG PYTHON_VERSION=3.14

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Unpinned on purpose: Wolfi removes old versions, so `=<version>` pins break the
# build within days. libstdc++ is needed by the rasterio, pyproj and numexpr
# wheels, which link it without vendoring it. Python's mpdecimal pulls it in
# today; it is listed so that a change there cannot drop it.
RUN apk add --no-cache python-${PYTHON_VERSION} libstdc++

# A stage rather than COPY --from=<image>, because Dependabot only reads FROM lines.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# Build stage
FROM base AS builder

LABEL stage=builder

COPY --from=uv /uv /usr/local/bin/

# The apk Python, not one uv downloads: apk patches it and Trivy can see it.
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /tmp/app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra server --extra oidc --extra postgres --no-install-project
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
