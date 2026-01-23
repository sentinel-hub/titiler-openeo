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
```

## Token Validation

The OIDC implementation performs the following validations in the [`_verify_token`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py#L177) method:

1. Verifies the token signature using the provider's JWKS
2. Validates token claims including:
   - Client ID matches the configured one
   - Token expiration
   - Token audience

## User Information

Upon successful validation, a [`User`](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py#L35) object is created with:

- `user_id`: Subject claim from the token (`sub`)
- `email`: Email claim if available
- `name`: Value from the configured name claim (defaults to "name")

## Security Considerations

- Keep your client ID secure
- Configure appropriate token expiration times
- Use HTTPS in production
- Review and limit the requested scopes
- Regularly rotate any client secrets if used

For more details on the implementation, see the [auth module source code](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/auth.py).
