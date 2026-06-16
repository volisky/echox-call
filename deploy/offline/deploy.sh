#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/echox-call"
START_SERVICES=1
FORCE_ENV=0
START_LLM_WORKER="${START_LLM_WORKER:-0}"

usage() {
  cat <<'EOF'
Usage:
  ./deploy.sh [options]

Options:
  --install-dir DIR   Install project files to DIR. Default: /opt/echox-call
  --no-start          Load images and extract files, but do not start services.
  --force-env         Regenerate .env.docker even if it already exists.
  -h, --help          Show this help.

Environment:
  START_LLM_WORKER=1  Start llm-worker after migration. Default: 0.
  POSTGRES_PASSWORD   Password written to .env.docker. Default: LZdx@2025.
  POSTGRES_PASSWORD_URL_ENCODED
                      URL-encoded password for DATABASE_URL. Default: LZdx%402025.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="${2:?missing value for --install-dir}"
      shift
      ;;
    --no-start)
      START_SERVICES=0
      ;;
    --force-env)
      FORCE_ENV=1
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

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: $1 is required" >&2
    exit 1
  }
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    echo "error: docker compose plugin or docker-compose is required" >&2
    exit 1
  fi
}

write_default_env() {
  local password="$1"
  local password_url_encoded="$2"
  cat > .env.docker <<EOF
POSTGRES_DB=echox_call
POSTGRES_ADMIN_DB=postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_BIND_HOST=0.0.0.0
POSTGRES_HOST_PORT=5432
POSTGRES_USER=echox
POSTGRES_PASSWORD=${password}
DATABASE_URL=postgresql://echox:${password_url_encoded}@postgres:5432/echox_call

ECHOX_CALL_HOST=0.0.0.0
ECHOX_CALL_PORT=8000
ECHOX_CALL_CONSOLE_HOST=0.0.0.0
ECHOX_CALL_CONSOLE_PORT=8002

POSTCALL_DEVICE=cuda
POSTCALL_ANALYSIS_PROFILE=fast
POSTCALL_STORAGE_DIR=/app/data/postcall
POSTCALL_STORAGE_TIMEZONE=Asia/Shanghai
POSTCALL_WORKER_BATCH_SIZE=1
POSTCALL_WORKER_LOCK_SECONDS=600
POSTCALL_WORKER_SKIP_CALL_ID_JOBS=1
POSTCALL_AUDIO_MAX_BYTES=104857600
POSTCALL_AUDIO_MAX_DURATION_SEC=600
POSTCALL_AUDIO_DOWNLOAD_TIMEOUT_SEC=30
POSTCALL_AUDIO_EVENT_TOP_K=20
POSTCALL_TORCH_NUM_THREADS=4
POSTCALL_TORCH_INTEROP_THREADS=1

POSTCALL_BEATS_CHECKPOINT_PATH=/app/models/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt
POSTCALL_BEATS_LABELS_PATH=/app/docs/postcall-beats-audioset-labels.csv
POSTCALL_WAVLM_EMOTION_MODEL_DIR=/app/models/wavlm-large-categorical-emotion
POSTCALL_WAVLM_BACKBONE_DIR=/app/models/wavlm-large
POSTCALL_WAVLM_LABELS_PATH=/app/docs/postcall-wavlm-output-labels.csv
POSTCALL_DIARIZATION_MODEL_DIR=/app/models/speaker-diarization-community-1
POSTCALL_DIARIZATION_NUM_SPEAKERS=2
POSTCALL_ATTENTION_RULES_PATH=/app/config/postcall_attention_rules.yaml

POSTCALL_REALTIME_STORAGE_DIR=/app/data/realtime
POSTCALL_REALTIME_WINDOW_SEC=10
POSTCALL_REALTIME_TAIL_MIN_SEC=3
POSTCALL_REALTIME_WORKER_BATCH_SIZE=1
POSTCALL_REALTIME_WORKER_SLEEP_SECONDS=2.0

CLIENTS_CONFIG_PATH=/app/config/clients.yaml
CONSOLE_USERS_CONFIG_PATH=/app/config/console_users.yaml

NVIDIA_VISIBLE_DEVICES=all
NVIDIA_DRIVER_CAPABILITIES=compute,utility

LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.modelarts-maas.com/openai/v1
LLM_WORKER_MODEL=deepseek-v4-flash
LLM_WORKER_MAX_TOKENS=1024
LLM_WORKER_BATCH_SIZE=3
LLM_WORKER_USE_TOOLS=1
EOF
  chmod 600 .env.docker
}

need_cmd docker
COMPOSE="$(compose_cmd)"

echo "install dir: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

if [[ -f "$BUNDLE_DIR/SHA256SUMS" ]]; then
  echo "checking SHA256SUMS..."
  (cd "$BUNDLE_DIR" && sha256sum -c SHA256SUMS)
fi

echo "loading docker images..."
gzip -dc "$BUNDLE_DIR/images/echox-call-stream-cuda.tar.gz" | docker load
gzip -dc "$BUNDLE_DIR/images/postgres16.tar.gz" | docker load

echo "extracting project files..."
tar -xzf "$BUNDLE_DIR/project/echox-call-project-with-models.tar.gz" -C "$INSTALL_DIR"

cd "$INSTALL_DIR"
mkdir -p data/postcall data/realtime data/console_uploads data/logs

if [[ "$FORCE_ENV" -eq 1 || ! -f .env.docker ]]; then
  write_default_env "${POSTGRES_PASSWORD:-LZdx@2025}" "${POSTGRES_PASSWORD_URL_ENCODED:-LZdx%402025}"
  echo "created .env.docker"
else
  echo "kept existing .env.docker"
fi

COMPOSE_ARGS=(
  --env-file .env.docker
  -f deploy/offline/docker-compose.production.yml
)

if [[ "$START_SERVICES" -eq 0 ]]; then
  echo "installed only. Edit $INSTALL_DIR/.env.docker, then start with:"
  echo "  cd $INSTALL_DIR"
  echo "  $COMPOSE ${COMPOSE_ARGS[*]} up -d postgres"
  echo "  $COMPOSE ${COMPOSE_ARGS[*]} --profile tools run --rm migrate"
  echo "  $COMPOSE ${COMPOSE_ARGS[*]} up -d api worker realtime-worker console"
  exit 0
fi

echo "starting postgres..."
$COMPOSE "${COMPOSE_ARGS[@]}" up -d postgres

echo "running migrations..."
$COMPOSE "${COMPOSE_ARGS[@]}" --profile tools run --rm migrate

echo "starting application services..."
SERVICES=(api worker realtime-worker console)
if [[ "$START_LLM_WORKER" == "1" ]]; then
  SERVICES+=(llm-worker)
fi
$COMPOSE "${COMPOSE_ARGS[@]}" up -d "${SERVICES[@]}"

echo
echo "deployment complete."
echo "API:      http://<server-ip>:${ECHOX_CALL_PORT:-8000}"
echo "Stream:   http://<server-ip>:${ECHOX_CALL_PORT:-8000}/gateway/asr/push"
echo "Console:  http://<server-ip>:${ECHOX_CALL_CONSOLE_PORT:-8002}/console/"
echo
echo "status:"
$COMPOSE "${COMPOSE_ARGS[@]}" ps
