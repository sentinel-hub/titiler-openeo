"""Client-side compatibility patches for the openEO **Python client**.

Nothing here runs as part of the backend. This module exists so a notebook or
script talking *to* a titiler-openeo deployment can work around a defect in the
`openeo` client library, in one import and one call:

    from titiler.openeo.client_compat import patch_openeo_client_scopes
    patch_openeo_client_scopes()

Importing this module has no side effects -- the patch is applied only when the
function is called, so it can never surprise the server or a test run.

See docs/adr/upstream/openeo-python-client-scope-intersection.md for the
upstream report this works around.
"""

import logging
from typing import Any, List, Optional, Set

__all__ = ["patch_openeo_client_scopes"]

logger = logging.getLogger(__name__)

#: Set on the patched class so a second call is a no-op rather than wrapping
#: the wrapper -- notebooks get re-run, and a stack of wrappers would still be
#: correct but would make a traceback harder to read.
_MARKER = "_titiler_openeo_scopes_patched"


def patch_openeo_client_scopes(provider_info_cls: Optional[Any] = None) -> bool:
    """Stop the openEO Python client discarding scopes its backend asked for.

    ``OidcProviderInfo.__init__`` narrows the backend's advertised scopes to
    those the identity provider lists in its discovery document::

        self._scopes = {"openid"}.union(scopes or []).intersection(self._supported_scopes)

    Microsoft Entra's ``scopes_supported`` is fixed at
    ``["openid", "profile", "email", "offline_access"]`` and, per Microsoft,
    "there's no concept of custom resources or custom scopes here" -- so an
    ``api://<client_id>/<scope>`` scope is always discarded. Entra then issues a
    token audienced at Microsoft Graph, whose signature no third party can
    verify, and login fails at the backend with a signature error.

    This restores the scopes the backend actually advertised. ``openid`` is
    still forced, matching upstream, and ``get_scopes_string`` keeps its own
    ``offline_access`` handling.

    Args:
        provider_info_cls: The class to patch. Defaults to
            ``openeo.rest.auth.oidc.OidcProviderInfo``; injectable so the
            behaviour can be tested without the client installed.

    Returns:
        ``True`` if the patch was applied, ``False`` if it was already in place.

    Raises:
        ImportError: If the openEO client is not installed and no class was
            passed explicitly.
    """
    if provider_info_cls is None:
        try:
            from openeo.rest.auth.oidc import (  # type: ignore[import-untyped]
                OidcProviderInfo,
            )
        except ImportError as exc:  # pragma: nocover
            raise ImportError(
                "The openEO Python client is not installed, so there is nothing "
                "to patch. Install it with `pip install openeo` (or "
                "`uv sync --group notebook`)."
            ) from exc

        provider_info_cls = OidcProviderInfo

    if getattr(provider_info_cls, _MARKER, False):
        return False

    original_init = provider_info_cls.__init__

    def __init__(  # noqa: N807
        self,
        issuer: Optional[str] = None,
        discovery_url: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        original_init(
            self,
            issuer=issuer,
            discovery_url=discovery_url,
            scopes=scopes,
            **kwargs,
        )
        # Patched after the fact rather than by reimplementing __init__: the
        # rest of it (discovery fetch, issuer resolution, default_clients) is
        # upstream's business and must keep working unchanged.
        requested: Set[str] = {"openid"} | set(scopes or [])
        if requested != set(self._scopes):
            logger.debug(
                "openEO client scope intersection undone: %s -> %s",
                sorted(self._scopes),
                sorted(requested),
            )
        self._scopes = requested

    provider_info_cls.__init__ = __init__
    setattr(provider_info_cls, _MARKER, True)
    return True
