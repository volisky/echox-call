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
    warn "未安装 rsync，回退为 tar 覆盖（不会自动删除目标目录里已废弃的文件）"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf '[dry-run] tar 覆盖 %s -> %s\n' "$src" "$dest"
    else
      (cd "$src" && tar -cf - --exclude='__pycache__' --exclude='*.pyc' .) |
        (cd "$dest" && tar -xf - --overwrite)
    fi
    # tar 覆盖不会删文件，但本版本明确退役的模块必须一并清掉，
    # 否则旧代码残留会被入口模块继续拉起。
    if [[ "$name" == "src" ]]; then
      local rel
      for rel in "${REMOVED_SRC_FILES[@]}"; do
        # 只在来源确实不含该文件时删除：旧代码回滚包仍带着这些文件，
        # tar 已经把它们重新复制回来，不能再删
        if [[ ! -f "$src/$rel" && -f "$dest/$rel" ]]; then
          log "清理已退役文件 $rel"
          run rm -f "$dest/$rel"
        fi
      done
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

# ---------------------------------------------------------------- 退役服务
# 老版本安装里 realtime-worker 仍在 compose 中定义并随 restart 存活。离线升级只同步
# src/，新代码已不含实时音频实现，继续运行只会 crash-loop。这里先做针对性退役：
# 1) 服务是否定义以 `compose config --services` 为准，不猜缩进和注释写法
# 2) 容器按 project + service 标签初筛，再按 config_files 逗号列表精确匹配本
#    安装 compose 文件后定向删除，不用 compose rm，绝不误删其他项目/安装
# 3) 从现场 compose 文件删掉该服务块后用候选文件验证，防止后续 up -d 把它复活
# 任何一步无法安全完成都会报错退出并保留备份，绝不静默成功。
RETIRE_BACKUP=""

REMOVED_IDS=""

# 生效的 compose 项目名，按可靠性逐层探测：
#   a) `config --format json` 顶层 name（compose v2）
#   b) 普通 `config` 输出的 name:（v1 没有该字段时为空）
#   c) COMPOSE_PROJECT_NAME 环境变量（v1 用户显式指定）
#   d) 现有业务容器 inspect 的 project 标签（v1 无 name 字段时的可靠回退；
#      compose ps 本身只列本项目容器，拿第一个的标签即生效项目名）
# 探测仅在有候选容器时才需要：没有候选就没有容器可删，不必求项目名。
# 有候选而所有途径都拿不到项目名时明确失败，否则可能误删同名文件不同
# -p 的另一套安装。
EFFECTIVE_PROJECT_NAME=""
effective_project_name() {
  if [[ -z "$EFFECTIVE_PROJECT_NAME" ]]; then
    local json cid
    json="$($COMPOSE "${COMPOSE_ARGS[@]}" config --format json 2>/dev/null || true)"
    if [[ -n "$json" ]]; then
      EFFECTIVE_PROJECT_NAME="$(printf '%s' "$json" |
        sed -n 's/^  "name": "\([^"]*\)".*/\1/p' | head -1)"
    fi
    if [[ -z "$EFFECTIVE_PROJECT_NAME" ]]; then
      EFFECTIVE_PROJECT_NAME="$($COMPOSE "${COMPOSE_ARGS[@]}" config 2>/dev/null |
        sed -n 's/^name: *//p' | head -1)"
    fi
    if [[ -z "$EFFECTIVE_PROJECT_NAME" && -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
      EFFECTIVE_PROJECT_NAME="$COMPOSE_PROJECT_NAME"
    fi
    if [[ -z "$EFFECTIVE_PROJECT_NAME" ]]; then
      cid="$($COMPOSE "${COMPOSE_ARGS[@]}" ps -aq 2>/dev/null | head -1 || true)"
      if [[ -n "$cid" ]]; then
        EFFECTIVE_PROJECT_NAME="$(docker inspect \
          --format '{{index .Config.Labels "com.docker.compose.project"}}' \
          "$cid" 2>/dev/null || true)"
      fi
    fi
    [[ -n "$EFFECTIVE_PROJECT_NAME" ]] || \
      fail "无法确定 compose 项目名称，退役中止以保证不误删其他安装"
  fi
  printf '%s' "$EFFECTIVE_PROJECT_NAME"
}

removed_service_containers() {
  REMOVED_IDS=""
  local candidates id cfg item proj
  candidates="$(docker ps -aq \
    --filter "label=com.docker.compose.service=$1" 2>/dev/null)" || \
    fail "docker 不可用，无法确认 ${1} 容器状态；请人工检查后重试"
  [[ -n "$candidates" ]] || return 0
  # 有候选才需要项目名消歧：同一份 compose 文件可被另一个 -p 项目复用
  proj="$(effective_project_name)"
  local keep=""
  for id in $candidates; do
    local cp
    cp="$(docker inspect \
      --format '{{index .Config.Labels "com.docker.compose.project"}}' \
      "$id" 2>/dev/null || true)"
    [[ "$cp" == "$proj" ]] && keep+="$id "
  done
  candidates="$keep"
  [[ -n "$candidates" ]] || return 0
  # project 名在不同安装目录可能相同，不能单独用来定位；config_files 标签是
  # 逗号分隔的绝对路径列表，逐项与本安装 compose 文件精确比对。
  for id in $candidates; do
    cfg="$(docker inspect \
      --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' \
      "$id" 2>/dev/null || true)"
    [[ -n "$cfg" ]] || continue
    local IFS=','
    for item in $cfg; do
      if [[ "$item" == "$INSTALL_DIR/$COMPOSE_FILE_REL" ]]; then
        REMOVED_IDS+="$id "
      fi
    done
    IFS=$' \t\n'
  done
}

force_remove_service_containers() {
  local svc="$1" ids
  removed_service_containers "$svc"
  ids="$REMOVED_IDS"
  [[ -n "$ids" ]] || return 0
  warn "退役服务 ${svc}: 按本安装目录标签定向删除容器"
  # shellcheck disable=SC2086
  run docker rm -f $ids || \
    fail "删除 ${svc} 容器失败（$ids），请人工删除后重试"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  removed_service_containers "$svc"
  [[ -z "$REMOVED_IDS" ]] || \
    fail "退役服务 ${svc}: 仍有容器残留（$REMOVED_IDS），请人工删除后重试"
}

restore_compose_backup() {
  [[ -n "$RETIRE_BACKUP" && -f "$RETIRE_BACKUP" ]] || return 0
  cp -a "$RETIRE_BACKUP" "$COMPOSE_FILE_REL"
  warn "已恢复 compose 备份: $RETIRE_BACKUP"
}

RETIRE_REMOVED_SERVICES=(realtime-worker)
# 本版本删除的源码文件（tar fallback 同步 src 后定向清理；rsync --delete 已覆盖）
REMOVED_SRC_FILES=(
  echox_call/cli/realtime_worker.py
  echox_call/api/gateway.py
  echox_call/features/audio_analysis/postcall/realtime.py
)

path_is_planned() {
  case " $PATHS " in
    *" $1 "*) return 0 ;;
  esac
  return 1
}

