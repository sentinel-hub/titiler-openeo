"""Tests for titiler.openeo.client_compat.

The openEO client is not a dependency of this project, so these exercise the
patch against a faithful stand-in for `OidcProviderInfo` -- one that reproduces
the intersection at `openeo/rest/auth/oidc.py` `__init__`. The real class is
also patched, but only when the client happens to be installed.
"""

import pytest

from titiler.openeo.client_compat import patch_openeo_client_scopes

ENTRA_SUPPORTED = ["openid", "profile", "email", "offline_access"]
CUSTOM_SCOPE = "api://4c866cbc-a39d-4eff-b598-73e23309b51c/openeo"


def _make_stub(supported=None):
    """A stand-in reproducing upstream's scope intersection."""
    supported = supported if supported is not None else ENTRA_SUPPORTED

    class OidcProviderInfoStub:
        def __init__(self, issuer=None, discovery_url=None, scopes=None, **kwargs):
            self.issuer = issuer
            self.kwargs = kwargs
            self._supported_scopes = supported
            self._scopes = {"openid"}.union(scopes or []).intersection(
                self._supported_scopes
            )

        def get_scopes_string(self, request_refresh_token: bool = False) -> str:
            scopes = self._scopes
            if request_refresh_token and "offline_access" in self._supported_scopes:
                scopes = scopes | {"offline_access"}
            return " ".join(sorted(scopes))

    return OidcProviderInfoStub


def test_the_stub_reproduces_the_upstream_defect():
    """Guard the premise: without the patch the custom scope is dropped."""
    cls = _make_stub()
    provider = cls(issuer="https://idp", scopes=["openid", "email", CUSTOM_SCOPE])
    assert provider.get_scopes_string() == "email openid"


def test_patch_restores_the_backend_declared_scopes():
    cls = _make_stub()
    assert patch_openeo_client_scopes(cls) is True

    provider = cls(issuer="https://idp", scopes=["openid", "email", CUSTOM_SCOPE])
    assert CUSTOM_SCOPE in provider.get_scopes_string()
    assert provider.get_scopes_string() == f"{CUSTOM_SCOPE} email openid"


def test_patch_leaves_offline_access_handling_to_upstream():
    cls = _make_stub()
    patch_openeo_client_scopes(cls)

    provider = cls(issuer="https://idp", scopes=["openid", CUSTOM_SCOPE])
    assert provider.get_scopes_string(request_refresh_token=True) == (
        f"{CUSTOM_SCOPE} offline_access openid"
    )


def test_openid_is_always_present():
    cls = _make_stub()
    patch_openeo_client_scopes(cls)

    provider = cls(issuer="https://idp", scopes=[CUSTOM_SCOPE])
    assert "openid" in provider.get_scopes_string().split()


def test_no_declared_scopes_still_yields_openid():
    cls = _make_stub()
    patch_openeo_client_scopes(cls)

    assert cls(issuer="https://idp").get_scopes_string() == "openid"


def test_patch_is_idempotent():
    """Notebooks get re-run; a second call must not stack wrappers."""
    cls = _make_stub()
    assert patch_openeo_client_scopes(cls) is True
    assert patch_openeo_client_scopes(cls) is False

    provider = cls(issuer="https://idp", scopes=["openid", CUSTOM_SCOPE])
    assert CUSTOM_SCOPE in provider.get_scopes_string()


def test_patch_preserves_the_rest_of_init():
    """Discovery, issuer resolution and default_clients stay upstream's job."""
    cls = _make_stub()
    patch_openeo_client_scopes(cls)

    provider = cls(
        issuer="https://idp", scopes=["openid"], default_clients=[{"id": "x"}]
    )
    assert provider.issuer == "https://idp"
    assert provider.kwargs["default_clients"] == [{"id": "x"}]
    assert provider._supported_scopes == ENTRA_SUPPORTED


def test_a_provider_that_advertises_everything_is_unaffected():
    """Against a compliant provider the patch changes nothing observable."""
    supported = ["openid", "profile", "email", "offline_access", CUSTOM_SCOPE]
    declared = ["openid", "email", CUSTOM_SCOPE]

    before = _make_stub(supported)(issuer="https://idp", scopes=declared)
    after_cls = _make_stub(supported)
    patch_openeo_client_scopes(after_cls)
    after = after_cls(issuer="https://idp", scopes=declared)

    assert after.get_scopes_string() == before.get_scopes_string()


def test_importing_the_module_has_no_side_effects():
    """The patch must only ever be applied by an explicit call."""
    openeo = pytest.importorskip("openeo")  # noqa: F841
    from openeo.rest.auth.oidc import OidcProviderInfo

    import titiler.openeo.client_compat  # noqa: F401

    assert not getattr(OidcProviderInfo, "_titiler_openeo_scopes_patched", False)
