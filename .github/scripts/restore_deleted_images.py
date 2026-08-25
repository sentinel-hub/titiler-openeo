#!/usr/bin/env python3
"""Restore GHCR image manifests wrongly deleted by the pre-v0.2.2 cleanup bug.

developmentseed/container-registry-cleanup < v0.2.2 authenticated GHCR
distribution-API manifest fetches (ghcr.io/v2/...) with the raw GitHub API
token. That token is not valid there, so every manifest fetch failed, and the
tool could never discover which untagged child manifests belonged to a
tagged multi-arch image index. It then deleted those children as if they
were orphaned. The tag itself survives (e.g. v0.17.0, latest) but `docker
pull` fails because the platform-specific manifest it points to is gone.
See https://github.com/developmentseed/container-registry-cleanup/pull/40.

This script re-derives the same "protected digest" set the fixed tool now
computes (manifests reachable from a currently tagged release), finds which
of those digests are unreachable, and restores them from GHCR's 30-day
soft-delete via the package-version restore API. It only ever restores
digests that are still referenced by a currently active release tag, so it
cannot resurrect anything that was correctly deleted.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
ORG = os.environ["GITHUB_REPO_OWNER"]
PACKAGE = os.environ["REPOSITORY_NAME"]
VERSION_PATTERN = re.compile(
    os.environ.get("VERSION_PATTERN", r"^(v\d+\.\d+\.\d+.*|latest)$")
)
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

MANIFEST_ACCEPT = ",".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def gh_api(path: str, method: str = "GET") -> dict | list | None:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=b"" if method == "POST" else None,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        return json.loads(body) if body else None


def list_package_versions(state: str) -> list[dict]:
    versions: list[dict] = []
    page = 1
    while True:
        batch = gh_api(
            f"/orgs/{ORG}/packages/container/{PACKAGE}/versions"
            f"?state={state}&per_page=100&page={page}"
        )
        if not batch:
            break
        versions.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return versions


def get_registry_token() -> str:
    scope = f"repository:{ORG}/{PACKAGE}:pull"
    basic = base64.b64encode(f"{ORG}:{GITHUB_TOKEN}".encode()).decode()
    req = urllib.request.Request(
        f"https://ghcr.io/token?service=ghcr.io&scope={scope}",
        headers={"Authorization": f"Basic {basic}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["token"]


def fetch_manifest(registry_token: str, digest: str) -> dict | None:
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{ORG}/{PACKAGE}/manifests/{digest}",
        headers={
            "Authorization": f"Bearer {registry_token}",
            "Accept": MANIFEST_ACCEPT,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main() -> int:
    print(f"Restoring missing release images for {ORG}/{PACKAGE} (dry_run={DRY_RUN})")

    active = list_package_versions("active")
    deleted = list_package_versions("deleted")
    deleted_by_digest = {v["name"]: v for v in deleted}
    print(f"{len(active)} active version(s), {len(deleted)} deleted version(s)")

    release_tags = {}
    for v in active:
        for tag in v.get("metadata", {}).get("container", {}).get("tags", []):
            if VERSION_PATTERN.match(tag):
                release_tags[tag] = v["name"]  # digest

    if not release_tags:
        print("No release tags found, nothing to check.")
        return 0
    print(f"Release tags to check: {sorted(release_tags)}")

    registry_token = get_registry_token()

    to_restore: dict[str, tuple[str, int]] = {}
    unrecoverable: list[str] = []
    for tag, digest in sorted(release_tags.items()):
        manifest = fetch_manifest(registry_token, digest)
        if manifest is None:
            print(f"  ! could not fetch manifest for tag {tag} ({digest}) - skipping")
            continue
        children = [m["digest"] for m in manifest.get("manifests", [])]
        for child in [digest, *children]:
            if child in to_restore or child in unrecoverable:
                continue
            if fetch_manifest(registry_token, child) is not None:
                continue  # still reachable, nothing to do
            deleted_version = deleted_by_digest.get(child)
            if deleted_version is None:
                unrecoverable.append(child)
                print(
                    f"  ! {child} (referenced by {tag}) is unreachable and "
                    "not present among deleted versions - cannot auto-restore"
                )
                continue
            to_restore[child] = (tag, deleted_version["id"])

    if not to_restore:
        print("Nothing to restore.")
        return 1 if unrecoverable else 0

    for digest, (tag, version_id) in to_restore.items():
        action = "[dry-run] would restore" if DRY_RUN else "Restoring"
        print(f"{action} {digest} (referenced by tag {tag}, version_id={version_id})")
        if not DRY_RUN:
            gh_api(
                f"/orgs/{ORG}/packages/container/{PACKAGE}/versions/{version_id}/restore",
                method="POST",
            )

    verb = "Would restore" if DRY_RUN else "Restored"
    print(f"Done. {verb} {len(to_restore)} image(s).")
    return 1 if unrecoverable else 0


if __name__ == "__main__":
    sys.exit(main())
