#!/usr/bin/env bash
#
# 离线增量更新前的预检脚本（代码挂载式部署专用，与 update.sh 配套）。
#
# 只读：不修改安装目录，不重启、不创建任何长期容器。容器内检查使用
# `docker run --rm --network none` 的一次性容器，新代码以只读方式挂载。
#
# 退出码：0 = 可以更新（可能带有需要先确认的警告）；1 = 存在阻断项，不能直接更新；
#         2 = 参数错误。
#
# 用法见 --help。
set -euo pipefail

INSTALL_DIR="/opt/echox-call"
SOURCE_DIR=""
ARCHIVE=""
PATHS="src"
IMAGE=""
SKIP_CONTAINER=0
COMPOSE_FILE_REL="deploy/offline/docker-compose.production.yml"
# 备份和同步之外额外预留的磁盘空间（KB）。
DISK_MARGIN_KB=102400
# 每类差异最多列出的条目数。
DIFF_LIST_LIMIT=20

usage() {
  cat <<'EOF'
更新前预检：确认安装目录能否替换为给定的新版本，并给出对应的 update.sh 命令。

Usage:
  ./check_update.sh --source DIR [options]
  ./check_update.sh --archive FILE [options]

Source（二选一，必填，与 update.sh 相同）:
  --source DIR   新代码根目录
  --archive FILE 新代码归档（.tar.gz）。可含一层顶层目录，会自动探测

Options:
  --install-dir DIR  安装目录。默认 /opt/echox-call
  --paths "src ..."  计划同步的顶层目录，空格分隔。默认 "src"
  --image NAME       用于容器内检查的镜像。默认取 compose 文件中的 echox-call 镜像
  --skip-container   跳过容器内检查（语法、入口模块导入、依赖版本）
  -h, --help         显示本帮助

检查内容:
  1. 安装目录、compose 文件、.env.docker、docker / compose / rsync 是否就绪
  2. 新旧文件差异（新增 / 修改 / 将被删除）
  3. requirements.txt、migrations/、config/、compose 文件是否有变化
  4. 磁盘空间是否足够备份和同步
  5. 在现有镜像里检查新代码：语法、各服务入口模块能否导入、依赖版本是否满足

为了让第 3、5 项完整，归档里除 src 外建议一并带上 requirements.txt、migrations、
config 和 compose 文件（update.sh 只会同步 --paths 指定的目录，多带不影响更新）:

  tar --exclude='__pycache__' --exclude='*.pyc' -czf echox-update.tar.gz \
    src migrations config requirements.txt deploy/offline/docker-compose.production.yml
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
    --image)
      IMAGE="${2:?missing value for --image}"
      shift
      ;;
    --skip-container)
      SKIP_CONTAINER=1
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

if [[ -z "$SOURCE_DIR" && -z "$ARCHIVE" ]]; then
  usage >&2
  echo "需要 --source 或 --archive" >&2
  exit 2
fi
if [[ -n "$SOURCE_DIR" && -n "$ARCHIVE" ]]; then
  echo "--source 与 --archive 只能给一个" >&2
  exit 2
fi

# ---------------------------------------------------------------- 结果记录
FAILS=()
WARNS=()
SUGGESTED_PATHS="$PATHS"
NEED_MIGRATE=0

section() { printf '\n== %s\n' "$*"; }
ok() { printf '[ OK ] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() {
  printf '[WARN] %s\n' "$*"
  WARNS+=("$*")
}
fail() {
  printf '[FAIL] %s\n' "$*"
  FAILS+=("$*")
}

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
  return 0
}
trap cleanup EXIT

