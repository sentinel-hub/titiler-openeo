"""Asset href signing: mediated access to credential-gated assets.

Most catalogues this backend reads hand their credentials to GDAL and to
``ObstoreFetcher`` through the process environment (``AWS_*``), because the
credential is a property of the *deployment* and applies identically to every
href. Microsoft Planetary Computer is different: its blob assets are private and
access is granted by a short-lived Shared Access Signature appended to each href
as a query string, minted per storage container. The credential is a property of
the *href*, so no environment variable can express it.

This module is that mechanism, kept behind a narrow protocol so it is swappable
and testable -- see docs/adr/0005-asset-href-signing.md.

Two things keep this from becoming the plugin system ADR 0002 S2.1 rejects.
Rules match on the href's **host**, which is a fact of the data rather than
hand-written per-collection configuration, and the registry ships in code. A new
catalogue means a new rule and a fixture, exactly as ``bandsources/sources.py``
does for band sources.

``get_href_signer`` returns ``None`` when no rule applies, and ``None`` is what
every threading site defaults to, so a deployment that needs no signing runs the
identical code path it ran before this module existed.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

from .models.auth import User
from .settings import PlanetaryComputerSettings

__all__ = [
    "HrefSigner",
    "SignerRule",
    "SigningError",
    "PlanetaryComputerSigner",
    "PLANETARY_COMPUTER",
    "rules_for_catalogue",
    "get_href_signer",
    "set_default_rules",
    "get_default_href_signer",
]

logger = logging.getLogger(__name__)

#: An href rewriter. Returns the href it was given when it has nothing to add,
#: so callers never need to branch on "did this apply".
HrefSigner = Callable[[str], str]


class SigningError(RuntimeError):
    """Raised when an href needs a credential that could not be obtained.

    Deliberately not swallowed into "return the href unsigned": the unsigned
    read that would follow fails with an opaque HTTP 409 from blob storage,
    which tells an operator nothing about the real cause.
    """


@dataclass(frozen=True)
class SignerRule:
    """One rule: which hrefs need signing, and what signs them.

    ``host`` is pre-compiled and matched with :meth:`re.Pattern.search` against
    the href's hostname, so a registry with many entries does not recompile per
    lookup.

    ``factory`` takes the authenticated user, even though the only shipped
    signer ignores it. See ADR 0005 S2.5: the per-request channel is built now
    because threading it is the expensive part, and a genuinely delegated
    backend (Planetary Computer Pro, private Azure containers reached by an
    on-behalf-of exchange) would otherwise need the same surgery a second time.
    """

    host: "re.Pattern[str]"
    factory: Callable[[Optional[User]], HrefSigner]


# ---------------------------------------------------------------------------
# Planetary Computer
# ---------------------------------------------------------------------------

#: `https://{account}.blob.core.windows.net/{container}/...` -- the only href
#: shape Planetary Computer publishes for its blob assets (ADR 0005 S1.2).
#: Verified against real items from `sentinel-2-l2a` (`sentinel2l2a01`) and
#: `sentinel-1-grd` (`sentinel1euwest`).
_BLOB_HOST = re.compile(
    r"^(?P<account>[^.]+)\.blob\.core\.windows\.net$", re.IGNORECASE
)

#: Any query already carrying a SAS signature. Signing is idempotent so that a
#: pre-signed `alternate` href is never corrupted by appending a second token.
_HAS_SIGNATURE = re.compile(r"(?:^|&)sig=", re.IGNORECASE)

#: Minted SAS tokens, keyed by `(account, container)`.
#:
#: Deliberately **not** keyed by user. Planetary Computer's SAS API is
#: unauthenticated and identity-blind -- an anonymous call, a call with a bearer
#: token and a call with a subscription key all return the same token (ADR 0005
#: S1.2) -- so one entry per container is correct and sharing it across users
#: costs nothing. A delegated signer must key this by user.
_TOKEN_CACHE: Dict[Tuple[str, str], Tuple[str, datetime]] = {}

#: Guards `_TOKEN_CACHE`. The read path fans out over a ThreadPoolExecutor, so
#: several threads can miss the cache for one container at the same time --
#: the same reasoning as `SimpleSTACReader._inverse_map_lock` (ADR 0002 S2.3).
_TOKEN_LOCK = Lock()


def _parse_blob_url(parts) -> Optional[Tuple[str, str]]:
    """Return ``(account, container)`` for an Azure blob href, else ``None``."""
    match = _BLOB_HOST.match(parts.hostname or "")
    if not match:
        return None

    segments = parts.path.lstrip("/").split("/", 1)
    if not segments or not segments[0]:
        return None

    return match.group("account"), segments[0]


def _parse_expiry(value: str) -> datetime:
    """Parse the API's ``msft:expiry`` (e.g. ``2026-08-10T14:36:34Z``)."""
    # `fromisoformat` accepts a trailing "Z" from Python 3.11, which is this
    # project's floor.
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class PlanetaryComputerSigner:
    """Appends a Planetary Computer SAS token to blob hrefs.

    Tokens are minted per ``(account, container)`` rather than per href: the
    per-href ``/sign?href=`` endpoint costs one round-trip per asset, and a
    single mosaic read touches many assets in one container (ADR 0005 S2.4).
    """

    def __init__(
        self,
        user: Optional[User] = None,
        settings: Optional[PlanetaryComputerSettings] = None,
    ) -> None:
        """Bind a signer to ``user``, which public Planetary Computer ignores.

        ``user`` is stored but never read: PC grants every caller the same
        container-scoped, read+list token regardless of identity (ADR 0005
        S1.2), so there is no entitlement to delegate. It is kept so the seam
        has a real shape for a backend where identity does decide access.
        """
        self.user = user
        self.settings = settings or PlanetaryComputerSettings()

    def __call__(self, href: str) -> str:
        """Return ``href`` with a SAS token, or unchanged if it needs none."""
        parts = urlsplit(href)

        account_container = _parse_blob_url(parts)
        if account_container is None:
            return href

        if _HAS_SIGNATURE.search(parts.query):
            return href

        token = self._token_for(*account_container)
        query = f"{parts.query}&{token}" if parts.query else token
        # The API returns an already percent-encoded token; re-encoding it
        # through urlencode would double-escape the signature and invalidate it.
        return urlunsplit(parts._replace(query=query))

    def _token_for(self, account: str, container: str) -> str:
        """Return a live SAS token for one container, minting it if needed."""
        key = (account, container)
        margin = timedelta(seconds=self.settings.expiry_margin)

        with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(key)
            if cached is not None and datetime.now(timezone.utc) + margin < cached[1]:
                return cached[0]

        token, expiry = self._mint(account, container)

        with _TOKEN_LOCK:
            _TOKEN_CACHE[key] = (token, expiry)

        return token

    def _mint(self, account: str, container: str) -> Tuple[str, datetime]:
        """Fetch a fresh token from the Data Authentication API."""
        url = f"{self.settings.sas_url.rstrip('/')}/token/{account}/{container}"
        headers = {}
        if self.settings.subscription_key:
            headers["Ocp-Apim-Subscription-Key"] = self.settings.subscription_key

        request = urllib.request.Request(url, headers=headers)  # noqa: S310

        # One retry: minting is on the critical path of every read, and a single
        # transient failure should not fail a user's request. Anything
        # persistent is a real error and is raised rather than hidden.
        last_error: Optional[Exception] = None
        for _attempt in range(2):
            try:
                with urllib.request.urlopen(  # noqa: S310
                    request, timeout=self.settings.timeout
                ) as response:
                    payload = json.loads(response.read())
                return payload["token"], _parse_expiry(payload["msft:expiry"])
            except (urllib.error.URLError, OSError, ValueError, KeyError) as err:
                last_error = err
                logger.debug(
                    "SAS token request failed for %s/%s",
                    account,
                    container,
                    exc_info=True,
                )

        raise SigningError(
            f"Could not obtain a Planetary Computer SAS token for "
            f"'{account}/{container}' from {url}: {last_error}"
        ) from last_error


