# ADR 0003 — Service access control (`access` / `configuration.scope`)

- **Status:** Amended (2026-08-11) — see [§7](#7-amendment-2026-08-11--get-servicesservice_id-was-not-a-bug)
- **Date:** 2026-08-07
- **Deciders:** @emmanuelmathot
- **Supersedes / superseded by:** —

---

> **2026-08-11 update:** the openEO PSC chair ([openeo-api#85 discussion](https://github.com/Open-EO/openeo-api/issues/85#issuecomment-5231834617))
> corrected a mistake in this ADR's original decision: `GET /services/{service_id}`
> was never supposed to honor `scope` — the spec's `/services*` control plane is
> unconditionally Bearer-only by design, and only the served instance (`service.url`)
> is meant to vary. The corresponding code change shipped in
> [titiler-openeo#363](https://github.com/sentinel-hub/titiler-openeo/pull/363) has
> been reverted. Sections 1-6 below are left as originally written for the historical
> record of the (partially mistaken) reasoning; **read [§7](#7-amendment-2026-08-11--get-servicesservice_id-was-not-a-bug)
> for what's actually true.**

## 1. Context

### 1.1 The ask

titiler-openeo lets a service owner control who can reach a secondary web service
through `configuration.scope` (`private` / `restricted` / `public`), plus an optional
`authorized_users` list for the `restricted` case. This is documented in
[`docs/src/authorization.md`](../src/authorization.md), declared as an XYZ
service-type configuration setting in
[`titiler/openeo/factory.py`](../../titiler/openeo/factory.py), and enforced by
[`ServiceAuthorizationManager`](../../titiler/openeo/services/auth.py).

Nothing in the openEO API specification corresponds to this. Every back-end that wants
to expose a service without a Bearer token has to invent its own answer, which means no
client can be written against a portable contract. That gap surfaced concretely as
[titiler-openeo#362](https://github.com/sentinel-hub/titiler-openeo/issues/362): a
`scope: "public"` service served tiles anonymously but its metadata endpoint
(`GET /services/{service_id}`) still demanded a Bearer token — the two endpoints
disagreed about what `public` meant, because only one of them consulted the scope.

This ADR records the decision to (a) make titiler-openeo's own enforcement coherent, and
(b) shape a subset of the model — `private` vs `public`, under a new top-level
`access` property — as a proposal for the openEO API spec, via a `service-access`
extension.

### 1.2 What the openEO spec actually says (verified against v1.3.0 / `draft`)

- There is **no** `public`, `visibility`, `scope`, or `access` property on the `service`
  object, nor on jobs, UDPs, or service types. The only boolean on `service` is
  `enabled`, which is on/off, not who-can-see.
- Every `/services*` management endpoint is unconditionally `security: [Bearer: []]`.
  The spec knows how to express optional-anonymous access — `GET /service_types` uses
  `security: [{}, Bearer: []]` — it simply never applies that to `/services`.
- The `public` flag some may recall from the openEO Python client is **not** part of the
  spec. It is a VITO/Terrascope convention for user-defined processes, shipped with an
  inline `# TODO: this "public" flag is not standardized yet` and documented as beta.
- `service.url` is explicitly allowed to live outside the API ("Does not necessarily
  need to be located within the API"), so an unauthenticated tile endpoint is not
  spec-violating — it is spec-**silent**.
- The gap is acknowledged and long-standing:
  [Open-EO/openeo-api#85 "Sharing resources"](https://github.com/Open-EO/openeo-api/issues/85)
  has been open since 2018, is labelled `service management` + `extension`, and asks
  verbatim *"Are web services always public?"*.
  [#120](https://github.com/Open-EO/openeo-api/issues/120) lists sharing as an
  extension candidate.
  [#414](https://github.com/Open-EO/openeo-api/issues/414) is the open ticket for
  fine-grained permissions generally. Nothing anywhere proposes a concrete model for
  service-level access control — that space is empty.
- openEO has no user directory:
  [#523 "service account / user group concept"](https://github.com/Open-EO/openeo-api/issues/523)
  was closed without a resolution. There is no portable way for one back-end to resolve
  another user's ID, which rules out standardizing `authorized_users` today.
- The only spec-sanctioned "reachable without a Bearer token" primitive is the
  `canonical` link relation (added via
  [#405](https://github.com/Open-EO/openeo-api/issues/405)), currently blessed for
  shared UDPs and job results but not mentioned for services.

### 1.3 Extension mechanism

Official extensions live in-repo at `Open-EO/openeo-api/extensions/*`, each a
`README.md` with inline OpenAPI fragments, a conformance class of the form
`https://api.openeo.org/extensions/{slug}/{semver}`, and the mandatory constraint that
*"Extensions can not change or break existing behavior of the openEO API."* They are
merged to the `draft` branch via ordinary PR review (examples:
[#538](https://github.com/Open-EO/openeo-api/pull/538),
[#471](https://github.com/Open-EO/openeo-api/pull/471),
[#518](https://github.com/Open-EO/openeo-api/pull/518)); the PSC votes on the release
that ships them, not on the extension PR itself
([PSC#37](https://github.com/Open-EO/PSC/issues/37)).

---

## 2. Decision drivers

- **#362 must be fixed regardless of any spec outcome.** A public service that is only
  half-public is a bug in this codebase, not a spec question.
- **Ownership must be enforced.** While tracing the scope logic, `DELETE` and `PATCH`
  on `/services/{service_id}` were found to perform no ownership check at all — any
  authenticated user could delete or rewrite any other user's service. This is a
  security defect, unrelated to the naming question, and is fixed in the same change.
- **A proposal is more likely to be accepted if it asks for less.** `restricted` +
  `authorized_users` cannot be made portable today (no user directory, see #523), so
  bundling it into the spec proposal invites an objection that kills the whole
  proposal. Splitting the portable core (`private`/`public`) from the back-end
  extension (`restricted`) lets the former go upstream now and the latter wait on
  #414.
- **Naming must not collide with existing spec vocabulary.** openEO already uses
  `scope` for OIDC scopes (`GET /credentials/oidc` → `scopes`). Reusing the word for
  access control invites confusion in the same document.
- **Placement must match what `configuration` means in the spec.** `GET /service_types`
  → `configuration` is documented as *service-type-dependent* settings (tile size,
  TileMatrixSet, format, …). Access control applies uniformly across every service
  type, so modelling it as a `configuration` key misrepresents its scope to spec
  readers and to any future generic tooling that only knows to look at `configuration`
  for the current service type's knobs.
- **Zero-downtime migration.** titiler-openeo already has deployments relying on
  `configuration.scope`. The new property must be additive, not a breaking rename.

---

## 3. Options

### Naming and placement

| Option | Description | Assessment |
| --- | --- | --- |
| **A — Keep `configuration.scope`** | No change. Standardize a reserved `configuration.scope` key upstream. | Weakest pitch: misrepresents an API-wide concern as service-type-specific; reviewers will push back on exactly this point. |
| **B — Top-level `scope`** | Promote out of `configuration`, keep the existing name. | Zero renaming churn locally, but collides with OIDC `scopes` terminology in the same spec document. |
| **C — Top-level `access` (chosen)** | Promote out of `configuration`, rename to avoid the collision. | Matches how the spec already separates concerns (e.g. `enabled` is top-level, not inside `configuration`). Requires a local rename, mitigated by keeping `configuration.scope` as a deprecated alias. |

### What to standardize upstream

| Option | Description | Assessment |
| --- | --- | --- |
| **A — Full three-value model as shipped** | Standardize `private` / `restricted` / `public` plus `authorized_users`, matching titiler-openeo today. | Matches the installed base, but `authorized_users` is not portable (#523) — will draw objections and stall the proposal. |
| **B — Portable core now, grants deferred (chosen)** | Standardize only `private` vs `public`. Document `restricted` + ACLs as a titiler-openeo extension beyond the portable core, explicitly deferred to #414. | Smallest normative surface, hardest to object to, leaves room to grow. |
| **C — Three values, ACL as a sub-resource** | Standardize all three, model grants via `/services/{id}/permissions`. | More spec surface and review cycles for a concept (user identity across back-ends) the spec doesn't yet support. Premature. |

---

## 4. Decision

Adopt **Option C (naming)** and **Option B (scope of standardization)**:

- Introduce a top-level `access` property on the `service` object:
  `"private"` (owner only, matches today's default behaviour when absent) or
  `"public"` (no authentication required).
- Keep `configuration.scope` working as a **deprecated alias**, with `access` taking
  precedence when both are present. No deployment breaks.
- `restricted` and `authorized_users` remain a titiler-openeo-specific extension of the
  portable model — supported locally, not part of the upstream proposal.
- Pursue upstream as an experimental **`service-access` extension**
  (`https://api.openeo.org/extensions/service-access/0.1.0`) against
  `Open-EO/openeo-api`'s `draft` branch, sequenced as: comment on #85 with concrete
  evidence → open a focused issue → socialize at the monthly community call → PR the
  extension folder. Do not implement the `access` rename locally until the upstream
  issue gets a first reaction — if maintainers counter-propose a different name,
  shipping `access` in a release first means carrying two deprecated aliases forever
  instead of one.

Fix the enforcement gaps found along the way immediately and independently of the
naming question:

- ~~`GET /services/{service_id}` now uses `validate_optional` and calls
  `ServiceAuthorizationManager.authorize`, so it agrees with the tile endpoint about
  what `public` means (closes #362).~~ **Reverted, see [§7](#7-amendment-2026-08-11--get-servicesservice_id-was-not-a-bug) — this was wrong.**
- `DELETE /services/{service_id}` and `PATCH /services/{service_id}` now check
  `service["user_id"] == user.user_id` before acting, returning `403` otherwise.
- `ServiceAuthorizationManager.authorize` read `service.get("configuration", {})`,
  which returns `None` — not `{}` — when a client omits `configuration` entirely
  (its Pydantic field default). Fixed to `service.get("configuration") or {}`,
  matching the pattern already used correctly by the tile endpoint.

---

## 5. Consequences

- No breaking change for existing deployments: `access` is additive, `configuration.scope`
  keeps working.
- ~~`GET /services/{service_id}` and the XYZ tile endpoint now enforce the same policy;
  a client can no longer observe one as public and the other as private.~~ **Superseded
  by [§7](#7-amendment-2026-08-11--get-servicesservice_id-was-not-a-bug): they are
  supposed to differ.**
- Cross-user `DELETE`/`PATCH` on a service now correctly returns `403` instead of
  succeeding. This is a behaviour change for any deployment that was relying on the
  absence of the check (unlikely, and not a supported use case), so it ships flagged
  as a security fix rather than a routine feature patch.
- The `default: "public"` on the XYZ service-type `scope` setting
  ([`factory.py`](../../titiler/openeo/factory.py)) is left as-is for now — flipping it
  is a separate, deployment-visible behaviour change and is out of scope for this ADR.
  It contradicts this project's own documented best practice
  ("Use `private` scope by default",
  [`docs/src/authorization.md`](../src/authorization.md)) and should be revisited
  under a settings-controlled default in a future change.
- The upstream proposal, if accepted, gives every openEO back-end — not just
  titiler-openeo — a portable way to say "this service needs no token," which is a
  prerequisite for non-openEO clients (QGIS, Leaflet, a browser) to consume services
  written against the spec rather than against one implementation's conventions.

---

## 6. References

- [titiler-openeo#362](https://github.com/sentinel-hub/titiler-openeo/issues/362) —
  the bug that started this investigation.
- [Open-EO/openeo-api#85](https://github.com/Open-EO/openeo-api/issues/85) — "Sharing
  resources," open since 2018, asks "Are web services always public?"
- [Open-EO/openeo-api#120](https://github.com/Open-EO/openeo-api/issues/120) —
  "Extensibility of the API," lists sharing as an extension candidate.
- [Open-EO/openeo-api#414](https://github.com/Open-EO/openeo-api/issues/414) — "Allow
  more fine-grained access permissions."
- [Open-EO/openeo-api#405](https://github.com/Open-EO/openeo-api/issues/405) — the
  `canonical` link relation for shared resources without Bearer auth.
- [Open-EO/openeo-api#523](https://github.com/Open-EO/openeo-api/issues/523) — "service
  account / user group concept," closed without resolution; why `authorized_users`
  cannot be standardized today.
- [Open-EO/PSC#37](https://github.com/Open-EO/PSC/issues/37) — release vote precedent
  showing extensions are merged by PR review, voted on only at release time.
- Existing openEO API extensions for structural precedent: `federation`,
  `commercial-data`, `processing-parameters`, `remote-process-definition`,
  `workspaces` — `Open-EO/openeo-api/extensions/`.

---

## 7. Amendment (2026-08-11) — `GET /services/{service_id}` was not a bug

Section 1.2 correctly established that every `/services*` endpoint is unconditionally
`security: [Bearer: []]`, with no anonymous variant. Section 4's decision then treated
that fact as a **gap** — an inconsistency between the metadata endpoint and the tile
endpoint that titiler-openeo should paper over locally, ahead of any spec change, by
making `GET /services/{service_id}` also honor `scope`. That was wrong.

[m-mohr (openEO PSC chair) explained why](https://github.com/Open-EO/openeo-api/issues/85#issuecomment-5231834617),
in response to the comment posted from this ADR's findings:

> The `/services` endpoints are private, but the actual web services (e.g. the titiler
> endpoint) get expose via the `url` property and those can be public or restricted,
> depends on how the API implementation needs it. The openEO API doesn't specify how
> you expose the services you link to via the `url` property.

The spec draws a deliberate line between two things this ADR had conflated:

- **Control plane** — `/services/{service_id}` and its siblings. This is the API
  implementation's own bookkeeping about a service (who owns it, its process graph,
  its budget, ...). Always Bearer-protected, no exceptions. Not meant to vary with
  anything the owner configures.
- **Data plane** — `service.url`, the actual instance a non-openEO client (QGIS,
  Leaflet, a browser) consumes. The spec explicitly disclaims any control over this
  ("does not necessarily need to be located within the API") — its auth is entirely
  the back-end's call.

titiler-openeo's XYZ tile endpoint (`GET /services/xyz/{service_id}/tiles/{z}/{x}/{y}`,
what `service.url` points to) is the data plane, and it already correctly implemented
`scope`-based public/private access **before this investigation started** — that part
was never broken. [titiler-openeo#362](https://github.com/sentinel-hub/titiler-openeo/issues/362)
observed that the control-plane endpoint didn't match the data-plane endpoint's
openness, and this ADR misdiagnosed that mismatch as the defect. It is the intended
design: the two are supposed to differ.

### What changed as a result

- [titiler-openeo#363](https://github.com/sentinel-hub/titiler-openeo/pull/363)'s
  change to `GET /services/{service_id}` (using `validate_optional` and calling
  `ServiceAuthorizationManager.authorize`) has been **reverted**. The endpoint is back
  to unconditional `Depends(self.auth.validate)`, matching the spec and every sibling
  `/services*` endpoint.
- The other two things that PR did are **unaffected and were kept**: the ownership
  checks on `DELETE`/`PATCH /services/{service_id}` (a real security fix, orthogonal to
  the scope question) and the `configuration.get(...) or {}` `None`-safety fix in
  `ServiceAuthorizationManager.authorize` (still needed by the tile endpoint's call).
- [`docs/src/authorization.md`](../src/authorization.md) has been corrected to state
  plainly that `scope` governs the served tile instance only, never the metadata
  endpoint.

### What this means for the upstream proposal

The core premise of Sections 3-4 — "propose a top-level `access` property so
`GET /services/{service_id}` can become optionally anonymous" — no longer holds; that
would ask the spec to bless exactly the behavior m-mohr says is a misunderstanding of
its own design. The narrower, still-open question is whether *anonymous discovery of a
public service's metadata* (title, description, extent — the actual thing a client like
openEO Studio wants when rendering an unauthenticated preview) has any sanctioned answer
today, given that `/services/{service_id}` is off the table for that by design. That
question has been put back to m-mohr on #85, asking specifically for guidance on how
scope should be managed across a service's lifecycle (creation, listing, consumption)
rather than re-proposing a specific mechanism. The draft extension issue prepared
earlier (`docs/adr/upstream/openeo-api-service-access-issue.md`) has been withdrawn
pending that answer — it proposed changing `/services/{service_id}`'s security
definition, which is the part now known to be wrong.
