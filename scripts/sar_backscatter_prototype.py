#!/usr/bin/env python
"""Throwaway prototype for openEO ``sar_backscatter`` on Sentinel-1 GRD.

This is **Phase 0** of ``docs/adr/0001-sar-backscatter.md``: a deliberately rough,
single-file implementation whose only job is to turn that ADR's open questions into
measurements. It is not the design, it is not tested, and it must not be evolved into
the implementation.

What it does
============

For one STAC item and one output grid it:

1. resolves the measurement TIFF + ``schema-calibration-<pol>`` + ``schema-noise-<pol>``
   assets,
2. parses the ESA calibration and thermal-noise LUTs,
3. builds **one** inverse map from destination pixel centres back to GRD
   ``(line, pixel)`` with ``GCPTransformer(gcps, tps=True)``,
4. uses that same map to sample the DN (from a decimated read, i.e. the COG overviews)
   *and* to evaluate the LUTs, so radiometry and geometry can never disagree,
5. computes ``(DN^2 - noise) / A^2`` for the requested coefficient.

``rasterio.warp.reproject`` is deliberately **not** used for step 4: it silently ignores
``METHOD=GCP_TPS`` and always runs an order-2 polynomial, which on IW GRDH is a ~25 m RMS
geolocation error that looks like it worked (ADR 1.6b). ``--compare-transformers`` shows
the difference.

It prints the diagnostics the ADR asks for: timings, backscatter statistics in dB, and
the incidence-angle self-consistency check.

Usage
=====

CDSE is the reference catalogue (ADR 1.7) and the default. It needs credentials::

    export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...
    export AWS_S3_ENDPOINT=eodata.dataspace.copernicus.eu
    export AWS_VIRTUAL_HOSTING=FALSE

    uv run python scripts/sar_backscatter_prototype.py

Planetary Computer needs no credentials and is useful for validating the *algorithm*
before CDSE access is sorted out::

    uv run python scripts/sar_backscatter_prototype.py --catalog mspc

Other flags::

    --item <id>              pick a specific item instead of the first match
    --pol vv|vh|hh|hv        polarisation (default: first available)
    --coefficient sigma0-ellipsoid|gamma0-ellipsoid|beta0|null
    --size 512               output raster is size x size
    --no-noise-removal       skip thermal noise subtraction
    --compare-transformers   also warp via reproject() and report the delta

Requires ``obstore`` (or ``boto3``) only when reading ``s3://`` hrefs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import GCPTransformer, from_bounds
from rasterio.warp import reproject

CATALOGS = {
    "cdse": "https://stac.dataspace.copernicus.eu/v1",
    "mspc": "https://planetarycomputer.microsoft.com/api/stac/v1",
    "earthsearch": "https://earth-search.aws.element84.com/v1",
}
COLLECTION = "sentinel-1-grd"
POLARISATIONS = ("vv", "vh", "hh", "hv")


# --------------------------------------------------------------------------- fetching


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310
        return r.read()


def fetch_bytes(href: str) -> bytes:
    """Fetch an asset. ADR 9.1 -- this is the piece that needs a real dependency."""
    parsed = urlparse(href)
    if parsed.scheme in ("http", "https"):
        return _http_get(href)

    if parsed.scheme == "s3":
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
        endpoint = os.environ.get("AWS_S3_ENDPOINT") or os.environ.get(
            "AWS_ENDPOINT_URL"
        )
        if endpoint and not endpoint.startswith("http"):
            endpoint = f"https://{endpoint}"
        try:
            import obstore
            from obstore.store import S3Store

            # obstore rejects endpoint=None, so only pass it when actually set.
            opts = {
                "region": os.environ.get("AWS_REGION", "us-east-1"),
                "request_payer": os.environ.get("AWS_REQUEST_PAYER") == "requester",
                "virtual_hosted_style_request": os.environ.get(
                    "AWS_VIRTUAL_HOSTING", ""
                )
                .upper()
                .startswith("T"),
            }
            if endpoint:
                opts["endpoint"] = endpoint

            if os.environ.get("AWS_PROFILE"):
                # obstore's native auth does NOT read ~/.aws/credentials or
                # AWS_PROFILE -- support was removed upstream in arrow-rs. The
                # documented route is the boto3 credential provider, which is why
                # boto3 is an optional extra. See ADR 7.6 and
                # https://github.com/developmentseed/obstore/issues/571
                import boto3
                from obstore.auth.boto3 import Boto3CredentialProvider

                opts["credential_provider"] = Boto3CredentialProvider(
                    boto3.Session(profile_name=os.environ["AWS_PROFILE"])
                )
            elif os.environ.get("AWS_ACCESS_KEY_ID"):
                opts["access_key_id"] = os.environ["AWS_ACCESS_KEY_ID"]
                opts["secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
            else:
                opts["skip_signature"] = True
            store = S3Store(bucket, **opts)
            return bytes(obstore.get(store, key).bytes())
        except ImportError:
            pass
        try:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint)
            return client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except ImportError as exc:
            raise RuntimeError(
                f"cannot fetch {href}: install `obstore` (preferred) or `boto3`"
            ) from exc

    raise RuntimeError(f"unsupported href scheme: {href}")


_SAS_CACHE: Dict[str, str] = {}


def sign(href: str, catalog: str) -> str:
    """Planetary Computer blobs need a SAS token; it is anonymously mintable."""
    if catalog != "mspc" or "?" in href:
        return href
    parts = urlparse(href)
    account = parts.netloc.split(".")[0]
    container = parts.path.lstrip("/").split("/")[0]
    key = f"{account}/{container}"
    if key not in _SAS_CACHE:
        url = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{key}"
        _SAS_CACHE[key] = json.loads(_http_get(url))["token"]
    return f"{href}?{_SAS_CACHE[key]}"


def gdal_path(href: str) -> str:
    """Translate a STAC href into something rasterio/GDAL can open."""
    parsed = urlparse(href)
    if parsed.scheme == "s3":
        return f"/vsis3/{parsed.netloc}{parsed.path}"
    if parsed.scheme in ("http", "https"):
        return f"/vsicurl/{href}"
    return href


# --------------------------------------------------------------------------- LUTs


@dataclass
class Grid2D:
    """A LUT sampled on a rectilinear (line x pixel) grid."""

    lines: np.ndarray  # (nl,)
    pixels: np.ndarray  # (np,)
    values: Dict[str, np.ndarray]  # name -> (nl, np)

    def interp(self, name: str, q_line: np.ndarray, q_pixel: np.ndarray) -> np.ndarray:
        """Bilinear interpolation at arbitrary (line, pixel), clamped at the edges."""
        v = self.values[name]
        li = np.clip(np.searchsorted(self.lines, q_line) - 1, 0, len(self.lines) - 2)
        pi = np.clip(np.searchsorted(self.pixels, q_pixel) - 1, 0, len(self.pixels) - 2)

        l0, l1 = self.lines[li], self.lines[li + 1]
        p0, p1 = self.pixels[pi], self.pixels[pi + 1]
        tl = np.clip((q_line - l0) / np.maximum(l1 - l0, 1e-9), 0, 1)
        tp = np.clip((q_pixel - p0) / np.maximum(p1 - p0, 1e-9), 0, 1)

        v00, v01 = v[li, pi], v[li, pi + 1]
        v10, v11 = v[li + 1, pi], v[li + 1, pi + 1]
        return (
            v00 * (1 - tl) * (1 - tp)
            + v01 * (1 - tl) * tp
            + v10 * tl * (1 - tp)
            + v11 * tl * tp
        )


def bilinear_sample(src: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Bilinear sample ``src`` at fractional (row, col). Out-of-bounds -> 0."""
    h, w = src.shape
    r0 = np.floor(rows).astype(np.intp)
    c0 = np.floor(cols).astype(np.intp)
    inside = (r0 >= 0) & (r0 < h - 1) & (c0 >= 0) & (c0 < w - 1)

    r0c = np.clip(r0, 0, h - 2)
    c0c = np.clip(c0, 0, w - 2)
    fr = rows - r0c
    fc = cols - c0c

    v00 = src[r0c, c0c]
    v01 = src[r0c, c0c + 1]
    v10 = src[r0c + 1, c0c]
    v11 = src[r0c + 1, c0c + 1]
    out = (
        v00 * (1 - fr) * (1 - fc)
        + v01 * (1 - fr) * fc
        + v10 * fr * (1 - fc)
        + v11 * fr * fc
    )
    return np.where(inside, out, 0.0)


