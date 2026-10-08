# Microsoft Planetary Computer

titiler-openeo runs against [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
without credentials. Point it at the Planetary Computer STAC API and reads work.

```bash
cp .env.planetarycomputer .env
docker compose up
```

## Why this needs anything special

Planetary Computer's assets live in private Azure Blob Storage containers. An
unsigned read fails:

```console
$ curl -sI "https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/.../B04.tif"
HTTP/1.1 409 Public access is not permitted on this storage account.
```

Access is granted by a short-lived
[Shared Access Signature](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
(SAS) appended to the href as a query string. Unlike the `AWS_*` variables that
serve the other catalogues, the credential belongs to the *href*, not to the
process, so no environment variable can express it.

titiler-openeo mints those tokens for you. Every asset href the read path opens
is signed on the way through, including the non-raster annotation assets that
Sentinel-1 calibration bands and Sentinel-2 view/sun angle bands are derived
from.

## How signing switches on

Set the signer your catalogue needs:

```bash
TITILER_OPENEO_ASSET_SIGNER=planetary-computer
```

Signing is off unless you set it. Once set, the signer only ever fires for hrefs
on `*.blob.core.windows.net`; any asset served from somewhere else is untouched,
including Planetary Computer's own `tilejson` and `rendered_preview`, which come
from the API host.

At startup the log says so:

```text
Asset href signing enabled for https://planetarycomputer.microsoft.com/api/stac/v1: planetary-computer
```

If that line is absent and your reads fail with HTTP 409, the variable is not
set. A mistyped value fails loudly at first use rather than reading as "signing
off".

> **Changed in 0.18.0.** Earlier versions had no setting: signing switched on by
> itself when `TITILER_OPENEO_STAC_API_URL` named
> `planetarycomputer.microsoft.com`. That put a cloud provider's hostname in the
> application's decision logic, and it left Planetary Computer **mirrors on other
> hostnames** with no way to turn signing on at all
> ([#377](https://github.com/sentinel-hub/titiler-openeo/issues/377)).
> Deployments upgrading from 0.17.x must now set the variable above — see
> [ADR 0005](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0005-asset-href-signing.md) §2.3.

Tokens are container-scoped, read-only, and last about 45 minutes. One token is
minted per storage container and reused for every asset in it, refreshed five
minutes before it expires.

## Sentinel-2 L2A reflectance

Planetary Computer's `sentinel-2-l2a` has no scale/offset in STAC, and its COG
headers have scale 1 and offset 0 ([microsoft/PlanetaryComputer#134](https://github.com/microsoft/PlanetaryComputer/issues/134)).
The values are only in each product's `MTD_MSIL2A.xml` (asset
`product-metadata`): `BOA_QUANTIFICATION_VALUE` 10000, and `BOA_ADD_OFFSET`
-1000 from processing baseline 04.00 (2022-01-25), none before.

The `sentinel2-boa` scale/offset source reads that file for each product, so
B01–B12 are 0–1 reflectance for all baselines, and `AOT`/`WVP` are physical
values. `SCL` does not change. Set it as the only source for the collection:

```bash
TITILER_OPENEO_PROCESSING_SCALE_OFFSET_COLLECTIONS='{"sentinel-2-l2a": ["sentinel2-boa"]}'
```

`.env.planetarycomputer` and `ci/planetarycomputer-values.yaml` set it. The
default order (`stac,sentinel2-boa,cog`) also reaches `sentinel2-boa` here,
because STAC and the COG header have no values. The file is signed like any
other asset and cached for each product. See
[Scale and offset](admin-guide.md#scale-and-offset) in the admin guide.

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `TITILER_OPENEO_PC_SUBSCRIPTION_KEY` | *(empty)* | Optional [subscription key](https://planetarycomputer.microsoft.com/docs/concepts/sas/#rate-limits-and-access-restrictions), sent as `Ocp-Apim-Subscription-Key`. Raises SAS API rate limits. It does not change what a token grants. |
| `TITILER_OPENEO_PC_SAS_URL` | `https://planetarycomputer.microsoft.com/api/sas/v1` | Base URL of the Data Authentication API. |
| `TITILER_OPENEO_PC_EXPIRY_MARGIN` | `300` | Seconds before a token's stated expiry to mint a replacement. |
| `TITILER_OPENEO_PC_TIMEOUT` | `10` | Per-request timeout, in seconds, for minting a token. |

`GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` is **required**, not just a tuning
knob: signed hrefs carry a query string, and a directory listing on every open
is both wrong and expensive. `.env.planetarycomputer` and the Helm values set it.

## User identity with Microsoft Entra ID

Signing is independent of login — Planetary Computer's SAS API is
unauthenticated, and grants every caller the same read access. Adding Microsoft
Entra ID gives your **users** identities, so services and UDPs are theirs. See
[OpenID Connect](openid-connect.md#microsoft-entra-id) for the setup. The
multi-tenant `common` well-known URL lets users sign in with a work, school or
personal Microsoft account; `TITILER_OPENEO_AUTH_OIDC_ALLOWED_TENANTS` limits
the tenants.

## Known limits

- **No per-user data access.** Planetary Computer's SAS API declares no
  authentication and returns an identical token to every caller, so there is no
  entitlement to delegate. Entra identifies your users; it does not change what
  they can read.
- **Cold reads after a token refresh.** GDAL's `/vsicurl/` cache is keyed on the
  full URL, which changes when a token is renewed, so the chunk cache is cold
  for a moment roughly every 40 minutes.
- **Rate limits.** One token per container per 40 minutes is far below any
  documented threshold. Set a subscription key if you run many pods.

## Related

- [ADR 0005 — Asset href signing](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0005-asset-href-signing.md)
- [ADR 0006 — Microsoft Entra ID as an OIDC provider](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0006-microsoft-entra-oidc.md)
- [ADR 0009 — Scale/offset sources and plugins](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0009-scale-offset-sources.md)
- [SAR Backscatter](sar-backscatter.md) and
  [Sentinel-2 View/Sun Angle Bands](sentinel2-view-angles.md) — both read
  non-raster assets that are signed by the same mechanism.
