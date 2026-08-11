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

There is no enable flag. Signing activates when `TITILER_OPENEO_STAC_API_URL`
names `planetarycomputer.microsoft.com`, and the rule only ever fires for hrefs
on `*.blob.core.windows.net`. Any other catalogue, and any asset served from
somewhere else, is untouched.

At startup the log says so:

```text
Asset href signing enabled for https://planetarycomputer.microsoft.com/api/stac/v1: \.blob\.core\.windows\.net$
```

If you point at a Planetary Computer **mirror on a different hostname**, that
line will be absent and reads will fail with HTTP 409. That is the known cost of
deriving activation instead of configuring it — see
[ADR 0005](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0005-asset-href-signing.md) §3.1.

Tokens are container-scoped, read-only, and last about 45 minutes. One token is
minted per storage container and reused for every asset in it, refreshed five
minutes before it expires.

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
[OpenID Connect](openid-connect.md#microsoft-entra-id) for the setup, including
the single-tenant well-known URL requirement.

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
- [SAR Backscatter](sar-backscatter.md) and
  [Sentinel-2 View/Sun Angle Bands](sentinel2-view-angles.md) — both read
  non-raster assets that are signed by the same mechanism.