def _text(el: ET.Element, tag: str) -> str:
    """Required child text, or a clear error naming the missing element."""
    value = el.findtext(tag)
    if value is None:
        raise RuntimeError(f"annotation element is missing <{tag}>")
    return value


def _floats(el: ET.Element, tag: str) -> np.ndarray:
    return np.array(_text(el, tag).split(), dtype="f8")


def parse_calibration(xml: bytes) -> Grid2D:
    root = ET.fromstring(xml)
    vectors = root.findall("calibrationVectorList/calibrationVector")
    if not vectors:
        raise RuntimeError("no calibrationVector found")

    lines = np.array([float(_text(v, "line")) for v in vectors])
    pixels = _floats(vectors[0], "pixel")
    values = {}
    for name in ("sigmaNought", "betaNought", "gamma", "dn"):
        rows = []
        for v in vectors:
            row = _floats(v, name)
            if row.shape != pixels.shape:
                raise RuntimeError(f"ragged calibration grid in {name}")
            rows.append(row)
        values[name] = np.vstack(rows)
    return Grid2D(lines, pixels, values)


@dataclass
class NoiseLUT:
    rng: Grid2D
    azimuth_blocks: List[dict]

    def evaluate(self, q_line: np.ndarray, q_pixel: np.ndarray) -> np.ndarray:
        """Full thermal noise in DN^2: range LUT scaled by the per-swath azimuth LUT."""
        noise = self.rng.interp("noiseRangeLut", q_line, q_pixel)
        if not self.azimuth_blocks:
            return noise

        scale = np.ones_like(noise)
        for blk in self.azimuth_blocks:
            sel = (
                (q_line >= blk["first_line"])
                & (q_line <= blk["last_line"])
                & (q_pixel >= blk["first_sample"])
                & (q_pixel <= blk["last_sample"])
            )
            if not sel.any():
                continue
            scale[sel] = np.interp(q_line[sel], blk["lines"], blk["lut"])
        return noise * scale