finish() {
  section "结论"
  if [[ "${#FAILS[@]}" -gt 0 ]]; then
    echo "不能直接更新，存在 ${#FAILS[@]} 个阻断项："
    printf '  - %s\n' "${FAILS[@]}"
    if [[ "${#WARNS[@]}" -gt 0 ]]; then
      echo "另有 ${#WARNS[@]} 个警告："
      printf '  - %s\n' "${WARNS[@]}"
    fi
    exit 1
  fi

  if [[ "${#WARNS[@]}" -gt 0 ]]; then
    echo "可以更新，但有 ${#WARNS[@]} 个警告需要先确认："
    printf '  - %s\n' "${WARNS[@]}"
  else
    echo "可以更新，未发现问题。"
  fi

  local source_arg update_cmd
  if [[ -n "$ARCHIVE" ]]; then
    source_arg="--archive $ARCHIVE"
  else
    source_arg="--source $SOURCE_DIR"
  fi
  update_cmd="./deploy/offline/update.sh $source_arg"
  [[ "$INSTALL_DIR" == "/opt/echox-call" ]] || update_cmd+=" --install-dir $INSTALL_DIR"
  [[ "$SUGGESTED_PATHS" == "src" ]] || update_cmd+=" --paths \"$SUGGESTED_PATHS\""
  [[ "$NEED_MIGRATE" -eq 0 ]] || update_cmd+=" --migrate"
  echo
  echo "对应的更新命令："
  echo "  cd $INSTALL_DIR"
  echo "  $update_cmd --dry-run"
  echo "  $update_cmd"
  exit 0
}

# ---------------------------------------------------------------- 1. 环境
section "环境"

if [[ ! -d "$INSTALL_DIR" ]]; then
  fail "安装目录不存在: $INSTALL_DIR"
  finish
fi
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd)"
ok "安装目录: $INSTALL_DIR"

COMPOSE_FILE="$INSTALL_DIR/$COMPOSE_FILE_REL"
if [[ -f "$COMPOSE_FILE" ]]; then
  ok "compose 文件存在"
else
  fail "找不到 $COMPOSE_FILE（本脚本只适用于代码挂载式部署）"
fi

if [[ -f "$INSTALL_DIR/.env.docker" ]]; then
  ok ".env.docker 存在"
else
  warn ".env.docker 不存在，docker compose 可能无法解析变量"
fi

if [[ -w "$INSTALL_DIR" ]]; then
  ok "当前用户对安装目录有写权限"
else
  fail "当前用户对 $INSTALL_DIR 没有写权限，update.sh 无法备份和同步"
fi

if command -v rsync >/dev/null 2>&1; then
  ok "rsync 可用"
else
  warn "未安装 rsync，update.sh 会回退为 tar 覆盖，新版本已删除的文件会残留在安装目录"
fi

DOCKER_READY=0
COMPOSE=""
if ! command -v docker >/dev/null 2>&1; then
  fail "未安装 docker"
elif ! docker info >/dev/null 2>&1; then
  fail "docker 不可用（守护进程未运行，或当前用户无权限访问）"
else
  DOCKER_READY=1
  ok "docker 可用"
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
  else
    fail "docker compose plugin 或 docker-compose 未安装"
  fi
fi

# ---------------------------------------------------------------- 2. 来源
section "更新来源"

SRC_ROOT=""
if [[ -n "$ARCHIVE" ]]; then
  if [[ ! -f "$ARCHIVE" ]]; then
    fail "归档不存在: $ARCHIVE"
    finish
  fi
  ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
  EXTRACT_DIR="$TMP_DIR/extract"
  mkdir -p "$EXTRACT_DIR"
  if ! tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR" 2>"$TMP_DIR/tar.err"; then
    fail "归档无法解包（文件损坏或不是 .tar.gz）: $(head -n 1 "$TMP_DIR/tar.err")"
    finish
  fi
  ok "归档可以解包: $ARCHIVE"
  info "sha256: $(sha256sum "$ARCHIVE" | cut -d' ' -f1)（与外网打包时的值核对）"
  # 与 update.sh 相同的探测规则：顶层只有一个包装目录时下钻一层，
  # 但该目录本身就是要同步的路径时不下钻。
  entry_count="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 | wc -l)"
  if [[ "$entry_count" -eq 1 ]]; then
    only_entry="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1)"
    if [[ -d "$only_entry" ]]; then
      case " $PATHS " in
        *" $(basename "$only_entry") "*) : ;;
        *) SRC_ROOT="$only_entry" ;;
      esac
    fi
  fi
  [[ -n "$SRC_ROOT" ]] || SRC_ROOT="$EXTRACT_DIR"
else
  if [[ ! -d "$SOURCE_DIR" ]]; then
    fail "目录不存在: $SOURCE_DIR"
    finish
  fi
  SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
  SRC_ROOT="$SOURCE_DIR"
  ok "来源目录: $SRC_ROOT"
