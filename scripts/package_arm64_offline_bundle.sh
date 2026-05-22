#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RELEASE_NAME="echox-call-arm64-cpu-offline-$(date +%Y%m%d%H%M%S)"
OUT_DIR="$ROOT_DIR/dist/$RELEASE_NAME"
INCLUDE_DATA=0
SKIP_BUILD=0
SKIP_POSTGRES=0
ALLOW_NON_ARM=0

usage() {
  cat <<'EOF'
Usage:
  scripts/package_arm64_offline_bundle.sh [options]

Run this script on an online ARM64/aarch64 Linux server. It builds the ARM64
Docker image, saves it, and creates a single offline deployment archive that
also contains the project files and local models/.

Options:
  --include-data       Include runtime data/ in the project archive.
  --skip-build         Do not build echox-call:cpu before saving it.
  --skip-postgres      Do not pull/save postgres:16.
  --allow-non-arm      Continue even if uname -m is not aarch64/arm64.
  -h, --help           Show this help.

Output:
  dist/<release-name>/
    echox-call-arm64-cpu-image.tar.gz
    postgres16-arm64-image.tar.gz
    echox-call-project-with-models.tar.gz
    install_offline.sh
    SHA256SUMS
  dist/<release-name>.tar.gz
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-data)
      INCLUDE_DATA=1
      ;;
    --skip-build)
      SKIP_BUILD=1
      ;;
    --skip-postgres)
      SKIP_POSTGRES=1
      ;;
    --allow-non-arm)
      ALLOW_NON_ARM=1
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

ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
  if [[ "$ALLOW_NON_ARM" -eq 0 ]]; then
    echo "error: current machine is $ARCH, not ARM64. Run on an ARM64/aarch64 server." >&2
    echo "       If you intentionally use buildx/qemu, rerun with --allow-non-arm." >&2
    exit 1
  fi
  echo "warning: current machine is $ARCH, not ARM64. Make sure the saved image is linux/arm64." >&2
fi

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required" >&2
  exit 1
}

COMPOSE=()
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "error: docker compose plugin or docker-compose is required" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  if [[ "${COMPOSE[0]} ${COMPOSE[1]:-}" == "docker compose" ]]; then
    "${COMPOSE[@]}" build api
  else
    docker build -t echox-call:cpu .
  fi
fi

IMAGE_ARCH="$(docker image inspect echox-call:cpu --format '{{.Architecture}}' 2>/dev/null || true)"
if [[ "$IMAGE_ARCH" != "arm64" ]]; then
  if [[ "$ALLOW_NON_ARM" -eq 0 ]]; then
    echo "error: echox-call:cpu image architecture is '$IMAGE_ARCH', expected 'arm64'." >&2
    exit 1
  fi
  echo "warning: echox-call:cpu image architecture is '$IMAGE_ARCH', expected 'arm64'." >&2
fi

docker save echox-call:cpu | gzip > "$OUT_DIR/echox-call-arm64-cpu-image.tar.gz"

if [[ "$SKIP_POSTGRES" -eq 0 ]]; then
  docker pull --platform linux/arm64 postgres:16
  docker save postgres:16 | gzip > "$OUT_DIR/postgres16-arm64-image.tar.gz"
fi

PROJECT_ITEMS=(
  docker-compose.yml
  docker-compose.legacy.yml
  Dockerfile
  requirements.txt
  README.md
  offline_deploy.txt
  .dockerignore
  .env.docker.example
  config
  docs
  migrations
  scripts
  src
  tests
  third_party
  models
)

if [[ -f .env.docker ]]; then
  PROJECT_ITEMS+=(.env.docker)
fi

if [[ "$INCLUDE_DATA" -eq 1 ]]; then
  PROJECT_ITEMS+=(data)
fi

tar \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='._*' \
  --exclude='.DS_Store' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  -czf "$OUT_DIR/echox-call-project-with-models.tar.gz" \
  "${PROJECT_ITEMS[@]}"

cat > "$OUT_DIR/install_offline.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${1:-/opt/echox-call}"

mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

docker load < "$ROOT_DIR/echox-call-arm64-cpu-image.tar.gz"
if [[ -f "$ROOT_DIR/postgres16-arm64-image.tar.gz" ]]; then
  docker load < "$ROOT_DIR/postgres16-arm64-image.tar.gz"
fi

tar -xzf "$ROOT_DIR/echox-call-project-with-models.tar.gz" -C "$DEPLOY_DIR"
mkdir -p data/postcall data/console_uploads data/logs

if [[ ! -f .env.docker ]]; then
  cp .env.docker.example .env.docker
  echo "created .env.docker from .env.docker.example"
fi

cp .env.docker .env

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose --env-file .env.docker"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose -f docker-compose.legacy.yml"
else
  COMPOSE_CMD=""
fi

echo "offline files installed to $DEPLOY_DIR"
echo
echo "next:"
echo "  cd $DEPLOY_DIR"
echo "  vi .env.docker"
if [[ -n "$COMPOSE_CMD" ]]; then
  echo "  $COMPOSE_CMD run --rm migrate"
  echo "  $COMPOSE_CMD up -d api worker llm-worker console"
else
  echo "  install docker compose, then run migrations and services"
fi
EOF
chmod +x "$OUT_DIR/install_offline.sh"

(
  cd "$OUT_DIR"
  sha256sum *.tar.gz install_offline.sh > SHA256SUMS
)

tar -cf - -C "$ROOT_DIR/dist" "$RELEASE_NAME" \
  | gzip -1 > "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz.partial"
mv "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz.partial" "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz"

echo "created:"
echo "  $OUT_DIR"
echo "  $ROOT_DIR/dist/$RELEASE_NAME.tar.gz"