#: The shipped registry. One entry today; a new catalogue adds a rule here and a
#: fixture, per ADR 0005 S2.1.
PLANETARY_COMPUTER = SignerRule(
    host=re.compile(r"\.blob\.core\.windows\.net$", re.IGNORECASE),
    factory=PlanetaryComputerSigner,
)

#: Host of the Planetary Computer STAC API, which is what activates the rule
#: above (ADR 0005 S2.3). Signing is scoped to the configured catalogue so a
#: deployment reading its own Azure blob containers never makes an outbound call
#: to microsoft.com as a side effect of a registry default.
_PC_STAC_HOST = "planetarycomputer.microsoft.com"


def rules_for_catalogue(stac_api_url: str) -> Tuple[SignerRule, ...]:
    """Return the signing rules that apply to a deployment's STAC API."""
    host = (urlsplit(stac_api_url).hostname or "").lower()
    if host == _PC_STAC_HOST or host.endswith(f".{_PC_STAC_HOST}"):
        return (PLANETARY_COMPUTER,)
    return ()


def get_href_signer(
    rules: Sequence[SignerRule],
    user: Optional[User] = None,
) -> Optional[HrefSigner]:
    """Build a signer for ``user`` from ``rules``, or ``None`` if there are none.

    Returning ``None`` rather than an identity function is the point: it is what
    lets every call site keep ``signer=None`` as its default and run byte-identical
    code when nothing needs signing, mirroring ``build_per_request_registry``'s
    "return the base object unchanged" gate (ADR 0005 S2.2).

    An href whose host matches no rule is returned untouched -- which is how
    Planetary Computer's own ``tilejson`` and ``rendered_preview`` assets, served
    from the API host rather than blob storage, stay unsigned.
    """
    if not rules:
        return None

    signers = [(rule.host, rule.factory(user)) for rule in rules]

    def sign(href: str) -> str:
        host = urlsplit(href).hostname or ""
        for pattern, signer in signers:
            if pattern.search(host):
                return signer(href)
        return href

    return sign


_default_rules: Tuple[SignerRule, ...] = ()


def set_default_rules(rules: Sequence[SignerRule]) -> None:
    """Record the process-wide rules, resolved once from settings at startup.

    Only ``load_url`` needs this. Every other read path carries a signer bound
    to the authenticated user; ``load_url`` takes a user-supplied URL from
    inside an already-evaluating process graph and has no user in scope.
    """
    global _default_rules
    _default_rules = tuple(rules)


def get_default_href_signer() -> Optional[HrefSigner]:
    """Return an unbound signer built from the process-wide rules."""
    return get_href_signer(_default_rules)
