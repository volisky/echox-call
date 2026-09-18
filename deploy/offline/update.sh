#!/usr/bin/env bash
#
# 离线增量更新脚本（代码挂载式部署专用）。
#
# 适用部署形态：deploy/offline/docker-compose.production.yml
#   - 单一镜像 echox-call:stream-cuda 跑多个容器（api / worker / llm-worker / ...）
#   - 容器通过 x-app-volumes 把宿主机目录挂进去：src(ro) / migrations(ro) / docs(ro) /
#     config(ro) / models(ro) / data(rw) / third_party(ro)
#   - Dockerfile.cuda 只 COPY requirements.txt，镜像内不含业务代码
#
# 结论：只要 requirements.txt 没变，更新业务代码不需要重新构建镜像、不需要往内网
#       传镜像 tar。替换宿主机目录 + 重启容器即可。只有依赖变化时才需要新镜像。
#
# 用法见 --help。
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/echox-call"
SOURCE_DIR=""
ARCHIVE=""
PATHS="src"
SERVICES="all"
RUN_MIGRATE=0
RECREATE=0
DO_RESTART=1
DRY_RUN=0
KEEP=3
COMPOSE_FILE_REL="deploy/offline/docker-compose.production.yml"

log() { printf '[update] %s\n' "$*"; }
warn() { printf '[update][warn] %s\n' "$*" >&2; }
fail() {
  printf '[update][error] %s\n' "$*" >&2
  exit 1
}
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

usage() {
  cat <<'EOF'
离线增量更新：把新代码同步到安装目录，并重启受影响的容器。

Usage:
  ./update.sh --source DIR [options]
  ./update.sh --archive FILE [options]

Source（二选一，必填）:
  --source DIR   新代码根目录，需包含 <PATHS> 下的目录（通常是 src/）
  --archive FILE 新代码归档（.tar.gz）。可含一层顶层目录，会自动探测

Options:
  --install-dir DIR  安装目录。默认 /opt/echox-call
  --paths "src ..."  要同步的顶层目录，空格分隔。默认 "src"
                     改到 config/ 就写 "src config"，改到迁移就写 "src migrations"
  --services "..."   更新后重启的服务，空格分隔。默认 all
                     默认值是 all：从 compose 文件实时解析全部业务服务（排除 postgres/
                     migrate），这样以后新增服务也不会漏重启。没有容器的服务会被跳过，
                     不会被意外创建；只想重启个别服务时显式传服务名
  --migrate          同步后执行数据库迁移（migrations/ 有变化时用）
  --recreate         用 up -d --force-recreate 重建容器。改了 .env.docker 时必须用
  --no-restart       只同步代码，不重启任何容器
  --keep N           最多保留 N 份备份。默认 3
  --dry-run          只打印将要执行的操作，不改动任何文件
  -h, --help         显示本帮助

Examples:
  # 只更新 LLM 相关代码（本次 IfCase/CaseReason 场景）
  ./update.sh --archive /root/echox-update-20260917.tar.gz

  # 同步代码并跑迁移，同时重启全部业务容器
  ./update.sh --source /root/new-code --paths "src migrations" --migrate --services all

  # 先看会做什么，不动手
  ./update.sh --source /root/new-code --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE_DIR="${2:?missing value for --source}"
      shift
      ;;
    --archive)
      ARCHIVE="${2:?missing value for --archive}"
      shift
      ;;
    --install-dir)
      INSTALL_DIR="${2:?missing value for --install-dir}"
      shift
      ;;
    --paths)
      PATHS="${2:?missing value for --paths}"
      shift
      ;;
    --services)
      SERVICES="${2:?missing value for --services}"
      shift
      ;;
    --migrate)
      RUN_MIGRATE=1
      ;;
    --recreate)
      RECREATE=1
      ;;
    --no-restart)
      DO_RESTART=0
      ;;
    --keep)
      KEEP="${2:?missing value for --keep}"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h | --help)
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

[[ -n "$SOURCE_DIR" || -n "$ARCHIVE" ]] || {
  usage >&2
  fail "需要 --source 或 --archive"
}
[[ -z "$SOURCE_DIR" || -z "$ARCHIVE" ]] || fail "--source 与 --archive 只能给一个"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is required"
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    fail "docker compose plugin 或 docker-compose 未安装"
  fi
}

