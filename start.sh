#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

load_env_file() {
  local env_file="$ROOT_DIR/.env"
  [[ -f "$env_file" ]] || return 0

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue

    key="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"
    value="${value%$'\r'}"

    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi

    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$env_file"
}

load_env_file

CONDA_ENV_NAME="${ECHOX_CALL_CONDA_ENV:-echox-call}"
DEFAULT_HOST="${ECHOX_CALL_HOST:-127.0.0.1}"
DEFAULT_PORT="${ECHOX_CALL_PORT:-8000}"
DEFAULT_CONSOLE_HOST="${ECHOX_CALL_CONSOLE_HOST:-127.0.0.1}"
DEFAULT_CONSOLE_PORT="${ECHOX_CALL_CONSOLE_PORT:-8001}"
KILL_PORTS="${ECHOX_CALL_KILL_PORTS:-1}"
WORKER_COUNT="${ECHOX_CALL_WORKER_COUNT:-1}"
WORKER_ID_PREFIX="${ECHOX_CALL_WORKER_ID_PREFIX:-postcall-worker}"
LLM_WORKER_COUNT="${ECHOX_CALL_LLM_WORKER_COUNT:-1}"
LLM_WORKER_ID_PREFIX="${ECHOX_CALL_LLM_WORKER_ID_PREFIX:-llm-worker}"
WORKER_PIDS=()

usage() {
  cat <<'EOF'
Usage:
  ./start.sh                        Start API server on 127.0.0.1:8000
  ./start.sh api [args...]          Start API server
  ./start.sh console [args...]      Start management console on 127.0.0.1:8001
  ./start.sh worker [args...]       Run postcall audio analysis worker
  ./start.sh llm-worker [args...]   Run postcall LLM analysis worker
  ./start.sh all [args...]          Apply migrations, then start both workers + API
  ./start.sh migrate                Apply database migrations
  ./start.sh ping                   Check database connection
  ./start.sh install                Install Python dependencies

Examples:
  ./start.sh
  ECHOX_CALL_PORT=9000 ./start.sh api
  ECHOX_CALL_CONSOLE_PORT=9001 ./start.sh console
  ECHOX_CALL_KILL_PORTS=0 ./start.sh api
  ECHOX_CALL_WORKER_COUNT=2 ./start.sh worker
  ECHOX_CALL_LLM_WORKER_COUNT=2 ./start.sh llm-worker
  ./start.sh api --host 0.0.0.0 --port 8000
  ./start.sh console
  ./start.sh worker --once
  ./start.sh llm-worker --once
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

select_python() {
  if [[ "${CONDA_DEFAULT_ENV:-}" == "$CONDA_ENV_NAME" ]]; then
    PYTHON_CMD=(python)
    return
  fi

  if command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -Fxq "$CONDA_ENV_NAME"; then
    PYTHON_CMD=(conda run --no-capture-output -n "$CONDA_ENV_NAME" python)
    return
  fi

  if command -v python >/dev/null 2>&1; then
    PYTHON_CMD=(python)
    return
  fi

  die "python not found. Activate the ${CONDA_ENV_NAME} environment first."
}

run_python() {
  "${PYTHON_CMD[@]}" "$@"
}

is_kill_ports_enabled() {
  local normalized
  normalized="$(printf '%s' "$KILL_PORTS" | tr '[:upper:]' '[:lower:]')"

  case "$normalized" in
    0|false|no|off)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

resolve_port() {
  local port="$1"
  shift

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --port)
        shift
        [[ $# -gt 0 ]] || die "--port requires a value"
        port="$1"
        ;;
      --port=*)
        port="${1#--port=}"
        ;;
    esac
    shift
  done

  [[ "$port" =~ ^[0-9]+$ ]] || die "port must be an integer, got ${port}"
  echo "$port"
}

has_help_arg() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        return 0
        ;;
    esac
    shift
  done
  return 1
}

has_once_arg() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --once)
        return 0
        ;;
    esac
    shift
  done
  return 1
}

has_worker_id_arg() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --worker-id|--worker-id=*)
        return 0
        ;;
    esac
    shift
  done
  return 1
}

resolve_worker_count() {
  local count="$1"
  [[ "$count" =~ ^[0-9]+$ ]] || die "ECHOX_CALL_WORKER_COUNT must be a positive integer, got ${count}"
  [[ "$count" -ge 1 ]] || die "ECHOX_CALL_WORKER_COUNT must be greater than 0"
  echo "$count"
}

start_workers_background() {
  local count
  count="$(resolve_worker_count "$WORKER_COUNT")"
  WORKER_PIDS=()

  if [[ "$count" -gt 1 ]] && has_worker_id_arg "$@"; then
    die "do not pass --worker-id when ECHOX_CALL_WORKER_COUNT is greater than 1"
  fi

  if [[ "$count" -eq 1 ]]; then
    run_python -m echox_call.cli.worker "$@" &
    WORKER_PIDS=("$!")
    echo "started worker pid=${WORKER_PIDS[0]}"
    return 0
  fi

  echo "starting ${count} workers"
  local index worker_id pid
  for ((index = 1; index <= count; index++)); do
    worker_id="${WORKER_ID_PREFIX}-${index}"
    run_python -m echox_call.cli.worker "$@" --worker-id "$worker_id" &
    pid="$!"
    WORKER_PIDS+=("$pid")
    echo "started worker ${index}/${count}: pid=${pid} workerId=${worker_id}"
  done
}

