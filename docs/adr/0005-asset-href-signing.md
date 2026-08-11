# ADR 0005 — Asset href signing: mediated access to credential-gated assets

- **Status:** Proposed
- **Date:** 2026-08-10
- **Deciders:** @emmanuelmathot
- **Supersedes / superseded by:** closes the gap recorded in
  [ADR 0001](0001-sar-backscatter.md) §7.6 and [ADR 0004](0004-sentinel2-view-sun-angle-bands.md) §3.1

---

## 1. Context

Every asset this backend reads today is reached with credentials that come from
the process environment. GDAL resolves `AWS_*` for the raster path;
`ObstoreFetcher` reads the same variables for the non-raster path (ADR 0001
§7.6). This works for CDSE (S3 + static keys) and Earth Search (public HTTPS),
because in both cases the credential is a property of the *deployment*, applied
identically to every href.

Microsoft Planetary Computer (PC) does not fit that model. Its blob assets are
private, and access is granted by a short-lived Shared Access Signature (SAS)
appended to each href as a query string. The credential is therefore a property
of the *href*, minted per storage container and expiring in under an hour. No
environment variable can express it.

This is not a new observation. ADR 0001 §7.6 recorded PC's SAS requirement for
Sentinel-1 annotation XML, and ADR 0004 §3.1 recorded it again for Sentinel-2
`granule-metadata`, both times deferring it: *"a SAS-signing `AssetFetcher` is a
reasonable follow-up but is out of scope here."* Both ADRs left Planetary
Computer **unsupported, not silently wrong**. This ADR removes the deferral.

### 1.1 The two touch points

An audit of `reader.py` finds that every href the read path opens passes through
one helper, which makes this tractable:

- **`_resolve_asset_href`** (`reader.py:200-213`) — the single chokepoint. It
  serves real assets (`_get_asset_info`, line 446), band-source annotation
  assets, and sibling measurement hrefs (`_get_derived_asset_info`, lines 406
  and 409). The band-source `AssetFetcher` and `sar/geocode.get_gcps` both
  receive hrefs that already went through it, so neither needs its own hook.
- **`_item_has_untrustworthy_proj`** (`reader.py:190`) — the one path that opens
  an href without using that helper. It calls `rasterio.open` on
  `asset.get_absolute_href() or asset.href` directly.

The second is also a latent bug independent of this ADR: because it bypasses
`_resolve_asset_href`, it ignores `STAC_ALTERNATE_KEY` and can open a different
href variant than the one whose pixels are read three lines later — exactly the
inconsistency `_resolve_asset_href`'s own docstring warns against. Routing it
through the helper fixes that and picks up signing as a side effect.

### 1.2 Verified evidence (2026-08-10)

Probed live against the Planetary Computer STAC and SAS APIs.

| Claim | Evidence |
| --- | --- |
| Asset hrefs are `https://{account}.blob.core.windows.net/{container}/…` | Sentinel-2 on `sentinel2l2a01/sentinel2-l2`; Sentinel-1 GRD on `sentinel1euwest/s1-grd` |
| Account and container are derivable from the href alone | PC items carry no `msft:*` properties; nothing else is needed to mint a token |
| Not every asset is on blob storage | `tilejson` and `rendered_preview` are served from `planetarycomputer.microsoft.com` and must **not** be signed |
| Tokens are container-scoped, read+list, ~45 minutes | `sr=c`, `sp=rl`, `se` ≈ now + 45 min |
| Two token shapes exist | `GET /api/sas/v1/token/{collection_id}` and `GET /api/sas/v1/token/{account}/{container}`, both returning `{"msft:expiry", "token"}` |
| A per-href sign endpoint exists | `GET /api/sas/v1/sign?href=…` returns `{"msft:expiry", "href"}` — one round-trip **per asset** |
| Sentinel-1 annotation XML is on blob storage too | `schema-calibration-*` and `schema-noise-*` on `sentinel1euwest` — the band-source path needs signed hrefs, confirming ADR 0001 §7.6 |
| **The SAS API is unauthenticated and identity-blind** | Its OpenAPI declares no `securitySchemes` and no `security` on any operation. An anonymous call, a call with a bogus `Authorization: Bearer …`, and a call with a bogus `Ocp-Apim-Subscription-Key` all return HTTP 200 with the same `skoid`, the same `sp=rl`, the same `sr=c` and the same expiry |