TMP_DIR=""
cleanup() {
  [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
  return 0
}
trap cleanup EXIT

# ---------------------------------------------------------------- 1. 定位来源
SRC_ROOT=""
prepare_source() {
  if [[ -n "$ARCHIVE" ]]; then
    [[ -f "$ARCHIVE" ]] || fail "归档不存在: $ARCHIVE"
    TMP_DIR="$(mktemp -d)"
    log "解包 $ARCHIVE"
    tar -xzf "$ARCHIVE" -C "$TMP_DIR"
    # 归档常见两种形态：① 顶层就是一个包装目录（内含 src/）② 顶层直接是 src/。
    # 只有前者需要下钻；下钻前先排除“该目录本身就是要同步的路径”这种情况，
    # 否则 --paths src 会去错误的位置找 src/src。
    local entries only base
    entries=$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 | wc -l)
    if [[ "$entries" -eq 1 ]]; then
      only="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1)"
      if [[ -d "$only" ]]; then
        base="$(basename "$only")"
        case " $PATHS " in
          *" $base "*) : ;;
          *) SRC_ROOT="$only" ;;
        esac
      fi
    fi
    [[ -n "$SRC_ROOT" ]] || SRC_ROOT="$TMP_DIR"
  else
    [[ -d "$SOURCE_DIR" ]] || fail "目录不存在: $SOURCE_DIR"
    SRC_ROOT="$(cd "$SOURCE_DIR" && pwd)"
  fi
  log "更新来源: $SRC_ROOT"
}

# ---------------------------------------------------------------- 2. 检查安装目录
[[ -d "$INSTALL_DIR" ]] || fail "安装目录不存在: $INSTALL_DIR"
cd "$INSTALL_DIR"
[[ -f "$COMPOSE_FILE_REL" ]] || fail "找不到 $INSTALL_DIR/$COMPOSE_FILE_REL"
[[ -f .env.docker ]] || warn ".env.docker 不存在，docker compose 可能无法解析变量"

need_cmd docker
COMPOSE="$(compose_cmd)"
COMPOSE_ARGS=(--env-file .env.docker -f "$COMPOSE_FILE_REL")

# ---------------------------------------------------------------- 3. 备份
QSTAMP="$(date +%Y%m%d-%H%M%S)"
backup_path() {
  local dest="$1"
  [[ -e "$dest" ]] || return 0
  local backup="${dest}.bak.${QSTAMP}"
  log "备份 $(basename "$dest") -> $(basename "$backup")"
  run cp -a "$dest" "$backup"
}

prune_backups() {
  local dest="$1" backups
  backups="$(ls -1dt "${dest}".bak.* 2>/dev/null || true)"
  [[ -n "$backups" ]] || return 0
  printf '%s\n' "$backups" | tail -n +"$((KEEP + 1))" | while read -r old; do
    [[ -n "$old" ]] || continue
    log "清理旧备份 $(basename "$old")"
    run rm -rf "$old"
  done
}

# ---------------------------------------------------------------- 4. 同步
# 关键点：必须“原地覆盖”目标目录内容，不能删掉目录再新建。
# bind mount 绑定的是目录本身，换掉目录（inode 变了）后运行中的容器仍然看到旧内容，
# 直到容器重启才会重新挂载。原地覆盖则不存在这个歧义。
sync_one() {
  local name="$1"
  local src="$SRC_ROOT/$name"
  local dest="$INSTALL_DIR/$name"

  if [[ ! -d "$src" ]]; then
    warn "来源缺少 $name/，跳过"
    return 0
  fi

  log "同步 $name/ ($(find "$src" -type f | wc -l) 个文件)"
  backup_path "$dest"
  run mkdir -p "$dest"

  if command -v rsync >/dev/null 2>&1; then
    # 用 --checksum 按内容比对，而不是默认的“大小 + 秒级 mtime”。
    # 默认快速判定会在“文件大小相同且 mtime 落在同一秒”时直接跳过，导致代码看起来
    # 同步过了、实际仍是旧内容——部署脚本不能接受这种静默漏更。src 只有 MB 级，
    # 全量校验的代价可以忽略。
    run rsync -a -c --delete \
      --exclude '__pycache__/' --exclude '*.pyc' \
      "$src"/ "$dest"/
  else
    warn "未安装 rsync，回退为 tar 覆盖（不会删除目标目录里已废弃的文件）"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf '[dry-run] tar 覆盖 %s -> %s\n' "$src" "$dest"
    else
      (cd "$src" && tar -cf - --exclude='__pycache__' --exclude='*.pyc' .) |
        (cd "$dest" && tar -xf - --overwrite)
    fi
  fi
  prune_backups "$dest"
}

