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

Add one narrow protocol and a shipped in-code registry. A deployment names the
signer it needs; ingest stamps that name onto every item; the read path resolves
the name to a signer when it opens an href.

> **Revised 2026-08-14 (issue #377).** As first implemented, §2.2–§2.3 and §2.6
> said something different: activation was **derived** from the catalogue
> hostname, and the resulting signer was **threaded** as a parameter through
> `_reader`, `_get_target_crs_bbox`, `_get_cube_resolutions`,
> `_estimate_output_dimensions` and `SimpleSTACReader`. Review rejected both
> halves — the first put a cloud provider's hostname in the application's
> decision logic, the second spread a deployment concern across the generic read
> path. Those sections now describe the revision; §2.5 records what it cost. §1,
> §2.1 and §2.4 are unchanged: the problem, the "no hand-written per-collection
> configuration" rule and the signer itself all survived.

### 2.1 Why this is not the plugin system ADR 0002 rejected

[ADR 0002 §2.1](0002-band-sources.md) rules that if out-of-tree extension ever
becomes in-scope, *"the extension point should be the process/reader registry
itself — generic entry-point discovery serving all readers — never a
band-source-specific plugin system. That is the lesson from #281."* PR #281's
defect was that it bound behaviour to collections by **hand-written
configuration**, so the same catalogue served from two deployments could behave
differently.

This design keeps that rule. There is no configuration binding a signer to a
*collection*. A deployment names one signer for its whole catalogue, the
registry of implementations ships in code, and each signer decides per href
whether it has anything to add. Adding a catalogue means adding a signer and a
test fixture, exactly as `bandsources/sources.py` does for band sources.

### 2.2 The seam

```python
HrefSigner = Callable[[str], str]        # href -> href

ITEM_SIGNER_KEY = "titiler:sign"         # STAC property carrying the choice
SIGNERS: Dict[str, Callable[[], HrefSigner]]

def stamp_signer_key(item, key) -> item             # at ingest, once per item
def signer_for_item(item) -> Optional[HrefSigner]   # at open, once per reader
def get_signer(key) -> Optional[HrefSigner]         # memoised key -> signer
```

**The item is the channel.** The readers that open hrefs are built at three
fixed points inside the mosaic (`_reader`, `_get_target_crs_bbox`,
`_get_cube_resolutions`) and run on a `ThreadPoolExecutor` the request context
does not reach. rio-tiler creates that pool without `contextvars.copy_context()`
(`rio_tiler/tasks.py`), so a contextvar cannot carry the decision either. The
item already travels to every one of those points, and is the task argument, so
it is the one channel that works without threading a parameter.

**The stamp is a key, never a signed href.** A SAS token minted at ingest would
be reused by every lazily-evaluated task and every retry, so a graph outliving
its ~45-minute token would fail. Resolving the key at open time means the retry
loop in `_reader`, which rebuilds the reader, re-signs (§3.1). It also keeps the
stamp a plain string, which is what lets it survive the `Item.to_dict()` that
`load_stac` performs.

`get_signer` returns `None` for an unstamped item, and `_resolve_asset_href`
then returns exactly the string it returned before signing existed. This mirrors
`build_per_request_registry`'s "return the base object unchanged" gate
(`reader_requirements.py:216-217`, ADR 0002 §4 increment 5): the strongest form
of "a deployment that does not need this must produce a byte-identical read" is
no wrapper at all, not a wrapper that happens to be a no-op.

Each signer leaves any href it has nothing to add to untouched. That is what
keeps PC's own `tilejson` and `rendered_preview` assets (§1.2) unsigned, and it
is why the stamp is applied to every item rather than only to items that look
like they need it — one judgement, in one place.

### 2.3 Activation is configured, not derived

`TITILER_OPENEO_ASSET_SIGNER` names a key of `SIGNERS`; empty — the default —
means no signing. An unknown key raises `SigningError` at first use rather than
reading as "signing off", which would surface only as an opaque HTTP 409.

The original decision derived activation instead: the PC signer switched on when
`TITILER_OPENEO_STAC_API_URL` named `planetarycomputer.microsoft.com`. The
argument was that it "keeps *point at PC and it works* true" without adding a
knob whose only correct value is derivable. Review rejected it (#377) on two
grounds:

- **It is provider knowledge in the application's decision logic.** A hostname
  belonging to one cloud vendor decided application behaviour.
- **It was not actually derivable.** A PC mirror on another hostname could not
  turn signing on at all, and a deployment reading PC-hosted blobs through a
  different catalogue could not either — the cost §3.1 had already recorded as
  an open risk.

The alternative rejected in the original — matching on href host alone, always —
stands rejected, and for the same reason: a deployment reading its own Azure
blob containers must never make an outbound call to
`planetarycomputer.microsoft.com` carrying its href as a side effect of a
registry default. Configuration, not the shipped registry, is what turns a
signer on.

**Migration.** Deployments on 0.17.x that read Planetary Computer must now set
`TITILER_OPENEO_ASSET_SIGNER=planetary-computer`. Without it, reads of private
blob assets fail with HTTP 409. The startup log states which signer is active.

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

### 2.5 The per-user seam, and its removal

The original design threaded the authenticated `User` to a `factory(user)` on
each rule, building the signer per request even though `PlanetaryComputerSigner`
ignores its user. It was the one piece of this ADR built ahead of a shipped
consumer: the argument was that the plumbing, not the signer, is the expensive
part, and that a delegated backend — Planetary Computer Pro, or private Azure
containers reached by an on-behalf-of exchange — would need that channel later.

**That channel is gone.** It was the same threading §2.3's revision removed, and
keeping it would have meant keeping the parameter on every function purely for a
hypothetical consumer. `SIGNERS` maps a key to a signer with no user involved.

The limit this accepts, recorded rather than solved: **signing is now
user-independent.** That is correct for every shipped case, because §1.2 proves
public PC's tokens are identity-blind — anonymous, bearer-token and
subscription-key calls all return the same token, so there is no entitlement to
delegate. A genuinely delegated backend would need the caller's identity inside
the worker thread, which neither an item stamp (it must stay JSON-serialisable)
nor a contextvar (§2.2: the pool does not propagate context) can carry. Designing
that is deferred until such a backend exists, when its real requirements are
known — rather than guessed, which is what the removed seam did.

The token cache is likewise **not** keyed by user, for the same §1.2 reason; a
delegated signer must key its own cache by user, and the code says so at the
cache definition.

### 2.6 Where the decision is applied

| Site | Change |
| --- | --- |
| `signing.py` | `SIGNERS` key→signer registry; `stamp_signer_key` / `signer_for_item`; `get_signer` memoised |
| `settings.py::SigningSettings` | `TITILER_OPENEO_ASSET_SIGNER`, empty by default |
| `stacapi.py::LoadCollection._get_items` | stamps every item — the single funnel for `load_collection` |
| `stacapi.py::LoadStac` | `signer_key` field; stamps its single-item path, which bypasses `_get_items`; passes the key to its `LoadCollection` delegate |
| `reader.py::SimpleSTACReader` | `signer` resolved in `__attrs_post_init__` from the item; `init=False` |
| `reader.py::_resolve_asset_href` | unchanged — still takes the signer, now from `self.signer` |
| `reader.py::_item_has_untrustworthy_proj` | unchanged — uses `_resolve_asset_href` rather than the raw href (§1.1) |
| `processes/implementations/io.py::load_url` | reads the key from settings: no catalogue behind it, so no ingest step to stamp its synthetic item |
| `main.py` | reads the key once from settings and injects it |

`_reader`, `_get_target_crs_bbox`, `_get_cube_resolutions` and
`_estimate_output_dimensions` take **no** signing parameter. That is the point of
the revision.

---

## 3. Consequences

**Gained.** Planetary Computer becomes a supported catalogue for both raster
assets and the non-raster assets ADR 0001 §7.6 and ADR 0004 §3.1 recorded as
unreachable, which makes Sentinel-1 calibration/noise bands and Sentinel-2
view/sun angle bands work there. `_item_has_untrustworthy_proj`'s
`STAC_ALTERNATE_KEY` inconsistency is fixed. Deployments not pointing at PC get
`None` and are unchanged.

**Accepted costs.** A new module, and a convention — a namespaced STAC property
— that a reader must know to look for. One outbound dependency on
`planetarycomputer.microsoft.com` in the read path, whose availability now gates
reads that would otherwise only need blob storage. Signed hrefs carry a query
string that changes every time the token is refreshed, so GDAL's `/vsicurl/`
chunk cache is keyed on a URL with a roughly forty-minute lifetime; reads
immediately after a refresh start cold. Since the revision: one more environment
variable, and a breaking change for 0.17.x deployments that read PC (§2.3).

**Deliberately not done.** Delegated per-user SAS minting, which §1.2 proves is
not expressible against public PC (§2.5). A configuration file or environment
variable binding signers to *collections* (ADR 0002 §2.1) — the setting names one
signer for the deployment, not a mapping. Use of the `/api/sas/v1/sign` endpoint.
Write or delete SAS permissions — the minted token is read+list, and nothing in
this backend writes to a catalogue.

### 3.1 Open risks

- ~~**The activation rule is implicit.**~~ **Resolved by the §2.3 revision.** The
  risk as recorded — a PC *mirror* on another hostname getting no signing, "with
  nothing in the configuration explaining why" — was what #377 called in. There
  is now a setting; the mirror sets it like any other deployment. The startup log
  still names the active signer, and an unknown key now fails loudly instead of
  reading as "off".
- **A short read may outlive its token.** The five-minute margin covers ordinary
  tile reads. A long mosaic that expires mid-read raises `RasterioIOError`,
  which `_reader`'s existing ten-attempt retry loop handles: each attempt
  reconstructs `SimpleSTACReader`, whose `__attrs_post_init__` re-resolves the
  signer and therefore re-signs every href. This is now load-bearing rather than
  incidental — it is the reason §2.2 stamps a key rather than a signed href —
  and is asserted directly by
  `test_every_retry_rebuilds_the_reader_so_an_expired_token_is_reminted`.
- ~~**The per-user seam has no consumer.**~~ **Closed by removing it** (§2.5).
  The abandon condition recorded here — "collapse `factory` to take no argument"
  — is what happened, earlier than anticipated and for an unrelated reason. The
  replacement risk is that signing is user-independent with no channel to make it
  otherwise; §2.5 states what a delegated backend would have to build.
- **Rate limits are unmeasured.** §1.2 shows a subscription key changes nothing
  about the token itself, but a single probe cannot measure a rate limit. A
  deployment minting one token per container per forty minutes is far below any
  plausible threshold; a deployment that somehow defeats the cache is not. The
  cache is therefore the mechanism that keeps this safe, not an optimisation.
- **The stamp is a convention.** Nothing validates that an item carrying
  `titiler:sign` came from this backend's own ingest. A catalogue that published
  the property itself would be believed. The blast radius is bounded by `SIGNERS`
  — an unknown key raises rather than doing anything — so the worst case is a
  catalogue selecting a signer this deployment already ships, which then still
  only signs hrefs it recognises. Worth revisiting if the stamp ever carries
  something richer than a key.

---

## 4. Implementation plan

Kept as the record of how this shipped in 0.17.0. Increments 3 and 4 were undone
by the §2.3/§2.6 revision — the threading they describe no longer exists.

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