def parse_noise(xml: bytes) -> NoiseLUT:
    """Parse the thermal-noise annotation, handling both ESA schema generations.

    IPF >= 2.90 (from 2018-03) uses ``noiseRangeVectorList`` + ``noiseAzimuthVectorList``.
    IPF < 2.90 uses a single ``noiseVectorList`` with a ``noiseLut`` and no azimuth
    descalloping vector. Both occur in the CDSE archive; see ADR 1.6g.
    """
    root = ET.fromstring(xml)
    vectors = root.findall("noiseRangeVectorList/noiseRangeVector")
    lut_tag = "noiseRangeLut"
    if not vectors:
        # Legacy (IPF < 2.90) layout.
        vectors = root.findall("noiseVectorList/noiseVector")
        lut_tag = "noiseLut"
    if not vectors:
        raise RuntimeError(
            "noise annotation has neither noiseRangeVectorList nor noiseVectorList"
        )

    lines = np.array([float(_text(v, "line")) for v in vectors])
    pixels = _floats(vectors[0], "pixel")
    rows = [_floats(v, lut_tag) for v in vectors]
    rng = Grid2D(lines, pixels, {"noiseRangeLut": np.vstack(rows)})

    blocks: List[dict] = []
    for v in root.findall("noiseAzimuthVectorList/noiseAzimuthVector"):
        lut_text = v.findtext("noiseAzimuthLut")
        if not lut_text:
            continue
        blocks.append(
            {
                "first_line": float(_text(v, "firstAzimuthLine")),
                "last_line": float(_text(v, "lastAzimuthLine")),
                "first_sample": float(_text(v, "firstRangeSample")),
                "last_sample": float(_text(v, "lastRangeSample")),
                "lines": _floats(v, "line"),
                "lut": np.array(lut_text.split(), dtype="f8"),
            }
        )
    return NoiseLUT(rng, blocks)


