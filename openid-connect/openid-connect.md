# OpenID Connect Configuration

TiTiler-OpenEO supports OpenID Connect (OIDC) authentication following the OpenEO authentication model. The implementation supports the OpenID Connect Authorization Code Flow with PKCE.

The implementation is available in [`titiler/openeo/auth.py`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py) with the main class being [`OIDCAuth`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py#L123).

## OpenEO Authentication Model

TiTiler-OpenEO follows the OpenEO authentication specification where tokens are provided in the format:

```
Bearer oidc/oidc/{actual_token}
```

The token structure consists of three parts:

1. Authentication method (`oidc`)
2. Provider identifier (`oidc`)
3. The actual OIDC token

Token parsing is handled by the [`AuthToken`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py#L284) class.

## Configuration

The OIDC configuration is managed through [`OIDCConfig`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/settings.py#L8) in the settings. To enable OpenID Connect authentication, configure the following environment variables:

```bash
TITILER_OPENEO_AUTH_METHOD=oidc
TITILER_OPENEO_AUTH_OIDC_CLIENT_ID="your-client-id"
TITILER_OPENEO_AUTH_OIDC_WK_URL="https://your-provider/.well-known/openid-configuration"
TITILER_OPENEO_AUTH_OIDC_REDIRECT_URL="your-redirect-url"
```

Optional configuration:

```bash
TITILER_OPENEO_AUTH_OIDC_SCOPES="openid email profile"  # Space-separated list (default)
TITILER_OPENEO_AUTH_OIDC_NAME_CLAIM="name"  # Claim to use for user name (default)
TITILER_OPENEO_AUTH_OIDC_TITLE="OIDC"  # Provider title (default)
TITILER_OPENEO_AUTH_OIDC_DESCRIPTION="OpenID Connect (OIDC) Authorization Code Flow with PKCE"  # Provider description (default)
TITILER_OPENEO_AUTH_OIDC_AUDIENCES=""  # Space-separated extra accepted `aud` values
TITILER_OPENEO_AUTH_OIDC_USER_ID_CLAIM="sub"  # Claim used as User.user_id (default)
```

`TITILER_OPENEO_AUTH_METHOD=oidc` is validated at startup: the backend refuses
to start if `CLIENT_ID` or `WK_URL` is unset, rather than failing on the first
request.

!!! warning "`USER_ID_CLAIM` is not safe to change on a running deployment"
    Services, UDPs and tile assignments are all keyed on `User.user_id`.
    Changing which claim produces it orphans everything already stored.

## Microsoft Entra ID

[Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
works with the configuration above, with one requirement.

**Use a single-tenant well-known URL.** The multi-tenant `common` endpoint
returns a templated issuer, `https://login.microsoftonline.com/{tenantid}/v2.0`,
which is a literal placeholder rather than a URL. It cannot be compared against
a token's `iss` and cannot be advertised to openEO clients, so the backend
rejects it with a message naming the fix.

```bash
TITILER_OPENEO_AUTH_METHOD=oidc
TITILER_OPENEO_AUTH_OIDC_CLIENT_ID="<application (client) ID>"
TITILER_OPENEO_AUTH_OIDC_WK_URL="https://login.microsoftonline.com/<tenant_id>/v2.0/.well-known/openid-configuration"
TITILER_OPENEO_AUTH_OIDC_REDIRECT_URL="http://localhost:8080/"
TITILER_OPENEO_AUTH_OIDC_NAME_CLAIM="preferred_username"
TITILER_OPENEO_AUTH_OIDC_TITLE="Microsoft Entra ID"
```

### Expose an API scope, and mint the token out-of-band

openEO clients send the **access token**, never the ID token. With only
`openid profile email` requested, Entra issues an access token audienced at
**Microsoft Graph**, and those cannot be validated by anyone but Graph: they
carry a `nonce` in the JWT header and their signature will not verify against
the tenant's published JWKS. The symptom is a 401 with *"Token signature
verification failed"*.

Entra must therefore issue a token audienced at *this* backend:

1. App registration → **Expose an API** → **Set** the Application ID URI →
   accept the default `api://<client_id>`.
2. **Add a scope**, e.g. `openeo`, with admin and user consent enabled.
3. **Manifest** → set `"requestedAccessTokenVersion": 2`. Without this Entra
   issues a v1 access token whose `iss` is `https://sts.windows.net/<tenant>/`,
   which will not match the v2 discovery document's issuer and fails the `iss`
   check with *"Invalid issuer"*.

```bash
TITILER_OPENEO_AUTH_OIDC_AUDIENCES="api://<client_id>"
```

!!! warning "The openEO **Python** client drops this scope by default"
    `openeo` (checked at 0.48.0) intersects the scopes a backend advertises with
    the provider's own `scopes_supported`
    (`openeo/rest/auth/oidc.py`, `OidcProviderInfo.__init__`):
    `{"openid"}.union(scopes).intersection(self._supported_scopes)`. Microsoft
    Entra advertises a fixed `["openid", "profile", "email", "offline_access"]`
    — static platform metadata that by construction never lists an app's custom
    API scopes — so `api://<client_id>/openeo` is **silently dropped** and Entra
    falls back to a Graph-audienced token. `scopes_supported` is *RECOMMENDED*
    and informational per [RFC 8414](https://www.rfc-editor.org/rfc/rfc8414),
    so this client-side filtering is arguably too strict. Microsoft has
    [confirmed](https://github.com/AzureAD/microsoft-identity-web/discussions/1689)
    that Entra will never advertise custom scopes, so this cannot be fixed from
    the provider side.

Undo the intersection with the helper this project ships. Call it once, before
connecting — from any notebook or script:

```python
from titiler.openeo.client_compat import patch_openeo_client_scopes

patch_openeo_client_scopes()

import openeo
connection = openeo.connect("http://127.0.0.1:8083/").authenticate_oidc()
```

The call is idempotent, so re-running a cell is harmless, and importing the
module patches nothing on its own. `_scopes` is computed once in
`OidcProviderInfo.__init__`, so this covers every flow — authorization code,
device code and refresh token all read it through `get_scopes_string`.

With it in place `TITILER_OPENEO_AUTH_OIDC_SCOPES` works as documented and the
ordinary `authenticate_oidc()` flow succeeds, keeping refresh-token caching.
Patch at runtime rather than editing the installed package — `uv sync` reverts a
site-packages edit.

The upstream report is drafted at
`docs/adr/upstream/openeo-python-client-scope-intersection.md`; drop the helper
once a release carrying a fix is the minimum this project supports.

If you would rather not patch a third-party class, mint the token out-of-band
with MSAL and hand it over with `authenticate_oidc_access_token()`
(openeo >= 0.31); this also needs no redirect URI:

```python
import msal, openeo

TENANT, CLIENT_ID = "<tenant_id>", "<client_id>"
app = msal.PublicClientApplication(
    CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT}"
)
flow = app.initiate_device_flow(scopes=[f"api://{CLIENT_ID}/openeo"])
print(flow["message"])          # sign in at microsoft.com/devicelogin
token = app.acquire_token_by_device_flow(flow)["access_token"]

connection = openeo.connect("http://127.0.0.1:8083/").authenticate_oidc_access_token(token)
```

In the Entra app registration:

- Register a **public client**; both grants openEO clients use — authorization
  code with PKCE, and device code — are supported.
- Add your openEO client's redirect URL (the Web Editor's is its own origin).
- If your clients present **access** tokens audienced at your own API rather
  than ID tokens, add that audience:
  `TITILER_OPENEO_AUTH_OIDC_AUDIENCES="api://<client_id>"`.

Entra's `sub` is *pairwise*: stable for a given user in a given app
registration, and different if you re-register the app. If you expect to
re-register, consider pinning identity to the tenant-stable object id with
`TITILER_OPENEO_AUTH_OIDC_USER_ID_CLAIM="oid"` — but decide before going live,
per the warning above.

For asset access on Microsoft Planetary Computer, see
[Microsoft Planetary Computer](planetary-computer.md). Note that Entra
identifies your users; it does not change what they can read there.

### `GET /credentials/oidc` returns 404

That is expected when the backend is not configured for OIDC. openEO backends
advertise only the authentication method they actually support, so
`/credentials/oidc` and `/credentials/basic` are **mutually exclusive** — the
one that does not apply is never registered:

| `TITILER_OPENEO_AUTH_METHOD` | Registered route |
| --- | --- |
| `basic` (default) | `/credentials/basic` |
| `oidc` | `/credentials/oidc` |

Set `TITILER_OPENEO_AUTH_METHOD=oidc` together with `CLIENT_ID` and `WK_URL`.
If any of those are missing the backend now refuses to start and names the
variable, rather than starting and failing on the first request.

## Token Validation

Validation is performed in the [`_verify_token`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py)
and `_verify_claims` methods:

1. The header's `alg` must be `RS256`, the only algorithm this backend verifies.
2. The signature is verified against the provider's JWKS. If the token's key id
   is not in the cached key set, the JWKS is refetched once — providers rotate
   signing keys continuously, and Entra publishes six at a time.
3. `iss` must match the discovery document's `issuer`.
4. `aud` must contain the client ID or one of the configured `AUDIENCES`, or
   `azp` must equal one of them.
5. `exp` is **required**, and checked with 60 seconds of clock-skew allowance.
   `nbf` is checked when present.

The discovery document is cached for an hour rather than for the life of the
process, so a provider that moves its `jwks_uri` does not break a running
deployment permanently.

## User Information

Upon successful validation, a [`User`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/models/auth.py) object is created with:

- `user_id`: the claim named by `USER_ID_CLAIM` (defaults to `sub`)
- `email`: Email claim if available
- `name`: Value from the configured name claim (defaults to "name")

## Security Considerations

- Keep your client ID secure
- Configure appropriate token expiration times
- Use HTTPS in production
- Review and limit the requested scopes
- Regularly rotate any client secrets if used

For more details on the implementation, see the [auth module source code](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py).