cleanup_workers() {
  local pid
  for pid in "${WORKER_PIDS[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  for pid in "${WORKER_PIDS[@]}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
}

wait_workers() {
  local status=0
  local pid
  for pid in "${WORKER_PIDS[@]}"; do
    wait "$pid" || status="$?"
  done
  return "$status"
}

run_workers_foreground() {
  if has_help_arg "$@" || has_once_arg "$@"; then
    run_python -m echox_call.cli.worker "$@"
    return
  fi

  local count
  count="$(resolve_worker_count "$WORKER_COUNT")"
  if [[ "$count" -eq 1 ]]; then
    run_python -m echox_call.cli.worker "$@"
    return
  fi

  start_workers_background "$@"
  trap cleanup_workers EXIT INT TERM
  wait_workers
}

kill_port_listeners() {
  local port="$1"

  is_kill_ports_enabled || return 0

  if ! command -v lsof >/dev/null 2>&1; then
    echo "warning: lsof not found, cannot clear port ${port}" >&2
    return 0
  fi

  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
  [[ -n "$pids" ]] || return 0

  echo "port ${port} is occupied, killing listener pids: $(echo "$pids" | tr '\n' ' ')"
  kill $pids >/dev/null 2>&1 || true
  sleep 0.5

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
  if [[ -n "$pids" ]]; then
    echo "port ${port} still occupied, force killing listener pids: $(echo "$pids" | tr '\n' ' ')"
    kill -9 $pids >/dev/null 2>&1 || true
    sleep 0.2
  fi

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
  [[ -z "$pids" ]] || die "port ${port} is still occupied by pids: $(echo "$pids" | tr '\n' ' ')"
}

ACTION="${1:-api}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$ACTION" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

select_python

case "$ACTION" in
  install)
    run_python -m pip install -r requirements.txt
    ;;

  ping)
    run_python -m echox_call.cli.db ping
    ;;

  migrate)
    run_python -m echox_call.cli.db migrate
    ;;

  api)
    if [[ $# -eq 0 ]]; then
      set -- --host "$DEFAULT_HOST" --port "$DEFAULT_PORT" --reload
    fi
    has_help_arg "$@" || kill_port_listeners "$(resolve_port "$DEFAULT_PORT" "$@")"
    run_python -m echox_call.cli.api "$@"
    ;;

  console)
    if [[ $# -eq 0 ]]; then
      set -- --host "$DEFAULT_CONSOLE_HOST" --port "$DEFAULT_CONSOLE_PORT" --reload
    fi
    has_help_arg "$@" || kill_port_listeners "$(resolve_port "$DEFAULT_CONSOLE_PORT" "$@")"
    run_python -m echox_call.cli.console "$@"
    ;;

  worker)
    if [[ $# -eq 0 ]]; then
      set -- --loop
    fi
    run_workers_foreground "$@"
    ;;

  llm-worker)
    if [[ $# -eq 0 ]]; then
      set -- --loop
    fi
    if has_help_arg "$@" || has_once_arg "$@"; then
      run_python -m echox_call.cli.llm_worker "$@"
    else
      local llm_count
      llm_count="$(resolve_worker_count "$LLM_WORKER_COUNT")"
      if [[ "$llm_count" -eq 1 ]]; then
        run_python -m echox_call.cli.llm_worker "$@"
      else
        echo "starting ${llm_count} LLM workers"
        local llm_pids=()
        for ((i = 1; i <= llm_count; i++)); do
          local wid="${LLM_WORKER_ID_PREFIX}-${i}"
          run_python -m echox_call.cli.llm_worker "$@" --worker-id "$wid" &
          llm_pids+=("$!")
          echo "started LLM worker ${i}/${llm_count}: pid=${llm_pids[-1]} workerId=${wid}"
        done
        trap 'for p in "${llm_pids[@]}"; do kill "$p" 2>/dev/null || true; done' EXIT INT TERM
        for p in "${llm_pids[@]}"; do wait "$p" || true; done
      fi
    fi
    ;;

  all)
    run_python -m echox_call.cli.db migrate
    start_workers_background --loop
    run_python -m echox_call.cli.llm_worker --loop &
    LLM_ALL_PID="$!"
    trap 'cleanup_workers; kill "$LLM_ALL_PID" 2>/dev/null || true' EXIT INT TERM

    if [[ $# -eq 0 ]]; then
      set -- --host "$DEFAULT_HOST" --port "$DEFAULT_PORT" --reload
    fi
    has_help_arg "$@" || kill_port_listeners "$(resolve_port "$DEFAULT_PORT" "$@")"
    run_python -m echox_call.cli.api "$@"
    ;;

  *)
    usage
    exit 2
    ;;
esac
