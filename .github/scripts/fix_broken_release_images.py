#!/usr/bin/env python3
"""Fix broken release image tags broken."""

import base64
import json
import os
import re
import subprocess
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


def restore_version(version_id: int) -> None:
    gh_api(
        f"/orgs/{ORG}/packages/container/{PACKAGE}/versions/{version_id}/restore",
        method="POST",
    )


def rebuild_and_push(tag: str) -> None:
    worktree = f"/tmp/rebuild-{tag}"
    subprocess.run(
        ["git", "worktree", "add", "--detach", worktree, tag], check=True
    )
    try:
        image = f"ghcr.io/{ORG}/{PACKAGE}:{tag}"
        subprocess.run(
            ["docker", "buildx", "build", "--push", "-t", image, worktree],
            check=True,
        )
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree], check=True
        )


def main() -> int:
    print(
        f"Fixing broken release images for {ORG}/{PACKAGE} (dry_run={DRY_RUN})"
    )

    active = list_package_versions("active")
    deleted = list_package_versions("deleted")
    deleted_by_digest = {v["name"]: v for v in deleted}
    print(f"{len(active)} active version(s), {len(deleted)} deleted version(s)")

    release_tags: dict[str, str] = {}
    for v in active:
        for tag in v.get("metadata", {}).get("container", {}).get("tags", []):
            if VERSION_PATTERN.match(tag):
                release_tags[tag] = v["name"]  # digest

    if not release_tags:
        print("No release tags found, nothing to check.")
        return 0
    print(f"Release tags to check: {sorted(release_tags)}")

    registry_token = get_registry_token()
    reachable_cache: dict[str, bool] = {}

    def is_reachable(digest: str) -> bool:
        if digest not in reachable_cache:
            reachable_cache[digest] = (
                fetch_manifest(registry_token, digest) is not None
            )
        return reachable_cache[digest]

    to_restore: dict[str, tuple[str, int]] = {}
    to_rebuild: list[str] = []

    for tag, digest in sorted(release_tags.items()):
        manifest = fetch_manifest(registry_token, digest)
        if manifest is None:
            print(
                f"  ! could not fetch top-level manifest for tag {tag} ({digest}) - skipping"
            )
            continue
        children = [m["digest"] for m in manifest.get("manifests", [])]
        needs_rebuild = False
        for child in [digest, *children]:
            if is_reachable(child):
                continue
            deleted_version = deleted_by_digest.get(child)
            if deleted_version is None:
                needs_rebuild = True
                print(
                    f"  ! {child} (tag {tag}) is unreachable and gone from "
                    "GHCR's deleted-version list - will rebuild from source"
                )
                continue
            to_restore.setdefault(child, (tag, deleted_version["id"]))
        if needs_rebuild:
            to_rebuild.append(tag)

    for digest, (tag, version_id) in to_restore.items():
        action = "[dry-run] would restore" if DRY_RUN else "Restoring"
        print(f"{action} {digest} (tag {tag}, version_id={version_id})")
        if not DRY_RUN:
            restore_version(version_id)

    for tag in to_rebuild:
        action = (
            "[dry-run] would rebuild and push"
            if DRY_RUN
            else "Rebuilding and pushing"
        )
        print(f"{action} {tag} from its git ref")
        if not DRY_RUN:
            rebuild_and_push(tag)

    print(
        f"Done. {'Would restore' if DRY_RUN else 'Restored'} {len(to_restore)} "
        f"image(s), {'would rebuild' if DRY_RUN else 'rebuilt'} {len(to_rebuild)} tag(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
