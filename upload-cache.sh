#!/usr/bin/env bash
# Copy this machine's cache onto the Google Compute Engine VM, replacing the
# copy the service reads. Google login files already on the VM are kept.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTANCE="${GMF_INSTANCE:-instance-20261104-105419}"
ZONE="${GMF_ZONE:-europe-west4-a}"
REMOTE_USER="${GMF_USER:-hugo22226}"
REMOTE_DIR="${GMF_REMOTE_DIR:-/home/hugo22226/nonstop-good-movie-finder}"
TARGET="${REMOTE_USER}@${INSTANCE}"
REMOTE_CACHE="${REMOTE_DIR}/cache"
STAGING="${REMOTE_DIR}/cache.upload"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed or not on PATH." >&2
  exit 1
fi
if [[ ! -d "$ROOT/cache" ]]; then
  echo "No cache directory at $ROOT/cache" >&2
  exit 1
fi

gssh() {
  gcloud compute ssh "$TARGET" --zone="$ZONE" --quiet --command "$1"
}

stopped=0
start_service() {
  gssh "sudo -n systemctl start goodmoviefinder"
  stopped=0
}
cleanup() {
  status=$?
  if [[ "$stopped" -eq 1 ]]; then
    echo "Upload did not finish. Starting the service again with the cache still on the VM." >&2
    gssh "sudo -n systemctl start goodmoviefinder" || true
  fi
  exit "$status"
}
trap cleanup EXIT

echo "Stopping goodmoviefinder on ${INSTANCE}..."
gssh "sudo -n systemctl stop goodmoviefinder"
stopped=1

echo "Preparing a fresh cache on the VM..."
gssh "rm -rf '${STAGING}' && mkdir -p '${STAGING}' '${REMOTE_CACHE}'"

shopt -s nullglob
upload=()
for path in "$ROOT/cache"/*; do
  base="$(basename "$path")"
  [[ -f "$path" ]] || continue
  case "$base" in
    calendar-request.json|calendar-busy-result.json|google-client.json|google-token.json|friends-test.json|friends-test2.json)
      continue
      ;;
  esac
  upload+=("$path")
done
shopt -u nullglob

if ((${#upload[@]} == 0)); then
  echo "Nothing to upload in $ROOT/cache" >&2
  exit 1
fi

echo "Uploading ${#upload[@]} files..."
gcloud compute scp --zone="$ZONE" "${upload[@]}" "${TARGET}:${STAGING}/"

if [[ -f "$ROOT/viewer.json" ]]; then
  echo "Uploading viewer.json..."
  gcloud compute scp --zone="$ZONE" "$ROOT/viewer.json" "${TARGET}:${REMOTE_DIR}/viewer.json"
fi

echo "Replacing ${REMOTE_CACHE}..."
gssh "set -euo pipefail
for name in google-client.json google-token.json; do
  if [[ -f '${REMOTE_CACHE}/'\$name ]]; then
    cp -a '${REMOTE_CACHE}/'\$name '${STAGING}/'
  fi
done
rm -rf '${REMOTE_DIR}/cache.previous'
mv '${REMOTE_CACHE}' '${REMOTE_DIR}/cache.previous'
mv '${STAGING}' '${REMOTE_CACHE}'
rm -rf '${REMOTE_DIR}/cache.previous'
"

echo "Starting goodmoviefinder..."
start_service
gssh "sudo -n systemctl is-active goodmoviefinder"
trap - EXIT

echo "Cache replaced at ${REMOTE_CACHE}"