# ---------------------------------------------------------------- 5. 重启
resolve_services() {
  if [[ "$SERVICES" == "all" ]]; then
    $COMPOSE "${COMPOSE_ARGS[@]}" config --services 2>/dev/null |
      grep -vxE 'postgres|migrate' | tr '\n' ' '
  else
    printf '%s' "$SERVICES"
  fi
}

restart_services() {
  local svc_list
  svc_list="$(resolve_services)"
  [[ -n "${svc_list// /}" ]] || fail "没有解析到要重启的服务"

  # shellcheck disable=SC2206
  local -a svc=($svc_list)
  log "待处理服务: ${svc[*]}"

  if [[ "$RECREATE" -eq 1 ]]; then
    # 改了 .env.docker 必须重建容器，restart 不会重新读取 env_file
    log "重建容器（会重新读取 .env.docker）"
    run $COMPOSE "${COMPOSE_ARGS[@]}" up -d --force-recreate "${svc[@]}"
    return 0
  fi

  # 只重启已存在的容器。没有容器的服务（例如未启用的 llm-worker）跳过去，不能在这一步
  # 被顺手创建出来——那会意外开启一个运维方有意没启的进程。
  local -a present=() missing=()
  local s
  for s in "${svc[@]}"; do
    if [[ -n "$($COMPOSE "${COMPOSE_ARGS[@]}" ps -q "$s" 2>/dev/null)" ]]; then
      present+=("$s")
    else
      missing+=("$s")
    fi
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    warn "以下服务当前没有容器，跳过（需要时自行 up -d）：${missing[*]}"
  fi
  [[ "${#present[@]}" -gt 0 ]] || {
    warn "没有可重启的容器，已跳过重启"
    return 0
  }

  log "重启容器（进程重生，加载新代码）"
  run $COMPOSE "${COMPOSE_ARGS[@]}" restart "${present[@]}"
}

# ---------------------------------------------------------------- 6. 迁移
run_migrations() {
  log "执行数据库迁移"
  run $COMPOSE "${COMPOSE_ARGS[@]}" --profile tools run --rm migrate
}

# ---------------------------------------------------------------- 主流程
prepare_source

# shellcheck disable=SC2206
PATHS_ARR=($PATHS)
log "安装目录: $INSTALL_DIR"
log "同步范围: ${PATHS_ARR[*]}"

for path_name in "${PATHS_ARR[@]}"; do
  sync_one "$path_name"
done

if [[ "$RUN_MIGRATE" -eq 1 ]]; then
  run_migrations
fi

if [[ "$DO_RESTART" -eq 1 ]]; then
  restart_services
else
  warn "已跳过重启。注意：不重启的话进程仍运行旧代码（Python 不会热加载）"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  echo
  log "当前状态:"
  $COMPOSE "${COMPOSE_ARGS[@]}" ps || true
  echo
  cat <<EOF
[update] 完成。建议验证：

  # 1) 看容器是否都起来了
  cd $INSTALL_DIR && $COMPOSE ${COMPOSE_ARGS[*]} ps

  # 2) 看容器有没有报错
  $COMPOSE ${COMPOSE_ARGS[*]} logs --tail=50 api worker realtime-worker

  # 3) 打一个已完成案件，确认返回里出现新字段（本次为 IfCase / CaseReason）
  curl -s -H "X-API-Key: <你的key>" http://127.0.0.1:8000/api/v1/postcall/jobs/<jobId>

回滚：同样要原地覆盖，不能删掉 src 再拷回来（换目录会让运行中的容器继续读旧内容）。
  ls -d $INSTALL_DIR/src.bak.*
  rsync -a -c --delete $INSTALL_DIR/src.bak.<时间戳>/ $INSTALL_DIR/src/
  $COMPOSE ${COMPOSE_ARGS[*]} restart api worker realtime-worker console
EOF
fi