fi

# shellcheck disable=SC2206
PATHS_ARR=($PATHS)
path_is_planned() {
  case " $PATHS " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

SOURCE_READY=1
for path_name in "${PATHS_ARR[@]}"; do
  if [[ -d "$SRC_ROOT/$path_name" ]]; then
    ok "来源包含 $path_name/（$(find "$SRC_ROOT/$path_name" -type f ! -name '*.pyc' | wc -l) 个文件）"
  else
    fail "来源缺少 $path_name/，update.sh 会跳过它，等于没有更新"
    SOURCE_READY=0
  fi
done
if path_is_planned src && [[ -d "$SRC_ROOT/src" && ! -f "$SRC_ROOT/src/echox_call/__init__.py" ]]; then
  fail "来源的 src/ 下没有 echox_call 包，目录层级不对"
  SOURCE_READY=0
fi

# ---------------------------------------------------------------- 3. 文件差异
# 输出相对路径的差异清单：A 新增、M 修改、D 仅存在于安装目录。
list_differences() {
  local installed="$1" incoming="$2"
  LC_ALL=C diff -rq -x '__pycache__' -x '*.pyc' "$installed" "$incoming" 2>/dev/null |
    awk -v installed="$installed" -v incoming="$incoming" '
      /^Files / {
        print "M " substr($2, length(installed) + 2)
        next
      }
      /^Only in / {
        dir = $3
        sub(/:$/, "", dir)
        name = $0
        sub(/^Only in [^:]*: /, "", name)
        status = "D"
        base = installed
        if (index(dir, incoming) == 1) {
          status = "A"
          base = incoming
        }
        rel = substr(dir, length(base) + 2)
        print status " " (rel == "" ? name : rel "/" name)
      }
    ' || true
}

count_differences() {
  grep -c "^$1 " "$2" || true
}

print_differences() {
  local label="$1" status="$2" diff_file="$3" count
  count="$(count_differences "$status" "$diff_file")"
  [[ "$count" -gt 0 ]] || return 0
  info "$label $count 项："
  grep "^$status " "$diff_file" | head -n "$DIFF_LIST_LIMIT" | sed "s/^$status /         /"
  if [[ "$count" -gt "$DIFF_LIST_LIMIT" ]]; then
    echo "         … 其余 $((count - DIFF_LIST_LIMIT)) 项省略"
  fi
}

section "文件差异"
TOTAL_DIFFERENCES=0
for path_name in "${PATHS_ARR[@]}"; do
  [[ -d "$SRC_ROOT/$path_name" ]] || continue
  if [[ ! -d "$INSTALL_DIR/$path_name" ]]; then
    info "$path_name/ 在安装目录中不存在，将整体新建"
    TOTAL_DIFFERENCES=$((TOTAL_DIFFERENCES + 1))
    continue
  fi
  diff_file="$TMP_DIR/diff.planned"
  list_differences "$INSTALL_DIR/$path_name" "$SRC_ROOT/$path_name" >"$diff_file"
  path_differences="$(wc -l <"$diff_file")"
  TOTAL_DIFFERENCES=$((TOTAL_DIFFERENCES + path_differences))
  if [[ "$path_differences" -eq 0 ]]; then
    ok "$path_name/ 与安装目录完全一致"
    continue
  fi
  print_differences "$path_name/ 修改" M "$diff_file"
  print_differences "$path_name/ 新增" A "$diff_file"
  print_differences "$path_name/ 将被删除（新版本中已不存在）" D "$diff_file"
done
if [[ "$SOURCE_READY" -eq 1 && "$TOTAL_DIFFERENCES" -eq 0 ]]; then
  warn "计划同步的目录与安装目录完全一致，更新不会带来任何变化（确认归档是否为新版本）"
fi

# ---------------------------------------------------------------- 4. 代码以外的变化
section "依赖、迁移、配置"

# requirements.txt：是否需要新镜像，以第 6 步在镜像内的验证结果为准。
REQUIREMENTS_FILE=""
if [[ -f "$SRC_ROOT/requirements.txt" ]]; then
  REQUIREMENTS_FILE="$SRC_ROOT/requirements.txt"
  if [[ ! -f "$INSTALL_DIR/requirements.txt" ]]; then
    info "安装目录没有 requirements.txt，无法对比，改为直接在镜像内验证"
  elif cmp -s "$SRC_ROOT/requirements.txt" "$INSTALL_DIR/requirements.txt"; then
    ok "requirements.txt 没有变化"
  else
    info "requirements.txt 有变化，是否需要新镜像以镜像内验证结果为准："
    LC_ALL=C diff \
      <(grep -vE '^[[:space:]]*(#|$)' "$INSTALL_DIR/requirements.txt") \
      <(grep -vE '^[[:space:]]*(#|$)' "$SRC_ROOT/requirements.txt") |
      grep '^[<>]' | sed 's/^</         旧:/; s/^>/         新:/' || true
  fi
else
  warn "来源未包含 requirements.txt，无法确认新版本的依赖是否变化"
  if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
    REQUIREMENTS_FILE="$INSTALL_DIR/requirements.txt"
  fi
fi

# migrations/
if [[ ! -d "$SRC_ROOT/migrations" ]]; then
  warn "来源未包含 migrations/，无法确认新版本是否需要数据库迁移"
elif [[ ! -d "$INSTALL_DIR/migrations" ]]; then
  warn "安装目录没有 migrations/，无法对比迁移文件"
else
  diff_file="$TMP_DIR/diff.migrations"
  list_differences "$INSTALL_DIR/migrations" "$SRC_ROOT/migrations" >"$diff_file"
  added_migrations="$(count_differences A "$diff_file")"
  if [[ "$added_migrations" -gt 0 ]]; then
    NEED_MIGRATE=1
    path_is_planned migrations || SUGGESTED_PATHS+=" migrations"
    warn "新版本新增 $added_migrations 个迁移文件，更新时必须同步 migrations 并加 --migrate"
    print_differences "新增迁移" A "$diff_file"
  fi
  if [[ "$(count_differences M "$diff_file")" -gt 0 ]]; then
    warn "已有迁移文件的内容被修改；已执行过的迁移不会重跑，需人工确认数据库结构"
    print_differences "被修改的迁移" M "$diff_file"
  fi
  if [[ "$(count_differences D "$diff_file")" -gt 0 ]]; then
    warn "安装目录中有新版本不存在的迁移文件，现场数据库可能比新代码更新，确认不是在回退版本"
    print_differences "仅存在于安装目录的迁移" D "$diff_file"
  fi
  if [[ ! -s "$diff_file" ]]; then
    ok "migrations/ 没有变化，不需要数据库迁移"
  fi
fi

# config/：现场配置通常被改过，这里只提示，不建议自动覆盖。
if [[ ! -d "$SRC_ROOT/config" ]]; then
  warn "来源未包含 config/，无法确认新版本是否需要新的配置文件"
elif [[ -d "$INSTALL_DIR/config" ]] && ! path_is_planned config; then
  diff_file="$TMP_DIR/diff.config"
  list_differences "$INSTALL_DIR/config" "$SRC_ROOT/config" >"$diff_file"
  if [[ "$(count_differences A "$diff_file")" -gt 0 ]]; then
    warn "新版本新增了配置文件，本次更新不会同步 config/，需人工确认是否要拷贝"
    print_differences "新增配置" A "$diff_file"
  fi
  print_differences "与新版本内容不同的配置（可能是现场修改，仅供参考）" M "$diff_file"
  if [[ ! -s "$diff_file" ]]; then
    ok "config/ 没有变化"
  fi
fi
if path_is_planned config; then
  warn "--paths 包含 config：update.sh 会用新版本覆盖现场配置（clients.yaml、console_users.yaml 等），并删除新版本中没有的文件"
fi

# compose 文件：update.sh 不会同步 deploy/。
if [[ ! -f "$SRC_ROOT/$COMPOSE_FILE_REL" ]]; then
  warn "来源未包含 $COMPOSE_FILE_REL，无法确认服务定义是否变化"
elif [[ -f "$COMPOSE_FILE" ]]; then
  if cmp -s "$SRC_ROOT/$COMPOSE_FILE_REL" "$COMPOSE_FILE"; then
    ok "compose 文件没有变化"
  else
    warn "compose 文件有变化，update.sh 不会同步它；需人工替换后用 --recreate 重建容器"
  fi
fi

# ---------------------------------------------------------------- 5. 磁盘空间与服务状态
section "磁盘空间与服务状态"

size_kb() {
  du -sk "$1" 2>/dev/null | cut -f1
}

# update.sh 会先把每个目标目录完整备份一份，再写入新内容。
required_kb="$DISK_MARGIN_KB"
for path_name in "${PATHS_ARR[@]}"; do
  if [[ -d "$INSTALL_DIR/$path_name" ]]; then
    required_kb=$((required_kb + $(size_kb "$INSTALL_DIR/$path_name")))
  fi
  if [[ -d "$SRC_ROOT/$path_name" ]]; then
    required_kb=$((required_kb + $(size_kb "$SRC_ROOT/$path_name")))
  fi
done
available_kb="$(df -Pk "$INSTALL_DIR" | awk 'NR == 2 { print $4 }')"
if [[ "$available_kb" -ge "$required_kb" ]]; then
  ok "磁盘空间足够（需要约 $((required_kb / 1024)) MB，可用 $((available_kb / 1024)) MB）"
else
  fail "磁盘空间不足（需要约 $((required_kb / 1024)) MB，可用 $((available_kb / 1024)) MB）"
fi

if [[ -n "$COMPOSE" && -f "$COMPOSE_FILE" ]]; then
  COMPOSE_ARGS=(-f "$COMPOSE_FILE")
  if [[ -f "$INSTALL_DIR/.env.docker" ]]; then
    COMPOSE_ARGS=(--env-file "$INSTALL_DIR/.env.docker" "${COMPOSE_ARGS[@]}")
  fi
  if service_list="$($COMPOSE "${COMPOSE_ARGS[@]}" config --services 2>"$TMP_DIR/compose.err")"; then
    running_services=()
    absent_services=()
    for service in $(grep -vxE 'postgres|migrate' <<<"$service_list"); do
      if [[ -n "$($COMPOSE "${COMPOSE_ARGS[@]}" ps -q "$service" 2>/dev/null)" ]]; then
        running_services+=("$service")
      else
        absent_services+=("$service")
      fi
    done
    if [[ "${#running_services[@]}" -gt 0 ]]; then
      ok "更新后会被重启的服务: ${running_services[*]}"
    else
      warn "当前没有任何业务容器，update.sh 不会启动服务，更新后需自行 up -d"
    fi
    if [[ "${#absent_services[@]}" -gt 0 ]]; then
      info "没有容器、不会被重启的服务: ${absent_services[*]}"
    fi
  else
    fail "docker compose 无法解析现有配置: $(head -n 1 "$TMP_DIR/compose.err")"
  fi
fi

# ---------------------------------------------------------------- 6. 镜像内检查
section "镜像内检查"

if [[ -z "$IMAGE" && -f "$COMPOSE_FILE" ]]; then
  IMAGE="$(grep -oE 'image:[[:space:]]*echox-call:[^[:space:]"]+' "$COMPOSE_FILE" |
    head -n 1 | sed -E 's/^image:[[:space:]]*//' || true)"
