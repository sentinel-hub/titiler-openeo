"""docker/push-tags.sh, which the image workflows use to push every tag."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "push-tags.sh"
TAGS = ["ghcr.io/org/app:1.2.3", "ghcr.io/org/app:v1.2.3", "ghcr.io/org/app:latest"]

# Stands in for `docker push TAG`: logs the tag, then fails while the tag's
# failure budget in $FAIL_DIR lasts, as GHCR did with "unknown blob".
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$2" >> "$LOG"
budget="$FAIL_DIR/$(printf '%s' "$2" | tr '/:' '__')"
if [ -s "$budget" ] && [ "$(cat "$budget")" -gt 0 ]; then
  echo $(( $(cat "$budget") - 1 )) > "$budget"
  echo "unknown blob" >&2
  exit 1
fi
"""


def _push(tmp_path, failures):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    fail_dir = tmp_path / "fail"
    fail_dir.mkdir()
    for tag, count in failures.items():
        (fail_dir / tag.replace("/", "_").replace(":", "_")).write_text(str(count))
    log = tmp_path / "pushes.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "LOG": str(log),
        "FAIL_DIR": str(fail_dir),
        "PUSH_RETRY_DELAY": "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), *TAGS], env=env, capture_output=True, text=True
    )
    return result, log.read_text().split() if log.exists() else []


def test_retries_a_failed_push(tmp_path):
    result, pushes = _push(tmp_path, {TAGS[0]: 1})
    assert result.returncode == 0, result.stdout + result.stderr
    assert pushes == [TAGS[0], TAGS[0], TAGS[1], TAGS[2]]


def test_fails_after_three_attempts_but_pushes_the_other_tags(tmp_path):
    result, pushes = _push(tmp_path, {TAGS[0]: 99})
    assert result.returncode != 0
    assert pushes == [TAGS[0]] * 3 + TAGS[1:]
    assert f"::error::docker push {TAGS[0]} failed" in result.stdout
