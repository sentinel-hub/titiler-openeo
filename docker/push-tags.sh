#!/usr/bin/env bash
#
# push-tags.sh: push an already built image under each of its tags.
#
#   ./docker/push-tags.sh <tag>...
#
# A registry can reject a push it would accept a moment later. GHCR answered
# "unknown blob" to the first tag of a release right after its layers had
# uploaded, and that tag was never published. Pushing the same local image again
# is safe, so each tag gets three attempts. Every tag is attempted before the
# script fails, so one bad tag does not hold back the others.
#
# Env: PUSH_RETRY_DELAY  base seconds between attempts (default 10; tests use 0)

set -u

ATTEMPTS=3
DELAY="${PUSH_RETRY_DELAY:-10}"
status=0

for tag in "$@"; do
  for ((attempt = 1; ; attempt++)); do
    docker push "$tag" && break
    if ((attempt == ATTEMPTS)); then
      echo "::error::docker push $tag failed $ATTEMPTS times"
      status=1
      break
    fi
    echo "docker push $tag failed (attempt $attempt of $ATTEMPTS), retrying in $((DELAY * attempt))s"
    sleep $((DELAY * attempt))
  done
done

exit "$status"