fi

CONTAINER_READY=0
if [[ "$SKIP_CONTAINER" -eq 1 ]]; then
  warn "已按 --skip-container 跳过镜像内检查，新代码的语法、导入和依赖未验证"
elif [[ "$DOCKER_READY" -eq 0 ]]; then
  info "docker 不可用，无法做镜像内检查"
elif [[ "$SOURCE_READY" -eq 0 ]]; then
  info "来源不完整，跳过镜像内检查"
elif [[ -z "$IMAGE" ]]; then
  fail "无法从 compose 文件确定应用镜像，请用 --image 指定"
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  fail "本机没有镜像 $IMAGE"
else
  CONTAINER_READY=1
  ok "镜像存在: $IMAGE"
fi

# 与 compose 的 x-app-volumes 保持一致：计划同步的目录用新版本，其余用安装目录现有内容。
# data/ 不挂载，检查过程不接触现场数据。
build_container_args() {
  CONTAINER_ARGS=(--rm --network none)
  if [[ -f "$INSTALL_DIR/.env.docker" ]]; then
    CONTAINER_ARGS+=(--env-file "$INSTALL_DIR/.env.docker")
  fi
  local name host_dir
  for name in src config models migrations docs third_party; do
    host_dir="$INSTALL_DIR/$name"
    if path_is_planned "$name" && [[ -d "$SRC_ROOT/$name" ]]; then
      host_dir="$SRC_ROOT/$name"
    fi
    if [[ -d "$host_dir" ]]; then
      CONTAINER_ARGS+=(-v "$host_dir:/app/$name:ro")
    fi
  done
}

