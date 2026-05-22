#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RELEASE_NAME="echox-call-x86cpu-offline-$(date +%Y%m%d%H%M%S)"
OUT_DIR="$ROOT_DIR/dist/$RELEASE_NAME"
INCLUDE_DATA=0
SKIP_BUILD=0
SKIP_POSTGRES=0

usage() {
  cat <<'EOF'
Usage:
  scripts/package_offline_bundle.sh [options]

Options:
  --include-data      Include runtime data/ in the project archive.
  --skip-build        Do not build echox-call:cpu before saving it.
  --skip-postgres     Do not pull/save postgres:16.
  -h, --help          Show this help.

Output:
  dist/<release-name>/
    echox-call-cpu-image.tar.gz
    postgres16-image.tar.gz
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

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "warning: current machine is $(uname -m), not x86_64. Build on x86_64 for x86 CPU deployment." >&2
fi

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required" >&2
  exit 1
}

if ! docker compose version >/dev/null 2>&1; then
  echo "error: docker compose plugin is required" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

COMPOSE_ENV_ARGS=()
if [[ -f .env.docker ]]; then
  COMPOSE_ENV_ARGS=(--env-file .env.docker)
else
  echo "warning: .env.docker not found; using .env.docker.example defaults during build" >&2
fi

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  docker compose "${COMPOSE_ENV_ARGS[@]}" build api
fi

docker save echox-call:cpu | gzip > "$OUT_DIR/echox-call-cpu-image.tar.gz"

if [[ "$SKIP_POSTGRES" -eq 0 ]]; then
  docker pull postgres:16
  docker save postgres:16 | gzip > "$OUT_DIR/postgres16-image.tar.gz"
fi

PROJECT_ITEMS=(
  docker-compose.yml
  Dockerfile
  requirements.txt
  README.md
  offline_deploy.txt
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

tar -czf "$OUT_DIR/echox-call-project-with-models.tar.gz" "${PROJECT_ITEMS[@]}"

cat > "$OUT_DIR/install_offline.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${1:-/opt/echox-call}"

mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

docker load < "$ROOT_DIR/echox-call-cpu-image.tar.gz"
if [[ -f "$ROOT_DIR/postgres16-image.tar.gz" ]]; then
  docker load < "$ROOT_DIR/postgres16-image.tar.gz"
fi

tar -xzf "$ROOT_DIR/echox-call-project-with-models.tar.gz" -C "$DEPLOY_DIR"
mkdir -p data/postcall data/console_uploads

if [[ ! -f .env.docker ]]; then
  cp .env.docker.example .env.docker
  echo "created .env.docker from .env.docker.example; edit database and LLM settings before starting services"
fi

echo "offline files installed to $DEPLOY_DIR"
echo "next:"
echo "  cd $DEPLOY_DIR"
echo "  docker compose --env-file .env.docker run --rm migrate"
echo "  docker compose --env-file .env.docker up -d api worker llm-worker console"
EOF
chmod +x "$OUT_DIR/install_offline.sh"

(
  cd "$OUT_DIR"
  sha256sum *.tar.gz install_offline.sh > SHA256SUMS
)

# The release directory already contains compressed image/model archives, so
# the outer gzip mainly gives a single portable file. Write through a partial
# path and rename at the end so interrupted runs do not look complete.
tar -cf - -C "$ROOT_DIR/dist" "$RELEASE_NAME" \
  | gzip -1 > "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz.partial"
mv "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz.partial" "$ROOT_DIR/dist/$RELEASE_NAME.tar.gz"

echo "created:"
echo "  $OUT_DIR"
echo "  $ROOT_DIR/dist/$RELEASE_NAME.tar.gz"
