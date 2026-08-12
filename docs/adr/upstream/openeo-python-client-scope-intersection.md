# Upstream issue draft — openeo-python-client

- **Target:** [Open-EO/openeo-python-client](https://github.com/Open-EO/openeo-python-client)
- **Status:** Draft, not yet filed
- **Date:** 2026-08-11
- **Affects:** `openeo` 0.48.0
- **Local workaround:** `titiler.openeo.client_compat.patch_openeo_client_scopes()`

Paste the section below into a new GitHub issue.

---

## Title

Cannot authenticate against a Microsoft Entra ID protected backend: Entra never advertises custom scopes in `scopes_supported`

## The problem

Microsoft Entra ID cannot advertise an application's custom API scopes in its
OIDC discovery document. Its `scopes_supported` is tenant-wide and permanently
fixed at `["openid", "profile", "email", "offline_access"]`. This is by design,
per Microsoft in
[AzureAD/microsoft-identity-web#1689](https://github.com/AzureAD/microsoft-identity-web/discussions/1689):

> The OIDC metadata endpoint just returns Azure AD's STS metadata. There's no
> concept of custom resources or custom scopes here.

`OidcProviderInfo.__init__` intersects the scopes a backend declares at
`/credentials/oidc` against that list
(`openeo/rest/auth/oidc.py`, 0.48.0):

```python
self._scopes = {"openid"}.union(scopes or []).intersection(self._supported_scopes)
```

A sensible default against a well-behaved provider — but with Entra it drops the
one scope that matters:

```python
provider = OidcProviderInfo(
    issuer=f"https://login.microsoftonline.com/{TENANT}/v2.0",
    scopes=["openid", "profile", "email", f"api://{CLIENT_ID}/openeo"],
)
provider.get_scopes_string()
# -> 'email openid profile'      the api:// scope never reaches Entra
```

With no resource scope requested, Entra issues an access token audienced at
Microsoft Graph. Those carry a `nonce` header and are signed over a modified
header, so no third party can verify them — deliberately, as the
audience-confusion defence. The backend rejects the token, and the user sees a
signature error with no obvious connection to a dropped scope (it is only
`log.debug`-ed).

The net effect is that **no openEO backend protected by Entra can be
authenticated against with this client**, and nothing the backend or the app
registration can configure will change that.

## Possible accommodations

Any of these would be enough:

1. **Warn instead of dropping** — keep the declared scopes, `log.warning` when
   one is not advertised. Preserves the diagnostic value without the breakage.
2. **Opt out** — e.g. `OidcProviderInfo(..., strict_scopes=False)`, plumbed
   through `Connection.authenticate_oidc*`.
3. **Do not intersect at all** — [RFC 8414](https://www.rfc-editor.org/rfc/rfc8414#section-2)
   makes `scopes_supported` RECOMMENDED and descriptive, and RFC 6749 §3.3 puts
   the grant decision on the authorization server, which reports the result in
   the `scope` response parameter.

Options 1 and 3 also help any other provider with incomplete metadata; option 2
leaves each user to discover the problem first.

## Workaround

```python
from openeo.rest.auth.oidc import OidcProviderInfo

_orig_init = OidcProviderInfo.__init__

def _patched_init(self, issuer=None, discovery_url=None, scopes=None, **kwargs):
    _orig_init(self, issuer=issuer, discovery_url=discovery_url, scopes=scopes, **kwargs)
    self._scopes = {"openid"} | set(scopes or [])

OidcProviderInfo.__init__ = _patched_init
```

## Environment

`openeo` 0.48.0 · Python 3.13 · Entra ID v2.0 endpoint, single tenant ·
backend: [titiler-openeo](https://github.com/sentinel-hub/titiler-openeo)

---

## Notes for us (not part of the issue)

- Shipped as `titiler.openeo.client_compat.patch_openeo_client_scopes()`,
  documented in `docs/src/openid-connect.md`. Import-side-effect free and
  idempotent; tested against a stand-in reproducing the upstream intersection,
  so it needs no dependency on the client.
- Delete the helper once a release carrying a fix is our minimum.
- Affects any Entra-protected openEO backend, so worth filing despite the local
  fix.