print_indented() {
  sed 's/^/         /' "$1"
}

if [[ "$CONTAINER_READY" -eq 1 ]]; then
  build_container_args

  # 6.1 语法 + 入口模块导入。入口模块取自新旧 compose 文件中各服务的启动命令。
  # 已退役服务的入口（如旧版 realtime-worker）被定向排除——它们在新版中已不存在，
  # 属于本次升级刻意移除的能力，不算回归。其他现场定制入口照常校验。
  retired_entry_modules=(echox_call.cli.realtime_worker)
  entry_modules="$(cat "$COMPOSE_FILE" "$SRC_ROOT/$COMPOSE_FILE_REL" 2>/dev/null |
    grep -oE 'echox_call\.cli\.[A-Za-z0-9_]+' | sort -u | tr '\n' ' ' || true)"
  for retired in "${retired_entry_modules[@]}"; do
    entry_modules="$(tr ' ' '\n' <<<"$entry_modules" | grep -vxF "$retired" | tr '\n' ' ')"
  done
  cat >"$TMP_DIR/check_code.py" <<'PYTHON_EOF'
import importlib
import pathlib
import sys
import traceback

source_root = pathlib.Path(sys.argv[1])
entry_modules = sys.argv[2:]
problems = 0

source_files = sorted(source_root.rglob("*.py"))
for path in source_files:
    try:
        compile(path.read_bytes(), str(path), "exec")
    except SyntaxError as exc:
        problems += 1
        print(f"语法错误 {path}:{exc.lineno}: {exc.msg}")