The last row is the load-bearing one. It means **there is no per-user
entitlement on the server to delegate to**. Any design that threads the caller's
identity into token minting would, against public PC, produce the same token for
every user.

---

## 2. Decision

Add one narrow protocol and a shipped in-code registry, keyed on an observable
fact of the href — its host. Thread the resulting signer explicitly down the
read path.

### 2.1 Why this is not the plugin system ADR 0002 rejected

[ADR 0002 §2.1](0002-band-sources.md) rules that if out-of-tree extension ever
becomes in-scope, *"the extension point should be the process/reader registry
itself — generic entry-point discovery serving all readers — never a
band-source-specific plugin system. That is the lesson from #281."* PR #281's
defect was that it bound behaviour to collections by **hand-written
configuration**, so the same catalogue served from two deployments could behave
differently.

This design keeps that rule. There is no configuration binding a signer to a
collection. A rule matches on the href's host, which is a fact of the data, and
the registry ships in code. Adding a catalogue means adding a rule and a test
fixture, exactly as `bandsources/sources.py` does for band sources.

### 2.2 The seam

```python
HrefSigner = Callable[[str], str]        # href -> href

@dataclass(frozen=True)
class SignerRule:
    host: "re.Pattern[str]"                          # matched against urlparse(href).netloc
    factory: Callable[[Optional[User]], HrefSigner]

def rules_for_catalogue(stac_api_url: str) -> Tuple[SignerRule, ...]: ...
def get_href_signer(rules, user=None) -> Optional[HrefSigner]: ...
```

`get_href_signer` returns `None` when `rules` is empty. `None` is threaded as
the default everywhere, and `_resolve_asset_href` returns exactly the string it
returns today when it receives `None`. This mirrors
`build_per_request_registry`'s "return the base object unchanged" gate
(`reader_requirements.py:216-217`, ADR 0002 §4 increment 5): the strongest form
of "a deployment that does not need this must produce a byte-identical read" is
no wrapper at all, not a wrapper that happens to be a no-op.

The returned signer leaves any href whose host matches no rule untouched. This
is what keeps PC's own `tilejson` and `rendered_preview` assets (§1.2) unsigned.

### 2.3 Activation is scoped to the configured catalogue

`rules_for_catalogue` returns the PC rule only when
`TITILER_OPENEO_STAC_API_URL` names `planetarycomputer.microsoft.com`, and `()`
otherwise. No new setting is introduced for enablement.

Two alternatives were considered and rejected:

- **Match on host alone, always.** A deployment reading its own Azure blob
  containers would then make an outbound call to `planetarycomputer.microsoft.com`
  carrying its href. Leaking an href to a third party as a side effect of a
  registry default is not acceptable.
- **A dedicated `TITILER_OPENEO_ASSET_SIGNERS` setting.** More explicit, but it
  adds a knob whose only correct value is derivable from a setting the operator
  already provides. Deriving it keeps "point at PC and it works" true.

The trade-off accepted is that the activation rule is implicit. §3 records this.

### 2.4 `PlanetaryComputerSigner`

- Parses `(account, container)` from the blob host and the first path segment.
- Mints via `GET /api/sas/v1/token/{account}/{container}`. The per-href
  `sign?href=` endpoint is rejected: it costs one round-trip per asset, and a
  single mosaic read touches many assets across the same container.
- Sends `Ocp-Apim-Subscription-Key` when `TITILER_OPENEO_PC_SUBSCRIPTION_KEY` is
  set. §1.2 shows this changes nothing observable about the token, but it is the
  documented rate-limit lever and costs one header.
