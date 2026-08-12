# ADR 0006 — Microsoft Entra ID as an OIDC provider

- **Status:** Proposed
- **Date:** 2026-08-10
- **Deciders:** @emmanuelmathot
- **Supersedes / superseded by:** —

---

## 1. Context

The Planetary Computer deployment target (see [ADR 0005](0005-asset-href-signing.md))
needs user identity from Microsoft Entra ID, the identity platform behind
Azure and Microsoft 365.

This backend already speaks OIDC. `OIDCAuth` (`auth.py:88-272`) fetches a
provider's discovery document, fetches its JWKS, verifies an RS256 signature by
hand with `cryptography`, and builds a `User` from the token payload.
`/credentials/oidc` (`factory.py:273-341`) advertises the provider to openEO
clients with authorization-code+PKCE and device-code grants. A CDSE Keycloak
realm is driven through this path in production today.

So this ADR is not about adding a provider. It is about the distance between
"works against one Keycloak realm" and "works against Entra without failing in
production a few weeks later".

### 1.1 Verified evidence (2026-08-10)

Probed live against `https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration`
and its JWKS endpoint.

| Claim | Evidence | Consequence |
| --- | --- | --- |
| Entra signs with RS256 only | `id_token_signing_alg_values_supported: ["RS256"]` | The existing hand-rolled PKCS1v15+SHA256 verification is the right algorithm |
| Entra publishes six RSA signing keys | `common/discovery/v2.0/keys` returns six `kty: RSA` entries | Key rotation is real and continuous |
| JWK entries carry no `alg` field | Confirmed on all six | `_get_key`'s `kty`-only check is correct; an `alg`-based lookup would fail |
| The `common` endpoint returns a **templated** issuer | `"issuer": "https://login.microsoftonline.com/{tenantid}/v2.0"` | Unusable for `iss` comparison, and invalid in the `/credentials/oidc` response — a single-tenant `wk_url` is required |
| Entra supports the device-code grant | `device_authorization_endpoint` present | The grant list `/credentials/oidc` already hardcodes is accurate |
| `sub` is pairwise | `subject_types_supported: ["pairwise"]` | `sub` is stable per (user, application). Changing the app registration orphans every stored service, which are keyed on `user_id` |
| `preferred_username`, `name`, `email` are all available | `claims_supported` | `name_claim` has a sensible Entra value already |

### 1.2 The gaps

Reading the current implementation against that evidence:

1. **`_jwks_cache` never refreshes** (`auth.py:127-134`). It is populated once
   and held for the life of the process. Entra rotates its six keys
   continuously. When a token arrives signed by a key minted after the cache was
   filled, `_get_key` raises 401 and **every subsequent login fails until the
   process restarts**. This is the blocker, and it is a latent bug for Keycloak
   too — it has simply not been hit yet.
2. **The discovery document is cached forever** (`auth.py:110-118`), with the
   same failure mode if a provider moves its `jwks_uri`.
3. **`iss` is never validated.** `_verify_token` (`auth.py:162-206`) checks the
   signature, the audience and expiry, but never compares the issuer. A token
   from any issuer whose key happens to be in the cached JWKS would pass.
4. **The token header is parsed and discarded** (`auth.py:169-171`). `alg` is
   never checked, and verification is hardcoded to PKCS1v15+SHA256 regardless of
   what the token claims. This is the classic algorithm-confusion shape.
5. **`exp` is optional** (`auth.py:197`): `if payload.get("exp") and …`. A token
   with no `exp` at all validates.
6. **The audience check assumes an ID token** (`auth.py:190-193`): it accepts
   `azp == client_id` or `client_id in aud`. Entra *access* tokens for a custom
   API carry the application ID URI (`api://<client_id>`) in `aud`, not the bare
   client id, and so are rejected.
7. **`user_id` is hardcoded to `sub`** (`auth.py:257`). Given §1.1's pairwise
   note, an operator has no way to pin identity to the tenant-stable `oid`.
8. **`AuthSettings.__init__` clobbers its own argument** (`settings.py:118-121`):
   `kwargs["oidc"] = OIDCConfig()` runs unconditionally, so `AuthSettings(oidc=…)`
   is silently ignored, `self.oidc` is always truthy, and the
   `validate_oidc_config` model validator (`settings.py:123-128`) and the
   `if not settings.oidc` guards in `get_auth` (`auth.py:81`) and
   `__attrs_post_init__` (`auth.py:105`) are all dead code. A deployment with
   `method=oidc` and an unset `wk_url` starts cleanly and fails on the first
   request with an `httpx` error.

---

## 2. Decision

Fix the eight gaps in place. Keep the hand-rolled verification.

### 2.1 Why not adopt a JWT library

`PyJWT[crypto]` or `joserfc` with a `PyJWKClient` would provide rotation,
`iss`/`aud`/`exp`/`nbf`/`alg` validation and non-RSA algorithms for free, and is
the choice most projects should make.

It is not made here because the change it competes with is small and the code it
would replace is tested and working. Every gap in §1.2 is between four and
fifteen lines. Adding a dependency to the `oidc` extra to rewrite a correct RS256
verifier is a larger diff with a wider blast radius than the fixes themselves.

The condition to revisit is explicit: **the first provider this backend must
support that signs with anything other than RS256.** At that point the hand-rolled
path stops being a small amount of correct code and starts being a re-implementation
of a library, and it should be replaced wholesale rather than extended.

### 2.2 The changes