compose_service_defined() {
  $COMPOSE "${COMPOSE_ARGS[@]}" config --services 2>/dev/null | grep -qx "$1"
}

# 生成"删除指定服务后的候选 compose 文件"，不改动原文件。
write_strip_candidate() {
  local svc="$1" file="$2" tmp="$3"
  grep -qE "^  ${svc}:" "$file" || return 1
  awk -v svc="$svc" \
    'BEGIN { skipping = 0 }
     {
       if (skipping) {
         # 遇到下一个两空格缩进键（下一个服务）或非缩进顶层键就恢复输出
         if ($0 ~ /^  [^ ]/ || $0 ~ /^[^ #]/) skipping = 0
       }
       if (!skipping) {
         if ($0 ~ "^  " svc ":") { skipping = 1; next }
         print
       }
     }' "$file" > "$tmp"
  return 0
}

# 验证候选文件：单独解析（替换 -f 参数），必须可解析且不再包含该服务。
verify_strip_candidate() {
  local svc="$1" tmp="$2"
  local -a cand_args=()
  local i
  for ((i = 0; i < ${#COMPOSE_ARGS[@]}; i++)); do
    if [[ "${COMPOSE_ARGS[i]}" == "-f" ]]; then
      cand_args+=(-f "$tmp")
      i=$((i + 1))
    else
      cand_args+=("${COMPOSE_ARGS[i]}")
    fi
  done
  $COMPOSE "${cand_args[@]}" config --services >/dev/null 2>&1 || return 1
  ! $COMPOSE "${cand_args[@]}" config --services 2>/dev/null | grep -qx "$svc"
}

# 同步 src 前的预检：现场 compose 必须可解析，待退役服务的候选文件也必须
# 可解析且确实删掉该服务；任一失败在改动 src 前就退出，避免半升级。
preflight_compose() {
  $COMPOSE "${COMPOSE_ARGS[@]}" config --services >/dev/null 2>&1 || \
    fail "现有 compose 文件无法解析，未做任何改动；请先修复 $INSTALL_DIR/$COMPOSE_FILE_REL"
  local svc tmp marker
  for svc in "${RETIRE_REMOVED_SERVICES[@]}"; do
    case "$svc" in
      realtime-worker) marker="echox_call/cli/realtime_worker.py" ;;
      *) continue ;;
    esac
    # 与 retire_removed_services 同一条件：未同步 src 或来源仍含旧模块则无需预检
    path_is_planned src || continue
    [[ ! -f "$SRC_ROOT/src/$marker" ]] || continue
    [[ -d "$SRC_ROOT/src/echox_call" ]] || continue
    compose_service_defined "$svc" || continue
    tmp="$(mktemp "${COMPOSE_FILE_REL}.retire-precheck.XXXXXX")"
    if ! write_strip_candidate "$svc" "$COMPOSE_FILE_REL" "$tmp"; then
      rm -f "$tmp"
      fail "compose 定义了 ${svc}，但找不到其服务块（可能被注释或非标准写法），\
未做任何改动；请手工从 $COMPOSE_FILE_REL 删除后重试"
    fi
    if ! verify_strip_candidate "$svc" "$tmp"; then
      rm -f "$tmp"
      fail "删除 ${svc} 服务定义后的候选 compose 文件不可用，未做任何改动；请人工处理"
    fi
    rm -f "$tmp"
  done
  log "compose 预检通过（现有配置可解析）"
}

strip_compose_service() {
  local svc="$1" file="$2"
  RETIRE_BACKUP="${file}.bak.${QSTAMP}"
  run cp -a "$file" "$RETIRE_BACKUP"
  log "compose 文件备份: ${RETIRE_BACKUP}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] 生成并验证删除 %s 服务定义后的候选 compose 文件\n' "$svc"
    return 0
  fi
  local tmp
  tmp="$(mktemp "${file}.retire-candidate.XXXXXX")"
  if ! write_strip_candidate "$svc" "$file" "$tmp"; then
    rm -f "$tmp"
    restore_compose_backup
    fail "无法从 $file 定位 ${svc} 服务块，已恢复原文件，请人工处理"
  fi
  if ! verify_strip_candidate "$svc" "$tmp"; then
    rm -f "$tmp"
    restore_compose_backup
    fail "删除 ${svc} 服务定义后的候选 compose 文件不可用，已恢复原文件，请人工处理"
  fi
  # 沿用现场文件权限：mktemp 默认 600，用户可能自定义过 mode/owner
  chmod --reference="$file" "$tmp"
  mv "$tmp" "$file"
  log "已从 compose 文件删除 ${svc} 服务定义（已验证不再包含，up -d 不会复活）"
}

# 仅当本次确实同步 src/ 且新来源已删除对应模块时才退役：--paths 不含 src
# （纯 config/migrations 更新）或来源仍带旧模块（回滚旧包）都不动现场定义。
retire_removed_services() {
  local svc
  for svc in "${RETIRE_REMOVED_SERVICES[@]}"; do
    local marker=""
    case "$svc" in
      realtime-worker) marker="echox_call/cli/realtime_worker.py" ;;
      *) continue ;;
    esac
    if ! path_is_planned src; then
      log "退役服务 ${svc}: 本次未同步 src/，跳过（保持现场定义）"
      continue
    fi
    if [[ -f "$SRC_ROOT/src/$marker" ]]; then
      log "退役服务 ${svc}: 新来源仍含 $marker，跳过退役"
      continue
    fi
    if [[ ! -d "$SRC_ROOT/src/echox_call" ]]; then
      log "退役服务 ${svc}: 来源无有效 src/echox_call（与 sync_one 条件一致），跳过"
      continue
    fi

    if compose_service_defined "$svc"; then
      log "退役服务 ${svc}: 停止并移除其容器"
      # 不用 compose rm：project 名跨安装可能相同。容器一律按 service 标签 +
      # config_files 绝对路径精确匹配本安装，绝不误删其他项目。
      force_remove_service_containers "$svc"
      strip_compose_service "$svc" "$COMPOSE_FILE_REL"
    else
      log "退役服务 ${svc}: compose 已无定义，检查孤儿容器"
      force_remove_service_containers "$svc"
    fi
  done
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

# 先做 compose 预检与退役，再覆盖 src：任一步失败都停在一致的旧状态，
# 不会出现“代码已换新、compose 还指着已删除服务”的半升级现场。
preflight_compose
retire_removed_services

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
  $COMPOSE ${COMPOSE_ARGS[*]} logs --tail=50 api worker llm-worker

  # 3) 打一个已完成案件，确认返回里出现新字段（本次为 IfCase / CaseReason）
  curl -s -H "X-API-Key: <你的key>" http://127.0.0.1:8000/api/v1/postcall/jobs/<jobId>

回滚：同样要原地覆盖，不能删掉 src 再拷回来（换目录会让运行中的容器继续读旧内容）。
  ls -d $INSTALL_DIR/src.bak.*
  rsync -a -c --delete $INSTALL_DIR/src.bak.<时间戳>/ $INSTALL_DIR/src/

  若升级前 compose 定义了 realtime-worker（旧版本），可同步恢复
  deploy/offline/docker-compose.production.yml.bak.<时间戳> 备份以还原其定义；
  确认不再需要实时音频的话，compose 备份无需恢复。

  $COMPOSE ${COMPOSE_ARGS[*]} restart api worker console
EOF
fi