- Caches `(token, expiry)` in a module-level dict behind a `Lock`, refreshing at
  a five-minute safety margin. The lock is required: the read path fans out
  across a `ThreadPoolExecutor`, so several threads can miss the cache at once —
  the same reasoning as `SimpleSTACReader._inverse_map_lock` (ADR 0002 §2.3).
- Returns the href unchanged when it already carries a `sig=` parameter, so
  signing is idempotent and a pre-signed `alternate` href is never corrupted.
- Uses `urllib.request`, like `sar/fetcher._http_get`, so the module adds no
  dependency. The `planetary-computer` package is **not** taken: it pulls
  `requests` into a project that uses `httpx`, `urllib` and `obstore`, to
  replace roughly seventy lines.

The cache is **not** keyed by user. That is correct precisely because §1.2 shows
tokens are identity-blind; a delegated signer must key its own cache by user,
and the code says so at the cache definition.

### 2.5 The per-user seam

`SignerRule.factory` takes the authenticated `User` even though
`PlanetaryComputerSigner` ignores it, and the signer is built per request rather
than once per process. This is deliberate, and it is the one piece of this ADR
built ahead of a shipped consumer.

The justification is that the expensive and risky part is not the signer — it is
the plumbing. Threading a per-request object from the authenticated endpoint,
through `load_collection`, into a lazily-evaluated closure, and across a
`ThreadPoolExecutor` boundary touches six call sites in three modules. A
genuinely delegated backend does exist as a near-term target — Planetary
Computer Pro, and private Azure containers reached by an on-behalf-of token
exchange — and for those the caller's identity does determine access. Building
the channel once, while the read path is already being changed, avoids doing the
same surgery twice.

`contextvars` were considered for this and rejected: `create_tasks` and
`mosaic_reader` submit to a `ThreadPoolExecutor`, which does not propagate
context. This is the same constraint that makes rio-tiler need
`@inherit_rasterio_env`.

### 2.6 Threading

| Site | Change |
| --- | --- |
| `reader.py::_resolve_asset_href` | `signer=None` parameter, applied after the `STAC_ALTERNATE_KEY` branch |
| `reader.py::_item_has_untrustworthy_proj` | `signer=None`; use `_resolve_asset_href` instead of the raw href (§1.1) |
| `reader.py::SimpleSTACReader` | `signer` attrs field, default `None` |
| `reader.py::_estimate_output_dimensions` and its two helpers | `signer=None`, forwarded to the `SimpleSTACReader` constructions |
| `reader.py::_reader` | `kwargs.pop("signer", None)` before `part()` |
| `stacapi.py::LoadCollection`, `LoadStac` | `signer_rules` field; build the signer from `named_parameters["_openeo_user"]` |
| `processes/implementations/io.py::load_url` | process-wide unbound signer — the one path with no user context |
| `main.py` | build the rules once from `backend_settings.stac_api_url` |

`mosaic_reader` and `create_tasks` already forward `**kwargs` to the reader
callable, so no rio-tiler change is needed.

---

## 3. Consequences

**Gained.** Planetary Computer becomes a supported catalogue for both raster
assets and the non-raster assets ADR 0001 §7.6 and ADR 0004 §3.1 recorded as
unreachable, which makes Sentinel-1 calibration/noise bands and Sentinel-2
view/sun angle bands work there. `_item_has_untrustworthy_proj`'s
`STAC_ALTERNATE_KEY` inconsistency is fixed. Deployments not pointing at PC get
`None` and are unchanged.

**Accepted costs.** A new module, and a `signer` parameter on six functions that
did not need one. One outbound dependency on `planetarycomputer.microsoft.com`
in the read path, whose availability now gates reads that would otherwise only
need blob storage. Signed hrefs carry a query string that changes every time the
token is refreshed, so GDAL's `/vsicurl/` chunk cache is keyed on a URL with a
roughly forty-minute lifetime; reads immediately after a refresh start cold.

**Deliberately not done.** Delegated per-user SAS minting, which §1.2 proves is
not expressible against public PC. Signing based on anything other than the
href's host. A configuration file or environment variable binding signers to
collections (ADR 0002 §2.1). Use of the `/api/sas/v1/sign` endpoint. Write or
delete SAS permissions — the minted token is read+list, and nothing in this
backend writes to a catalogue.