# --------------------------------------------------------------------------- STAC


def search_item(catalog: str, item_id: Optional[str]) -> dict:
    base = CATALOGS[catalog]
    if item_id:
        url = f"{base}/collections/{COLLECTION}/items/{item_id}"
        return json.loads(_http_get(url))
    url = f"{base}/collections/{COLLECTION}/items?limit=1"
    feats = json.loads(_http_get(url))["features"]
    if not feats:
        raise RuntimeError(f"no items in {catalog}/{COLLECTION}")
    return feats[0]


def resolve_assets(
    item: dict, pol: Optional[str], catalog: str
) -> Tuple[str, str, str]:
    """Return (measurement, calibration, noise) hrefs for one polarisation."""
    assets = item["assets"]
    available = [p for p in POLARISATIONS if p in assets]
    if not available:
        raise RuntimeError(f"no measurement assets; keys = {sorted(assets)}")
    pol = pol or available[0]
    if pol not in assets:
        raise RuntimeError(f"polarisation {pol!r} absent; available: {available}")

    def href(key: str) -> str:
        a = assets[key]
        # CDSE puts the s3 href first with an `alternate.https`; keep the s3 one so
        # the fetcher and GDAL both see the same object (ADR 7.6).
        return a["href"]

    cal_key, noise_key = f"schema-calibration-{pol}", f"schema-noise-{pol}"
    for key in (cal_key, noise_key):
        if key not in assets:
            raise RuntimeError(
                f"{catalog} item lacks {key!r} -- this catalogue cannot support "
                f"sar_backscatter (ADR 1.7). keys = {sorted(assets)}"
            )
    return href(pol), href(cal_key), href(noise_key)


def default_bbox(item: dict, span: float = 0.35) -> Tuple[float, float, float, float]:
    """A small window at the footprint centroid."""
    w, s, e, n = item["bbox"][:4]
    cx, cy = (w + e) / 2, (s + n) / 2
    half_x = span / max(np.cos(np.radians(cy)), 0.05)
    return (cx - half_x, cy - span, cx + half_x, cy + span)


# --------------------------------------------------------------------------- core


COEFFICIENTS = {
    "sigma0-ellipsoid": "sigmaNought",
    "gamma0-ellipsoid": "gamma",
    "beta0": "betaNought",
    "null": None,
}


