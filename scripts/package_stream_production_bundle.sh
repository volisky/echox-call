#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RELEASE_NAME="echox-call-stream-production-offline-$(date +%Y%m%d%H%M%S)"
OUT_DIR="$ROOT_DIR/dist/$RELEASE_NAME"
INCLUDE_DATA=0
SKIP_IMAGE_SAVE=0

usage() {
  cat <<'EOF'
Usage:
  scripts/package_stream_production_bundle.sh [options]

Creates an offline production bundle containing:
  - echox-call:stream-cuda image
  - postgres:16 image
  - source code, config, docs, migrations, third_party, models
  - one-step deploy.sh
  - production offline deployment guide

Options:
  --include-data       Include runtime data/ in the project archive.
  --skip-image-save    Reuse existing files in the output images/ directory.
  --release-name NAME  Override generated release name.
  -h, --help           Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-data)
      INCLUDE_DATA=1
      ;;
    --skip-image-save)
      SKIP_IMAGE_SAVE=1
      ;;
    --release-name)
      RELEASE_NAME="${2:?missing value for --release-name}"
      OUT_DIR="$ROOT_DIR/dist/$RELEASE_NAME"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required" >&2
  exit 1
}

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "warning: current machine is $(uname -m), expected x86_64 for H100/x86 deployment" >&2
fi

docker image inspect echox-call:stream-cuda >/dev/null 2>&1 || {
  echo "error: missing image echox-call:stream-cuda" >&2
  exit 1
}
docker image inspect postgres:16 >/dev/null 2>&1 || {
  echo "error: missing image postgres:16" >&2
  exit 1
}

mkdir -p "$OUT_DIR/images" "$OUT_DIR/project"

if [[ "$SKIP_IMAGE_SAVE" -eq 0 ]]; then
  echo "saving echox-call:stream-cuda..."
  docker save echox-call:stream-cuda | gzip -1 > "$OUT_DIR/images/echox-call-stream-cuda.tar.gz"
  echo "saving postgres:16..."
  docker save postgres:16 | gzip -1 > "$OUT_DIR/images/postgres16.tar.gz"
fi

PROJECT_ITEMS=(
  .dockerignore
  .env.docker.example
  Dockerfile
  Dockerfile.cuda
  README.md
  config
  deploy
  docker-compose.yml
  docker-compose.cuda.yml
  docs
  migrations
  requirements.txt
  scripts
  src
  tests
  third_party
  tools
  models
)

if [[ "$INCLUDE_DATA" -eq 1 ]]; then
  PROJECT_ITEMS+=(data)
fi

echo "packing project and models..."
tar \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='._*' \
  --exclude='.DS_Store' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='dist' \
  -czf "$OUT_DIR/project/echox-call-project-with-models.tar.gz" \
  "${PROJECT_ITEMS[@]}"

cp deploy/offline/deploy.sh "$OUT_DIR/deploy.sh"
cp docs/production-offline-deployment.md "$OUT_DIR/README-production-offline.md"
chmod +x "$OUT_DIR/deploy.sh"

cat > "$OUT_DIR/MANIFEST.txt" <<EOF
Release: $RELEASE_NAME
CreatedAt: $(date -Is)
Images:
  echox-call:stream-cuda
  postgres:16
Ports:
  API / realtime push: 8000
  Console: 8002
  PostgreSQL: 5432
EOF

(
  cd "$OUT_DIR"
  sha256sum images/*.tar.gz project/*.tar.gz deploy.sh README-production-offline.md MANIFEST.txt > SHA256SUMS
)

echo "creating outer archive..."
tar -cf - -C "$ROOT_DIR/dist" "$RELEASE_NAME" \
  | gzip -1 > "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz.partial"
mv "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz.partial" "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz"

echo "created:"
echo "  $OUT_DIR"
echo "  $ROOT_DIR/dist/$RELEASE_NAME.tar.gz"
