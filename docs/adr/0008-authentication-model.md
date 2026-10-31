# ADR 0008 — Authentication model: methods, credentials and sessions

- **Status:** Proposed
- **Date:** 2026-10-02
- **Deciders:** @emmanuelmathot
- **Contributors:** @lhoupert, @vincentsarago
- **Supersedes / superseded by:** — (expected to supersede PR #415; amends the
  deployment guidance of [ADR 0006](0006-microsoft-entra-oidc.md))

---

## 1. Context

titiler-openeo does not keep its own user repository. Each instance runs
exactly one authentication method. In production, that method is always OIDC
against one external identity provider (IdP), with a client that the IdP owner
controls. Until now this was sufficient. Four problems now show that it is not:

1. **One external client is a single point of failure.** CDSE changed the
   policy of its public client (`cdse-public`) for redirect URIs. Login to
   openeo.ds.io and studio.openeo.ds.io stopped. We did not change anything,
   and we cannot correct it ourselves. EOPF Explorer uses the same client and
   still works, probably because `*.copernicus.eu` is an allowed domain or
   because EOPF Explorer is a registered ecosystem component. That is a
   dependency on a policy we do not see, not a guarantee.
2. **JupyterGIS cannot use our deployments.** Its openEO layer asks for a
   username and a password. It does not support an OIDC login
   ([geojupyter/jupytergis#1904](https://github.com/geojupyter/jupytergis/issues/1904),
   2026-10-01: "most available OpenEO backends only provides OIDC
   connections"). Our deployments do not offer Basic (unless in dev mode), so the login fails.
3. **We cannot count sessions correctly.** Issue #408 asks for accurate
   active-user and session metrics. PR #415 tried to get them from OIDC tokens.
   The PR is on pause, because the review showed that titiler cannot see where a
   session starts (§1.2, gap 7).
4. **The question of compliance.** We assumed that the openEO API requires
   Basic. §1.1 shows that it does not. But our Basic implementation has
   defects of its own.

This ADR also applies to openeo-studio, our web client. It has the same
problem from the client side (§1.2, gap 8).

### 1.1 Verified evidence (2026-10-02)

**openEO API 1.2.0** (`openapi.yaml`, copy in `Open-EO/openeo-test-suite/assets/openeo-api/`):

- Section *Authentication*: "The openEO API offers two forms of authentication
  by default: OpenID Connect (recommended) […] Basic […]. Further
  authentication methods MAY be added by back-ends."
- `GET /credentials/oidc`: "It is highly RECOMMENDED to implement OpenID Connect
  for public services in favor of Basic authentication." The bearer is
  `oidc/<provider id>/<access token>`, where the provider id is the `id` of an
  entry in the response.
- `GET /credentials/basic`: "Checks the credentials provided through HTTP Basic
  Authentication […] and returns an access token for valid credentials." The
  bearer is `basic//<access token>`. "It is RECOMMENDED to implement this
  authentication method for non-public services only."
- **No method is mandatory.** An OIDC-only back-end is compliant. A back-end
  with Basic and OIDC together is also compliant. The spec does not limit the
  number of OIDC providers.

**Live deployments:**

| Deployment | Method | IdP | Advertised client (`default_clients`) | Who controls the client |
| --- | --- | --- | --- | --- |
| local dev | `basic` (app default) or OIDC against a local Keycloak (EOPF fork `docker-compose.yml`) | local Keycloak | `titiler-openeo` | us |
| CDSE, openeo.ds.io | `oidc` | CDSE Keycloak | `cdse-public`; not working anymore | CDSE |
| MPC | `basic` (`charts/ci/planetarycomputer-values.yaml`); Entra ID planned (ADR 0006) | Entra ID (planned) | — | MPC tenant |
| EOPF Explorer | `oidc` | CDSE Keycloak | `cdse-public`, redirect `https://editor.openeo.org/` (live `GET /credentials/oidc`) | CDSE |

**The IdP identifies the user, nothing more.** Titiler does not forward the
user's token to a data service. ADR 0005 §2.5 removed the per-user seam. Thus
the IdP gives us `user_id` (and name and email), and the data access does not
depend on it. This is important for §2: a credential that titiler issues
itself does not lose any data access.

**JupyterGIS** (`packages/base/src/features/layers/openeo/`, main branch):
`addLayerDialog.tsx` has a username/password form. `OpenEOTileLayer.tsx`
`connect()` can also restore a stored bearer of the form
`type/providerId/token` with `setAuthToken`. The Python method
`GISDocument.add_openeo_tile_layer` stores `graph.connection.auth.bearer`.
Thus a notebook user who logs in with OIDC in the Python client can give the
bearer to the layer. But the bearer is an IdP access token, and it expires
after minutes.

### 1.2 The gaps

1. **The Basic "access token" is the credential.** `BasicAuth.login`
   (`auth.py:556-568`) returns `access_token=param`, which is the base64 of
   `user:password`. Each later request sends the password again in
   `basic//…`. The token never expires and cannot be revoked. The spec expects
   the back-end to issue a token.
2. **Basic passwords are in clear text.** `AuthSettings.users`
   (`settings.py:122-127`) has a default user `test`/`test`. The password is
   compared with `!=` (`auth.py:590`). The Helm chart puts the users in a
   ConfigMap (`templates/configmap.yaml:19-20`), not in a Secret.
3. **The chart's Basic users are ignored.** The chart sets
   `TITILER_OPENEO_AUTH_BASIC_USERS` (`templates/deployment.yaml:85`) with a
   list of `{username, password}`. The settings read
   `TITILER_OPENEO_AUTH_USERS` and expect a dict (`admin-guide.md:105`). We
   tested this: with the chart variable set, `AuthSettings().users` is still
   `{'test': {'password': 'test', …}}`. **Each chart deployment with
   `method: basic` accepts `test`/`test`**, whatever the values file says.
   This applies to the MPC values today.
4. **One method per instance.** `get_auth` (`auth.py:125`) builds one `Auth`.
   `factory.py:405-457` registers `/credentials/oidc` *or* `/credentials/basic`.
5. **One OIDC provider, with a fixed id.** The provider `id` is `"oidc"`
   (`factory.py:435`), and `OIDCAuth.validate` rejects any other id
   (`auth.py:455`). Grant types are fixed. One deployment cannot offer two IdPs,
   or two clients of one IdP.
6. **`user_id` has no issuer.** `user_id` is the value of one claim
   (`user_id_claim`, default `sub`). With more than one provider, two users of
   two IdPs can have the same `sub`. Services and UDPs are owned by `user_id`
   (ADR 0003).
7. **Sessions cannot be seen from titiler.** `track_user_login`
   (`services/base.py:289`) runs on each validated request. PR #415
   (`origin/feat/user-sessions`) uses the `sid` claim and a cache per process.
   The review (2026-09-30) found that:
   - N pods see the same session. Each pod tries to write it, and the
     counter loses increments under concurrency.
   - The `sid` claim is optional, so some IdPs give no session.
   - The openEO Python client asks for `offline_access`. An offline session
     keeps its `sid` for months, so a user who comes back counts only in the
     first month.

   The root cause is not the code. **The component that starts a session is
   the only one that can see it start.** With external OIDC, the session starts
   at the IdP. Titiler only sees access tokens, and must guess.
8. **openeo-studio does not read the back-end.** It sends
   `Bearer oidc/oidc/<token>` (`app/utils/api.ts:19`), so it works only with a
   provider whose id is `oidc`. It ignores `/credentials/oidc` and
   `default_clients`. Authority, client and redirect URI are fixed per build
   (`app/config/runtime.ts`). It has no Basic login.

---

## 2. Options

### 2.1 Requirements

| Id | Requirement |
| --- | --- |
| R1 | Compliant with openEO API 1.2 (endpoints, bearer formats). |
| R2 | JupyterGIS works (username/password dialog, or a bearer that lives long enough). |
| R3 | A change or outage of an external IdP or client does not stop all access. |
| R4 | Where a platform IAM exists (CDSE, MPC), the identity of a user stays the platform identity. |
| R5 | The openEO Python client, the openEO Web Editor, QGIS and openeo-studio continue to work. |
| R6 | Low operations cost. No secret in clear text. |
| R7 | `user_id` is stable and unique, for ownership of services and UDPs. |
| R8 | Titiler counts sessions and active users per period correctly, across pods and token refreshes (#408). |

The options are not exclusive. §3 proposes a combination.

### 2.2 O0 — Status quo: one OIDC provider, Basic only as a static alternative

No change.

- **Pros:** No work. Compliant (OIDC only is permitted). The identity stays
  the platform identity.
- **Cons:** JupyterGIS does not work. One external client stops all access
  (it occurred). Session metrics stay approximate. Gaps 1–3 stay, and gap 3 is
  a security defect.
- **Per deployment:** no change. openeo.ds.io depends on CDSE to restore the
  login.
- **Cost:** none now. Each future IdP policy change costs an outage.

### 2.3 O1 — Basic and OIDC together; Basic users from a Secret

Both methods can be enabled at the same time. Basic users are operator
accounts. They come from a Kubernetes Secret, with hashed passwords (for
example argon2 or bcrypt). `/credentials/basic` issues a random, short-lived
token. The store keeps its hash. The bearer is `basic//<token>`.

- **Pros:** Fixes gaps 1–4. Small change. Gives a working path for CI, demos,
  monitoring and an emergency admin access during an IdP outage. Each Basic
  login is a session that titiler starts, so it can count it.
- **Cons:** The accounts are shared or managed by hand. This does not give
  JupyterGIS to normal users. The identity is not the platform identity.
- **Per deployment:** dev and CI use it by default. CDSE, EOPF and MPC use it
  only for operator accounts.
- **Cost:** low. One table for issued tokens, or signed tokens without a
  table (see §3.4).

### 2.4 O2 — Basic forwarded to the IdP (resource-owner password grant)

Titiler receives `user:password` and exchanges it at the IdP with the
`password` grant.

- **Pros:** JupyterGIS works with the user's own platform account. No
  credential store in titiler.
- **Cons:** OAuth 2.1 and the OAuth security best practice (RFC 9700) forbid
  this grant. It fails for accounts with MFA, and Entra ID does not support it
  for most accounts. User passwords for the platform go through our service.
  It still depends on an IdP client, so it does not help R3.
- **Per deployment:** CDSE may permit it today on `cdse-public`. MPC/Entra: no.
- **Cost:** low code, high risk.

### 2.5 O3 — Several OIDC providers, with clients that we control

`/credentials/oidc` lists N providers. Each provider has a configurable `id`,
issuer, clients and grant types. Each deployment registers its own client at
its IdP, where the IdP permits it, and stops the use of a public client that
someone else controls. `user_id` gets an issuer namespace (gap 6). openeo-studio
reads `/credentials/oidc` and uses the advertised provider and client.

- **Pros:** Uses OIDC as the spec recommends. Removes the dependency on
  `cdse-public`. Permits a second IdP as a fallback (for example EOxHub
  Keycloak, or Entra next to CDSE). Clients choose a provider from the list.
- **Cons:** JupyterGIS does not work. Each client must be registered and kept
  in operation per deployment. The registration still depends on the platform
  policy: the `sh-…` client tested on openeo.ds.io did not solve the problem,
  and openeo.ds.io went back to `cdse-public`.
  Session metrics stay approximate. Adding the issuer to `user_id` is a data
  migration for existing services and UDPs.
- **Per deployment:** CDSE: own client, if CDSE permits one that works
  (the first test failed). EOPF: own client, as a registered component.
  MPC: Entra (ADR 0006).
- **Cost:** medium (settings model, migration of `user_id`, studio).

### 2.6 O4 — Personal access keys owned by titiler

A user logs in once with OIDC (in openeo-studio, or with any openEO client
through new endpoints under `/me`). The user creates an access key. Titiler
stores only a hash of the key, with the `user_id` and the issuer of the OIDC
identity, a name, a creation date, an expiry date and a last-use date. The user
gives the key to any Basic client: username = a stable login name, password =
the key. `/credentials/basic` checks the key and issues a short-lived
`basic//` token, as in O1. The user can list and revoke keys.

This is the model of personal access tokens (GitHub, GitLab, Sentinel Hub
OAuth clients). The key is a credential for titiler only. It is not the
platform password.

- **Pros:** JupyterGIS, QGIS and scripts work for each user, with the user's
  own identity. Keys continue to work during an IdP outage or a client policy
  change. Keys can be revoked one by one. Each Basic login is a session that
  titiler starts, so #408 gets exact numbers for these clients. The identity
  stays the platform identity, because only an OIDC user can create a key.
  The data access does not change (§1.1).
- **Cons:** Titiler keeps credentials, so we own their security: hashing, rate
  limits, expiry, audit, revocation. The endpoints to manage keys are a
  back-end extension (the spec permits it: "Further authentication methods MAY
  be added"). A user who is removed from the platform IAM keeps working keys
  until they expire, unless we check the identity again. Platform operators
  (CDSE, ESA, Microsoft) may have a policy against a second credential.
- **Per deployment:** CDSE, EOPF, MPC: yes, if the operator accepts it (§3.3).
  Dev: not necessary.
- **Cost:** medium. One table in each store (DuckDB, SQLAlchemy, local), four
  endpoints, one studio page.

### 2.7 O5 — Own IdP (Keycloak) as a broker

We operate a Keycloak (for example at `iam.openeo.ds.io`, which exists for
tests). It brokers upstream IdPs (CDSE, Entra, GitHub) and also holds local
users. Titiler trusts only our Keycloak.

- **Pros:** We control clients, redirect URIs, session policy and the
  login page. Keycloak has its own session events and metrics. Local users can
  use Basic through Keycloak.
- **Cons:** On a deployment that extends a platform (CDSE, MPC, EOPF), it puts
  a second identity layer between the user and the platform IAM. The platform
  operator must accept our broker as a client. It is an important service to
  operate and secure. Basic through Keycloak is the password grant of O2.
- **Per deployment:** **not** for CDSE, MPC and EOPF, because a platform IAM
  exists there. **Yes** for an independent deployment that has no platform IAM
  (for example on Scaleway). There, it is the provider of O3.
- **Cost:** high (operations).

### 2.8 O6 — Pass the external bearer to JupyterGIS

The user logs in with the Python client and gives
`connection.auth.bearer` to `add_openeo_tile_layer`. JupyterGIS already
supports this (§1.1).

- **Pros:** Works today with OIDC. No change in titiler.
- **Cons:** The IdP access token expires after minutes, and JupyterGIS does
  not refresh it. Works only from a notebook, not from the layer dialog. It
  does not help R3 or R8.
- **Per deployment:** all OIDC deployments, as a documented workaround.
- **Cost:** documentation only.

### 2.9 O7 — Titiler session token from a token exchange

The client sends the IdP access token once to a titiler endpoint. Titiler
validates it and issues its own short-lived session token with an explicit
start, refresh and end.

- **Pros:** Exact session metrics for OIDC users. Open sessions continue during
  a short IdP outage.
- **Cons:** Standard openEO clients send `oidc/<id>/<IdP token>` on each
  request and do not know this exchange. It works only for clients that we
  write (openeo-studio). For other clients, it is O4 with fewer features.
  It is not a standard openEO flow.
- **Per deployment:** studio only.
- **Cost:** medium.

### 2.10 Scoring

✔ satisfies, ~ partly, ✘ no.

| | R1 spec | R2 JupyterGIS | R3 IdP outage | R4 platform identity | R5 clients | R6 ops / secrets | R7 user_id | R8 sessions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| O0 status quo | ✔ | ✘ | ✘ | ✔ | ~ studio fixed id | ✘ gap 3 | ✔ | ✘ |
| O1 Basic+OIDC, operators | ✔ | ~ shared accounts | ~ admins only | ✘ for those accounts | ✔ | ✔ | ~ shared ids | ✔ Basic |
| O2 password grant | ✔ | ✔ | ✘ | ✔ | ✔ | ✘ passwords, MFA | ✔ | ~ |
| O3 multi-provider OIDC | ✔ | ✘ | ~ | ✔ | ✔ | ~ clients to keep | ~ migration | ✘ |
| O4 access keys | ✔ (+ extension) | ✔ | ✔ existing keys | ✔ | ✔ | ~ we hold hashes | ✔ | ✔ Basic |
| O5 own Keycloak | ✔ | ~ via O2 | ✔ | ✘ on platforms | ✔ | ✘ | ✔ | ~ in Keycloak |
| O6 pass bearer | ✔ | ~ minutes | ✘ | ✔ | ✔ | ✔ | ✔ | ✘ |
| O7 token exchange | ~ extension | ✘ | ~ | ✔ | ~ own clients | ~ | ✔ | ✔ own clients |

---

## 3. Proposed decision

This section is the author's proposal. It is open for review in the PR. §3.3
lists the questions for the stakeholders.

### 3.1 The proposal

Use **O1 + O3 + O4** in all deployments. Add **O5** only for an independent
deployment without a platform IAM. Document **O6** as a workaround until O4 is
available. Do not do **O2**. Keep **O7** as a later option for openeo-studio
only.

1. **The methods are independent.** Basic and OIDC can be enabled together.
   `GET /` lists each credentials endpoint that is enabled.
2. **Basic issues tokens.** `/credentials/basic` returns a random token with an
   expiry (§3.4). Titiler never returns the credential. Passwords and keys are
   stored as hashes only. This fixes gaps 1–3.
3. **Two sources of Basic credentials:**
   - operator accounts from a Kubernetes Secret (O1), for dev, CI, monitoring
     and emergency access;
   - personal access keys (O4), for users of JupyterGIS, QGIS and scripts.
4. **Several OIDC providers (O3).** The provider `id` is configurable. Each
   deployment uses a client that it controls, where the platform permits it.
   `user_id` gets the issuer as a namespace, with a migration for existing
   rows.
5. **Sessions (R8).** Each issued Basic token is one session. Titiler writes
   one row when it issues the token, so the count is exact. For a direct OIDC
   bearer, titiler cannot see the session start (§1.2, gap 7). For these, we
   report **active users per day** (distinct `user_id`, one upsert per user and
   day), and not sessions. PR #415 is closed. Its store work (upserts,
   `timezone=True`, table creation race) moves to the session table of item 2.
6. **openeo-studio** reads `/credentials/oidc` and uses the advertised
   provider id and client. It gets a page to create, list and revoke access
   keys, and an optional Basic login.

### 3.2 Deployment profiles

| Deployment | OIDC | Basic | Notes |
| --- | --- | --- | --- |
| local dev | optional (local Keycloak) | operator accounts | The default `test`/`test` user is removed. Dev sets its own account. |
| CDSE, openeo.ds.io | CDSE; own client when CDSE permits one | operator accounts + access keys | `cdse-public` stays until then. Keys keep access open during a CDSE client problem. |
| EOPF Explorer | CDSE; own client when CDSE permits one | operator accounts + access keys | `cdse-public` stays until then. The risk is recorded (§4.1). |
| MPC | Entra ID (ADR 0006) | operator accounts + access keys | Fix gap 3 before any MPC release on Basic. |
| independent (for example Scaleway) | own Keycloak (O5) | operator accounts + access keys, or Keycloak users | No platform IAM to keep. |

### 3.3 Questions for the stakeholders

1. **Platform operators (CDSE, ESA/EOPF, Microsoft/MPC):** do you accept that
   titiler issues its own access keys to users who logged in with your IAM?
   If not, which deployments must stay OIDC only?
2. **Key policy:** maximum lifetime of a key (proposal: 90 days, renewable),
   maximum number of keys per user (proposal: 10).
3. **Account removal:** is expiry enough when a user is removed from the
   platform IAM? Or must a key stop when the user did not log in with OIDC for
   N days (proposal: 90)?
4. **Metrics (#408):** is "active users per day" acceptable for OIDC users,
   with exact sessions for Basic only? Or must we also do O7 in openeo-studio?
5. **Client ownership:** who registers and owns the OIDC client for each
   deployment, and who is informed of a policy change at the IdP?
6. **`user_id` migration:** can the services and UDPs of existing users be
   migrated to the namespaced `user_id` in one release?

### 3.4 Details to fix in the implementation

- **Token:** random 256 bit, base64url. Titiler stores the SHA-256 hash, the
  `user_id`, the source (`operator` or `key:<id>`), the issue time and the
  expiry. Proposal: lifetime 24 h. The openEO spec has no Basic refresh, so a
  client logs in again.
- **Key:** prefix plus random 256 bit (for example `toek_…`), so that secret
  scanners can find leaked keys. Titiler shows the key once, at creation.
  Titiler stores an argon2 or SHA-256 hash (the key has enough entropy for
  SHA-256).
- **Login name for keys:** the `user_id` is not easy to type. Proposal:
  username = the key id, password = the key secret. Thus the username does not
  disclose the identity.
- **Rate limit** on `/credentials/basic`, per client IP and per username.
- **Endpoints (extension):** `GET/POST /me/access-keys`,
  `DELETE /me/access-keys/{id}`. Only an OIDC identity can call them. A Basic
  token cannot create a key.

---

## 4. Consequences

- Titiler has a small credential store. Its security is now our
  responsibility.
- The ADR 0006 statement "titiler validates, it does not issue" stays true for
  OIDC. It is not true for Basic any more.
- JupyterGIS, QGIS and scripts use Basic with a personal key. The openEO
  Python client also supports Basic.
- The default user `test`/`test` is removed. A deployment that used it breaks
  until the operator sets an account.
- `user_id` changes format once (issuer namespace).
- PR #415 is closed. Issue #408 is answered by §3.1 item 5.

### 4.1 Open risks

- **openeo.ds.io and EOPF Explorer still depend on `cdse-public`.** A CDSE
  policy change can stop OIDC login on both, as it did on openeo.ds.io.
  Access keys reduce the effect, but a user needs an OIDC login to create a
  key.
- **A leaked key is valid until it expires or the user revokes it.** The key
  prefix permits secret scanning. Last-use dates help users find unused keys.
- **Secrets in clear text found during this analysis** (outside this
  repository): an AWS secret key in a local Helm values file for the old CDSE
  deployment, a Keycloak admin password in the values of `iam.openeo.ds.io`,
  and a commented client secret in a local, git-ignored openeo-studio `.env`.
  Rotate them, and move them to Secrets.
- **The live `default_clients` must agree with the GitOps repository.** On
  2026-10-02, after the revert to `cdse-public`, openeo.ds.io still advertised
  the `sh-…` client. Check after each client change.

---

## 5. Implementation plan

Each phase is one PR. Each phase can be released alone.

1. **P1 — Basic correct and safe.** Methods independent. Issued tokens.
   Hashed operator accounts from a Secret. Chart fix for gap 3
   (`TITILER_OPENEO_AUTH_USERS`, dict, from a Secret). Remove the default user.
2. **P2 — Several OIDC providers.** New setting `TITILER_OPENEO_AUTH_OIDC_PROVIDERS`
   (JSON list). The current `TITILER_OPENEO_AUTH_OIDC_*` variables stay as one
   provider with id `oidc`, so that no deployment breaks. `user_id` namespace
   and migration.
3. **P3 — Access keys.** Store tables, `/me/access-keys` endpoints, rate limit,
   sessions written at token issue. Close PR #415.
4. **P4 — openeo-studio.** Read `/credentials/oidc`, provider id from the
   back-end, access-key page, Basic login.
5. **P5 — Deployments.** openeo.ds.io, EOPF `platform-deploy`, MPC values.
   Documentation for JupyterGIS users.

### 5.1 Verification

- P1: the openEO test suite (`openeo-test-suite`, authentication tests) passes
  for Basic and OIDC together. A test shows that a chart value for Basic users
  reaches `AuthSettings`. A test shows that `/credentials/basic` never returns
  the credential, and that an expired token is rejected.
- P2: a test with two providers that issue the same `sub` gives two
  different `user_id` values.
- P3: a test with N concurrent Basic logins on a Postgres store gives N
  session rows. A revoked key cannot log in.
- P4/P5: JupyterGIS connects to openeo.ds.io with a key, from the layer dialog,
  and the tiles load. The openEO Python client `authenticate_basic` works with
  a key.

---

## 6. Related

- [ADR 0003 — Service access control](0003-service-access-control.md) — what a
  `User` can do. Depends on `user_id` (gap 6).
- [ADR 0005 — Asset href signing](0005-asset-href-signing.md) — §2.5 removed the
  per-user seam, so the data access does not depend on the IdP token.
- [ADR 0006 — Microsoft Entra ID as an OIDC provider](0006-microsoft-entra-oidc.md) —
  the OIDC validation that this ADR keeps.
- Issue #408 and PR #415 — session metrics.
- [geojupyter/jupytergis#1904](https://github.com/geojupyter/jupytergis/issues/1904) —
  JupyterGIS cannot use OIDC.
- [openEO API 1.2 — Authentication](https://api.openeo.org/#section/Authentication).
- [RFC 9700 — OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700)
  — §2.4, the password grant must not be used.