def run(args: argparse.Namespace) -> int:
    t_start = time.time()
    item = search_item(args.catalog, args.item)
    props = item["properties"]
    print(f"item         {item['id']}")
    print(
        f"             mode={props.get('sar:instrument_mode')} "
        f"product:type={props.get('product:type')} "
        f"datetime={props.get('datetime')}"
    )

    meas_href, cal_href, noise_href = (
        sign(h, args.catalog) for h in resolve_assets(item, args.pol, args.catalog)
    )
    pol = args.pol or next(p for p in POLARISATIONS if p in item["assets"])
    print(f"polarisation {pol}")
    print(f"measurement  {meas_href}")

    # ADR 1.7: item/asset proj:* is untrustworthy for SAR geometry. Say so loudly.
    if any(k.startswith("proj:") for k in props):
        print(
            f"WARNING      item advertises {[k for k in props if k.startswith('proj:')]}"
            " -- IGNORED, SAR geometry has no valid affine (ADR 1.7)"
        )

    t0 = time.time()
    cal = parse_calibration(fetch_bytes(cal_href))
    noise = parse_noise(fetch_bytes(noise_href)) if args.noise_removal else None
    t_lut = time.time() - t0
    print(
        f"LUTs         calibration {cal.values['sigmaNought'].shape} "
        f"({len(cal.lines)} vectors x {len(cal.pixels)} samples), "
        f"fetch+parse {t_lut:.2f}s"
    )

    bbox = tuple(args.bbox) if args.bbox else default_bbox(item)
    dst_transform = from_bounds(*bbox, args.size, args.size)
    print(
        f"dst grid     {args.size}x{args.size} EPSG:4326 bbox={tuple(round(b, 4) for b in bbox)}"
    )

    env = rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR")
    with env, rasterio.open(gdal_path(meas_href)) as src:
        gcps, gcp_crs = src.gcps
        if not gcps:
            raise RuntimeError("measurement TIFF has no GCPs -- unexpected for GRD")
        print(
            f"source       {src.width}x{src.height} {src.dtypes[0]} "
            f"gcps={len(gcps)} crs={src.crs} overviews={src.overviews(1)}"
        )

        # ONE inverse map, used for both the DN sampling and the LUT evaluation, so the
        # two can never disagree. rasterio.warp.reproject cannot be used here: it
        # silently ignores METHOD=GCP_TPS and falls back to an order-2 polynomial
        # (see ADR 1.6b) -- verified by reproject(METHOD=GCP_TPS) being bit-identical
        # to reproject(METHOD=GCP_POLYNOMIAL) and to MAX_GCP_ORDER=2.
        t0 = time.time()
        rows, cols = np.mgrid[0 : args.size, 0 : args.size]
        xs, ys = rasterio.transform.xy(dst_transform, rows.ravel(), cols.ravel())
        with GCPTransformer(gcps, tps=True) as tr:
            q_line, q_pixel = tr.rowcol(xs, ys, op=lambda v: v)
        q_line = np.asarray(q_line, dtype="f8").reshape(rows.shape)
        q_pixel = np.asarray(q_pixel, dtype="f8").reshape(rows.shape)
        t_map = time.time() - t0

        # Source window covering the mapped footprint, with a 2 px margin.
        finite = np.isfinite(q_line) & np.isfinite(q_pixel)
        if not finite.any():
            raise RuntimeError("destination grid maps entirely outside the product")
        r_lo = max(int(np.floor(q_line[finite].min())) - 2, 0)
        r_hi = min(int(np.ceil(q_line[finite].max())) + 2, src.height)
        c_lo = max(int(np.floor(q_pixel[finite].min())) - 2, 0)
        c_hi = min(int(np.ceil(q_pixel[finite].max())) + 2, src.width)
        if r_hi <= r_lo or c_hi <= c_lo:
            raise RuntimeError("destination grid does not overlap the product")

        # Decimate to roughly the destination sampling; GDAL serves this from the COG
        # overviews. ADR 1.6c measured the amplitude-vs-power averaging bias at
        # <= 0.06 dB for GRD, so this is radiometrically safe.
        win = rasterio.windows.Window(c_lo, r_lo, c_hi - c_lo, r_hi - r_lo)
        decim = max(1, int(min((r_hi - r_lo) / args.size, (c_hi - c_lo) / args.size)))
        out_h = max(1, (r_hi - r_lo) // decim)
        out_w = max(1, (c_hi - c_lo) // decim)

        t0 = time.time()
        patch = src.read(
            1, window=win, out_shape=(out_h, out_w), resampling=Resampling.average
        ).astype("f8")
        t_read = time.time() - t0

        sy = out_h / (r_hi - r_lo)
        sx = out_w / (c_hi - c_lo)
        dn = bilinear_sample(patch, (q_line - r_lo) * sy, (q_pixel - c_lo) * sx)
        print(
            f"read         window {c_hi - c_lo}x{r_hi - r_lo} -> {out_w}x{out_h} "
            f"(decim x{decim}) in {t_read:.2f}s; inverse TPS map {t_map:.2f}s"
        )
        print(f"resample     {(dn > 0).mean() * 100:.1f}% valid")

        if args.compare_transformers:
            alt = np.zeros((args.size, args.size), dtype="float32")
            reproject(
                rasterio.band(src, 1),
                alt,
                gcps=gcps,
                src_crs=gcp_crs,
                dst_transform=dst_transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.bilinear,
                METHOD="GCP_TPS",
            )
            m = (dn > 0) & (alt > 0)
            if m.any():
                d = np.abs(dn[m] - alt[m])
                print(
                    f"             ADR 1.6b: TPS inverse map vs reproject(METHOD=GCP_TPS) "
                    f"-- mean|dDN|={d.mean():.1f} p99={np.percentile(d, 99):.1f} "
                    "(large => reproject really is on the order-2 polynomial)"
                )

    power = dn.astype("f8") ** 2
    if noise is not None:
        eta = noise.evaluate(q_line, q_pixel)
        negative = (power - eta) < 0
        power = np.maximum(power - eta, 0.0)
        print(
            f"noise        subtracted; {negative[dn > 0].mean() * 100:.2f}% of valid "
            "pixels clamped at 0"
        )

    lut_name = COEFFICIENTS[args.coefficient]
    if lut_name is None:
        result = power
    else:
        a = cal.interp(lut_name, q_line, q_pixel)
        result = power / (a**2)

    valid = (dn > 0) & np.isfinite(result) & (result > 0)
    if not valid.any():
        print("\nno valid pixels in this window -- try a different --bbox")
        return 1

    db = 10 * np.log10(result[valid])
    print(f"\n{args.coefficient}  (linear -> dB over {valid.sum()} valid px)")
    for label, v in (
        ("p05", np.percentile(db, 5)),
        ("median", np.median(db)),
        ("mean", db.mean()),
        ("p95", np.percentile(db, 95)),
    ):
        print(f"   {label:8s} {v:8.2f} dB")

    # ADR 1.6d: the three LUTs encode the ellipsoid incidence angle redundantly.
    a_s = cal.interp("sigmaNought", q_line, q_pixel)
    a_g = cal.interp("gamma", q_line, q_pixel)
    a_b = cal.interp("betaNought", q_line, q_pixel)
    th_sg = np.degrees(np.arccos(np.clip((a_g / a_s) ** 2, -1, 1)))
    th_bs = np.degrees(np.arcsin(np.clip((a_b / a_s) ** 2, -1, 1)))
    delta = np.nanmax(np.abs(th_sg - th_bs))
    print(
        f"\nincidence    {np.nanmin(th_sg):.2f}-{np.nanmax(th_sg):.2f} deg, "
        f"sigma/gamma vs beta/sigma agree to {delta:.5f} deg (ADR 1.6d)"
    )
    print(f"\ntotal        {time.time() - t_start:.1f}s")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--catalog", choices=sorted(CATALOGS), default="cdse")
    p.add_argument("--item", help="STAC item id (default: first in collection)")
    p.add_argument("--pol", choices=POLARISATIONS)
    p.add_argument(
        "--coefficient", choices=sorted(COEFFICIENTS), default="sigma0-ellipsoid"
    )
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"))
    p.add_argument("--no-noise-removal", dest="noise_removal", action="store_false")
    p.add_argument("--compare-transformers", action="store_true")
    args = p.parse_args()
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - prototype: report and exit
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