### 3.1 Open risks

- **The activation rule is implicit.** A deployment pointing at a PC *mirror* on
  another hostname gets no signing and reads fail with HTTP 409, with nothing in
  the configuration explaining why. Mitigated by logging once, at startup, which
  rules are active. If a second catalogue ever needs signing under a hostname
  that cannot be recognised, §2.3's rejected explicit setting should be
  revisited rather than special-cased.
- **A short read may outlive its token.** The five-minute margin covers ordinary
  tile reads. A long mosaic that expires mid-read raises `RasterioIOError`,
  which `_reader`'s existing ten-attempt retry loop (`reader.py:1286-1301`)
  handles: each attempt reconstructs `SimpleSTACReader`, which re-resolves and
  therefore re-signs every href. The recovery path is real but incidental, so it
  is asserted by a test rather than left to chance.
- **The per-user seam has no consumer.** §2.5 argues the plumbing is worth
  building early. If a delegated backend does not materialise, the `user`
  parameter on `SignerRule.factory` is dead weight — small, but dead. The
  abandon condition is explicit: if no delegated signer exists by the time a
  third catalogue needs signing, collapse `factory` to take no argument.
- **Rate limits are unmeasured.** §1.2 shows a subscription key changes nothing
  about the token itself, but a single probe cannot measure a rate limit. A
  deployment minting one token per container per forty minutes is far below any
  plausible threshold; a deployment that somehow defeats the cache is not. The
  cache is therefore the mechanism that keeps this safe, not an optimisation.

---

## 4. Implementation plan

| # | Increment | Gate / abandon condition |
| --- | --- | --- |
| 1 | This ADR and [ADR 0006](0006-microsoft-entra-oidc.md) | Docs only |
| 2 | `signing.py` and its unit tests, no wiring | Token mint, cache refresh at the margin, host gating, `sig=` idempotence all covered; zero production call sites changed |
| 3 | Thread `signer=None` through `reader.py` | The full suite passes **unchanged** — with `signer=None`, the read must be byte-identical |
| 4 | Bind the user; wire `stacapi.py`, `io.py`, `main.py` | A live PC Sentinel-2 read succeeds; a non-PC catalogue yields `None` from `get_href_signer` |
| 5 | Flavor files and documentation | `cp .env.planetarycomputer .env` gives a working backend from a clean checkout |

### 4.1 Verification

Offline tests reuse the committed PC fixtures
(`tests/fixtures/sentinel2/items/planetary_computer.json`,
`tests/fixtures/sar/items/planetary_computer.json`), which already carry real
blob hrefs, the unsigned `tilejson`/`rendered_preview` assets, and Sentinel-1
annotation XML. The SAS endpoint is mocked with `unittest.mock`, per house
style; `pytest-vcr` is registered but has no cassettes in this repo and is not a
model for new tests.

```bash
uv run pytest --cov=titiler.openeo --cov-report=term-missing
uv run pre-commit run --all-files
```

The decisive checks are that increment 3 changes no test output at all, and that
after increment 4 a PC read which returns HTTP 409 before the change returns
pixels after it.

---

## 5. Related

- [ADR 0001 — SAR backscatter](0001-sar-backscatter.md) §7.6 — the per-catalogue
  credential table, and the first record of PC's SAS gap.
- [ADR 0002 — Band sources](0002-band-sources.md) §2.1 — the rule this design is
  measured against, and the registry idiom it follows.
- [ADR 0004 — Sentinel-2 view/sun angle bands](0004-sentinel2-view-sun-angle-bands.md) §3.1 —
  the same gap, recorded a second time and deferred.
- [ADR 0006 — Microsoft Entra ID as OIDC provider](0006-microsoft-entra-oidc.md) —
  the identity half of the same deployment target.
- [Planetary Computer SAS documentation](https://planetarycomputer.microsoft.com/docs/concepts/sas/).