| # | Change | Location |
| --- | --- | --- |
| 1 | On an unknown `kid`, invalidate the JWKS cache and refetch **once**, behind a `Lock` and a cooldown so unknown kids cannot cause a fetch storm | `auth.py::_get_key` |
| 2 | Cache the discovery document with a TTL instead of forever | `auth.py::config` |
| 3 | Validate `iss` against the discovery document's `issuer` | `auth.py::_verify_token` |
| 4 | Require the header's `alg` to be `RS256` before verifying | `auth.py::_verify_token` |
| 5 | Require `exp`; check `nbf` when present | `auth.py::_verify_token` |
| 6 | Accept an operator-supplied audience list alongside `client_id` | `auth.py::_verify_token`, `settings.py::OIDCConfig.audiences` |
| 7 | Take `user_id` from a configurable claim, default `sub` | `auth.py::validate`, `settings.py::OIDCConfig.user_id_claim` |
| 8 | Stop clobbering `oidc`; validate `wk_url` and `client_id` at startup when `method=oidc` | `settings.py::AuthSettings` |

### 2.3 The templated issuer is a startup error, not a runtime one

§1.1 shows the `common` endpoint returns `https://login.microsoftonline.com/{tenantid}/v2.0`.
That string is useless for change 3, and `/credentials/oidc` would advertise it
verbatim to openEO clients as the provider `issuer`.

Rather than substituting the token's `tid` into it — which would accept tokens
from every tenant in the world, the opposite of what an `iss` check is for —
a discovery document whose `issuer` contains `{tenantid}` is rejected with a
message naming the fix: configure a single-tenant `wk_url`,
`https://login.microsoftonline.com/<tenant_id>/v2.0/.well-known/openid-configuration`.

Multi-tenant support is a real feature, and this deliberately does not provide
it. It needs an allow-list of tenant ids, which is a policy decision no default
can make.

### 2.4 `user_id` stability is the operator's problem, and they need the lever

Services, UDPs and tile assignments are all keyed on `User.user_id`
(`services/base.py`). §1.1 shows Entra's `sub` is pairwise: it is stable for a
given user *in a given application registration*, and changes if the operator
re-registers the app. `oid` is stable across applications within a tenant.

Neither is correct in general — `oid` is not unique across tenants, and `sub` is
the specification's own answer — so change 7 supplies a default of `sub` and a
documented lever, rather than choosing for the operator. The documentation
states plainly that changing this setting on a running deployment orphans every
stored service.

---

## 3. Consequences

**Gained.** Entra works, and keeps working across key rotation. `iss`, `alg` and
`exp` are enforced for every provider, not just Entra — Keycloak deployments get
the same hardening. `AuthSettings(oidc=…)` becomes assignable, which makes the
existing settings validators live and lets tests construct real settings objects
instead of `Mock()` (`tests/test_auth.py:23-27`).

**Accepted costs.** Four new validation paths that can reject a token which is
accepted today. The most likely to bite is change 5: any provider issuing tokens
without `exp` now fails. This is intended — such a token is not safely
verifiable — but it is a behaviour change, and it is called out in the release
notes rather than buried.

**Deliberately not done.** A JWT library (§2.1). Multi-tenant Entra (§2.3).
Non-RSA signature algorithms. Token refresh, introspection or revocation
checking. Mapping Entra groups or app roles onto the `roles` field that
`AuthSettings.users` already carries and nothing reads — authorization is
[ADR 0003](0003-service-access-control.md)'s subject, not this one.

### 3.1 Open risks

- **The JWKS refetch is a request-triggered outbound call.** A flood of tokens
  with unknown kids would otherwise become a flood of requests to the provider.
  The cooldown bounds this, but the bound is a constant, not a rate limiter. If
  this ever matters, the correct fix is a background refresh rather than a
  tighter cooldown.
- **`_get_key` and `config` are per-process, unsynchronised caches.** Adding a
  `Lock` fixes the refetch stampede within one worker. Across workers, each
  holds its own cache and refetches independently. This is acceptable at the
  observed key-rotation rate and is not worth a shared cache.
- **Change 6 widens the audience check.** An operator who sets `audiences` to a
  value they do not control weakens validation. The default is an empty list,
  which preserves today's behaviour exactly, and the documentation frames the
  setting as "the application ID URI of your own API", not "audiences to trust".

---

## 4. Implementation plan

Delivered as a single increment — the changes are individually small, share one
test surface, and splitting them would ship a validator in a half-hardened state.

| # | Increment | Gate / abandon condition |
| --- | --- | --- |
| 1 | This ADR | Docs only |
| 2 | Changes 1–8, with tests | An unknown `kid` triggers exactly one refetch and then succeeds; a second unknown `kid` inside the cooldown does not refetch; `iss`, `alg`, `exp` and `aud` each have a rejection test; `/me` is unchanged for a valid token |

### 4.1 Verification

Unit tests follow `tests/test_auth.py`'s existing shape: a hand-built JWT plus
`@patch.object(OIDCAuth, "_get_key")`. Rotation is tested by patching the JWKS
fetch to return different key sets on successive calls and asserting the call
count. Endpoint behaviour uses the `app_with_auth` / `app_no_auth` fixture pair
in `tests/conftest.py`.

```bash
uv run pytest --cov=titiler.openeo --cov-report=term-missing
uv run pre-commit run --all-files
```

Live check against a real tenant: point `TITILER_OPENEO_AUTH_OIDC_WK_URL` at a
single-tenant discovery document, confirm `GET /credentials/oidc` returns a
concrete `issuer` rather than the templated form, and confirm `GET /me` with a
real Entra token returns the expected `user_id`.

---

## 5. Related

- [ADR 0003 — Service access control](0003-service-access-control.md) — what a
  `User` is allowed to do, once this ADR has established who they are.
- [ADR 0005 — Asset href signing](0005-asset-href-signing.md) — the data-access
  half of the same deployment target, and the consumer of the per-user seam.
- [Microsoft identity platform and OpenID Connect](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc).
- `docs/src/openid-connect.md` — the operator-facing configuration guide this ADR
  extends.
