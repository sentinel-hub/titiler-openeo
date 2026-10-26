#!/usr/bin/env bash
#
# smoke-test.sh: assert the runtime contract of the titiler-openeo image.
#
#   ./docker/smoke-test.sh <image-ref>
#
# A green `docker build` says nothing about what callers of the image rely on.
# The expectations come from those callers and are written down here on purpose:
# the Helm chart (deployment/k8s/charts: `command: uvicorn`, its args, the /config
# mount, probes on :80, fsGroup 65532), docker-compose.yml (default CMD, a JSON
# store in /tmp) and the deployments that set GDAL_* and WEB_CONCURRENCY.
#
# Env: SMOKE_PLATFORM  platform to test (default linux/amd64, the published one)
#      SMOKE_TIMEOUT   seconds to wait for HTTP (default 180: QEMU on arm64 is slow)

set -euo pipefail

IMAGE="${1:?usage: $0 <image-ref>}"
PLATFORM="${SMOKE_PLATFORM:-linux/amd64}"
TIMEOUT="${SMOKE_TIMEOUT:-180}"
LABEL="titiler-openeo-smoke=$$"
# The app needs a STAC API and a store to start; no check below connects to them.
APP_ENV=(-e TITILER_OPENEO_STAC_API_URL=https://stac.invalid -e TITILER_OPENEO_STORE_URL=/tmp/services.json)

PASS=0
FAILED=()
trap 'docker ps -aq --filter "label=$LABEL" | xargs docker rm -f >/dev/null 2>&1 || true' EXIT

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull -q --platform "$PLATFORM" "$IMAGE" >/dev/null

# check <name> <function>: the function's exit status is the verdict; its output
# is shown only on failure.
check() {
  local out
  if out="$("$2" 2>&1)"; then
    PASS=$((PASS + 1))
    echo "ok:   $1"
  else
    FAILED+=("$1")
    echo "FAIL: $1"
    tail -20 <<<"$out" | sed 's/^/      /'
  fi
}

# Python from stdin, inside the image, as the image's user.
pyrun() {
  docker run --rm -i --platform "$PLATFORM" --label "$LABEL" "${APP_ENV[@]}" "$@" --entrypoint python "$IMAGE" -
}

# start <docker run args...>: run detached with :80 published, wait for /healthz,
# print the base URL.
start() {
  local id port i
  id="$(docker run -d --platform "$PLATFORM" --label "$LABEL" -p 127.0.0.1::80 "${APP_ENV[@]}" "$@")"
  port="$(docker port "$id" 80/tcp | head -1 | sed 's/.*://')"
  for ((i = 0; i < TIMEOUT; i += 2)); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$port/healthz" 2>/dev/null; then
      echo "http://127.0.0.1:$port"
      return
    fi
    [[ "$(docker inspect -f '{{.State.Running}}' "$id")" == true ]] || break
    sleep 2
  done
  docker logs "$id" 2>&1 | tail -20
  return 1
}

# ---------------------------------------------------------------- contract

c_uvicorn_on_path() {
  docker run --rm --platform "$PLATFORM" --label "$LABEL" --entrypoint uvicorn "$IMAGE" --version
}

c_app_imports() {
  pyrun <<'PY'
from fastapi import FastAPI
from titiler.openeo.main import app

assert isinstance(app, FastAPI), type(app)
paths = {getattr(r, "path", None) for r in app.routes}
assert {"/healthz", "/readyz"} <= paths, paths
PY
}

# The chart can set runAsNonRoot only if the image user is a number.
c_numeric_nonroot_user() {
  local user
  user="$(docker image inspect -f '{{.Config.User}}' "$IMAGE")"
  [[ "$user" == 65532 ]] || { echo "image USER is '$user', want 65532"; return 1; }
  pyrun <<'PY'
import os

assert os.getuid() == 65532, os.getuid()
PY
}

c_writable_dirs() {
  pyrun <<'PY'
import os

for d in ("/tmp", "/data", os.path.expanduser("~")):
    path = os.path.join(d, "smoke")
    with open(path, "w") as f:
        f.write("ok")
    os.remove(path)
PY
}

c_log_config() {
  pyrun <<'PY'
import logging.config

import yaml

with open("/config/log_config.yaml") as f:
    logging.config.dictConfig(yaml.safe_load(f))
PY
}

c_default_cmd_serves() {
  start "$IMAGE" >/dev/null
}

# As the chart runs it: `command: uvicorn` with the deployments' args.
c_chart_cmd_serves() {
  local url
  url="$(start -e WEB_CONCURRENCY=2 -e CPL_TMPDIR=/tmp --entrypoint uvicorn "$IMAGE" \
    titiler.openeo.main:app --host 0.0.0.0 --port 80 --forwarded-allow-ips '*' \
    --log-config /config/log_config.yaml --timeout-keep-alive 600)" || { echo "$url"; return 1; }
  curl -fsS "$url/" | grep -q '"backend_version"'
  # /readyz pings the STAC API, unreachable here: it must answer, not pass.
  curl -sS "$url/readyz" | grep -q '"checks"'
}

c_gdal_config_honoured() {
  pyrun -e GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR <<'PY'
import rasterio

with rasterio.Env():
    got = rasterio.env.get_gdal_config("GDAL_DISABLE_READDIR_ON_OPEN")
    assert got == "EMPTY_DIR", got
PY
}

c_osgeo_absent() {
  pyrun <<'PY'
import importlib.util

assert importlib.util.find_spec("osgeo") is None, "osgeo is installed"
PY
}

# ---------------------------------------------------------------- native libraries
# `import rasterio` succeeds with a broken driver or a missing proj.db, so these
# exercise the libraries the wheels vendor.

n_pyproj() {
  pyrun <<'PY'
from pyproj import Transformer

x, y = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(2.3522, 48.8566)
assert abs(x - 261845.7) < 1.0, x
assert abs(y - 6250564.3) < 1.0, y
PY
}

n_rasterio() {
  pyrun <<'PY'
import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

want = np.arange(64, dtype="uint8").reshape(8, 8)
profile = dict(driver="GTiff", width=8, height=8, count=1, dtype="uint8",
               crs="EPSG:4326", transform=from_origin(0, 8, 1, 1), compress="deflate")
with MemoryFile() as mem:
    with mem.open(**profile) as dst:
        dst.write(want, 1)
    with mem.open() as src:
        assert src.crs.to_epsg() == 4326, src.crs
        np.testing.assert_array_equal(src.read(1), want)
out = np.zeros((8, 8), "uint8")
reproject(want, out, src_transform=profile["transform"], src_crs="EPSG:4326",
          dst_transform=from_origin(0, 893463, 111319, 111319), dst_crs="EPSG:3857",
          resampling=Resampling.nearest)
assert out.any()
PY
}

n_render() {
  pyrun <<'PY'
import numpy as np
from rio_tiler.utils import render

data = np.random.default_rng(0).integers(0, 255, (3, 16, 16), dtype="uint8")
assert render(data, img_format="PNG")[:4] == b"\x89PNG"
assert render(data, img_format="JPEG")[:3] == b"\xff\xd8\xff"
PY
}

n_numexpr() {
  pyrun <<'PY'
import numexpr
import numpy as np

a = np.arange(4.0)
np.testing.assert_allclose(numexpr.evaluate("a * 2 + 1"), a * 2 + 1)
PY
}

n_postgres() {
  pyrun <<'PY'
import psycopg2
import sqlalchemy

assert psycopg2.extensions.libpq_version() >= 90000
engine = sqlalchemy.create_engine("postgresql://u:p@localhost/db")
assert engine.dialect.driver == "psycopg2", engine.dialect.driver
PY
}

n_crypto_and_server() {
  pyrun <<'PY'
import httptools  # noqa: F401  uvicorn[standard]
import uvloop  # noqa: F401
from cryptography.hazmat.primitives import hashes

digest = hashes.Hash(hashes.SHA256())
digest.update(b"smoke")
assert len(digest.finalize()) == 32
PY
}

n_obstore() {
  pyrun <<'PY'
import obstore
from obstore.store import MemoryStore

store = MemoryStore()
obstore.put(store, "a.txt", b"smoke")
assert bytes(obstore.get(store, "a.txt").bytes()) == b"smoke"
PY
}

n_tls_ca_store() {
  pyrun <<'PY'
import ssl

assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0
PY
}

# ---------------------------------------------------------------- run

echo "smoke-testing $IMAGE ($PLATFORM)"
check "uvicorn is on PATH (chart command)" c_uvicorn_on_path
check "titiler.openeo.main:app imports" c_app_imports
check "runs as numeric uid 65532" c_numeric_nonroot_user
check "/tmp, /data and HOME are writable" c_writable_dirs
check "/config/log_config.yaml loads" c_log_config
check "default CMD serves /healthz on :80" c_default_cmd_serves
check "chart command serves /healthz, / and /readyz" c_chart_cmd_serves
check "GDAL config from env is honoured" c_gdal_config_honoured
check "osgeo stays absent" c_osgeo_absent
check "pyproj transforms" n_pyproj
check "rasterio GTiff roundtrip and reproject" n_rasterio
check "rio-tiler renders PNG and JPEG" n_render
check "numexpr evaluates" n_numexpr
check "psycopg2 and SQLAlchemy" n_postgres
check "cryptography, uvloop, httptools" n_crypto_and_server
check "obstore roundtrip" n_obstore
check "TLS CA store is populated" n_tls_ca_store

echo
echo "$PASS passed, ${#FAILED[@]} failed"
((${#FAILED[@]} == 0))
