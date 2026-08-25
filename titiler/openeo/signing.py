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

**Which signer applies is deployment configuration, not something this module
infers.** An earlier version activated signing when the configured STAC API URL
happened to be ``planetarycomputer.microsoft.com``; that put a cloud provider's
hostname in the application's decision logic (issue #377). Now a deployment names
a signer key, core maps the key to an implementation, and nothing here knows
which catalogue a deployment reads.

The key travels to the read path on the **item**, stamped once at ingest
(``ITEM_SIGNER_KEY``), because the readers that open hrefs are constructed deep
inside the mosaic and run on worker threads no parameter or contextvar reaches.
The key is resolved to a signer at *open* time, per read and per retry, so a
token that expires mid-graph is re-minted rather than reused (ADR 0005 S3.1) --
which is why the stamp carries a key and never a signed href.

``get_signer`` returns ``None`` for an unstamped item, so a deployment that needs
no signing runs the identical code path it ran before this module existed.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .settings import PlanetaryComputerSettings

__all__ = [
    "HrefSigner",
    "SigningError",
    "PlanetaryComputerSigner",
    "ITEM_SIGNER_KEY",
    "SIGNERS",
    "get_signer",
    "stamp_signer_key",
    "signer_for_item",
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
        settings: Optional[PlanetaryComputerSettings] = None,
    ) -> None:
        """Build a signer for this deployment's Planetary Computer settings.

        Deliberately takes no ``User``. PC grants every caller the same
        container-scoped, read+list token regardless of identity (ADR 0005
        S1.2), so there is no entitlement to delegate, and the item stamp that
        selects this signer cannot carry a live user into the worker threads
        that open assets. A genuinely delegated backend (PC Pro, private Azure
        via on-behalf-of) needs a per-user channel that does not exist here --
        see ADR 0005 S2.5, which records this as an accepted limit rather than
        a solved problem.
        """
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


#: The STAC property an item carries to say which signer its assets need.
#: Written once at ingest (`stacapi.py`), read at open time by
#: `reader._signer_for_item`. Namespaced like a STAC extension field so it reads
#: as metadata rather than folklore, and a plain string so it survives
#: `Item.to_dict()` -- `load_stac` hands `_reader` dicts, and a callable would
#: not make that round trip (ADR 0005 S2.2).
ITEM_SIGNER_KEY = "titiler:sign"

#: The shipped signers, by key. A deployment names one of these keys; core never
#: infers it from a hostname. A new catalogue adds an entry here and a fixture.
SIGNERS: Dict[str, Callable[[], HrefSigner]] = {
    "planetary-computer": PlanetaryComputerSigner,
}


@lru_cache(maxsize=None)
def get_signer(key: Optional[str]) -> Optional[HrefSigner]:
    """Return the signer registered under ``key``, or ``None`` for no key.

    Returning ``None`` rather than an identity function is the point: it lets
    every call site keep signing off by default and run byte-identical code when
    no item is stamped (ADR 0005 S2.2).

    Memoised because this is now resolved per href opened, not once per request:
    building a signer constructs a pydantic settings object, which reads the
    environment. The signers themselves hold no per-request state -- the SAS
    token cache above is module-level and already shared.

    Unknown keys raise rather than silently returning ``None``: a typo'd key
    would otherwise read as "signing off" and surface as an opaque HTTP 409 from
    blob storage, which is exactly what `SigningError` exists to prevent.
    """
    if not key:
        return None

    factory = SIGNERS.get(key)
    if factory is None:
        raise SigningError(
            f"No signer registered under '{key}'. Known signers: "
            f"{', '.join(sorted(SIGNERS)) or '(none)'}."
        )

    return factory()


def stamp_signer_key(item: Any, key: Optional[str]) -> Any:
    """Record on ``item`` which signer its assets need, and return it.

    Called once per item at ingest. A falsy ``key`` leaves the item untouched,
    so a deployment with no signer configured produces items indistinguishable
    from those this backend produced before signing existed.

    Accepts a ``pystac.Item`` or a plain STAC dict because both reach the read
    path: `load_collection` carries `pystac.Item`s while `load_stac` hands
    `_reader` the output of `Item.to_dict()`.

    The stamp is deliberately **not** scoped to hrefs that look like they need
    signing. Each signer already returns an href untouched when it has nothing
    to add -- which is how Planetary Computer's own `tilejson` and
    `rendered_preview` assets, served from the API host rather than blob
    storage, stay unsigned -- so narrowing here would duplicate that judgement
    in a second place and let the two disagree.
    """
    if not key:
        return item

    if isinstance(item, dict):
        item.setdefault("properties", {})[ITEM_SIGNER_KEY] = key
    else:
        item.properties[ITEM_SIGNER_KEY] = key

    return item


def signer_for_item(item: Any) -> Optional[HrefSigner]:
    """Return the signer ``item`` was stamped with at ingest, if any.

    Resolved per call rather than cached on the item: this runs at *open* time,
    once per reader construction, so a retry after a `RasterioIOError` re-mints
    an expired token instead of reusing the one the first attempt used
    (ADR 0005 S3.1). Resolution itself is memoised in `get_signer`.
    """
    if isinstance(item, dict):
        properties = item.get("properties") or {}
    else:
        properties = getattr(item, "properties", None) or {}

    return get_signer(properties.get(ITEM_SIGNER_KEY))