print(f"语法检查: {len(source_files)} 个文件")

for module_name in entry_modules:
    try:
        importlib.import_module(module_name)
    except BaseException:
        problems += 1
        print(f"导入失败 {module_name}")
        traceback.print_exc(limit=-3, file=sys.stdout)
    else:
        print(f"导入成功 {module_name}")

sys.exit(1 if problems else 0)
PYTHON_EOF

  # shellcheck disable=SC2086
  if docker run -i "${CONTAINER_ARGS[@]}" "$IMAGE" python - /app/src $entry_modules \
    <"$TMP_DIR/check_code.py" >"$TMP_DIR/check_code.out" 2>&1; then
    ok "新代码语法正确，各服务入口模块均可在现有镜像中导入"
    print_indented "$TMP_DIR/check_code.out"
  else
    fail "新代码在现有镜像中存在语法错误或无法导入（详见下方输出）"
    print_indented "$TMP_DIR/check_code.out"
  fi

  # 6.2 依赖版本。--no-index 下 pip 只看镜像里已安装的包：
  # 全部满足则成功，缺包或版本不符则报错。--dry-run 保证不安装任何东西。
  if [[ -n "$REQUIREMENTS_FILE" ]]; then
    if docker run "${CONTAINER_ARGS[@]}" -v "$REQUIREMENTS_FILE:/tmp/requirements-check.txt:ro" \
      "$IMAGE" python -m pip install --dry-run --no-index --no-deps \
      --disable-pip-version-check --quiet -r /tmp/requirements-check.txt \
      >"$TMP_DIR/check_deps.out" 2>&1; then
      ok "现有镜像满足 requirements.txt 的全部依赖，不需要新镜像"
    else
      fail "现有镜像不满足新版本的依赖，需要重建镜像并重新传输镜像包"
      grep -E 'ERROR' "$TMP_DIR/check_deps.out" | sed 's/^/         /' || print_indented "$TMP_DIR/check_deps.out"
    fi
  fi
fi

finish
