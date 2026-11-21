#!/usr/bin/env bash
# 从工作区打包，包含尚未提交的当前代码。
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT_DIR/dist}"
if [[ $# -gt 1 ]]; then
  echo '用法 bash scripts/package_code_update.sh [输出目录]' >&2
  exit 2
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
RELEASE_NAME="echox-call-upgrade-$(date +%Y%m%d-%H%M%S)"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT
BUNDLE_DIR="$STAGING_DIR/$RELEASE_NAME"
mkdir -p "$BUNDLE_DIR/project"
cd "$ROOT_DIR"
tar --exclude='__pycache__' --exclude='*.pyc' -cf - \
  src requirements.txt migrations config deploy/offline/docker-compose.production.yml |
  tar -xf - -C "$BUNDLE_DIR/project"
for script in upgrade.sh check_update.sh update.sh; do
  install -m 755 "deploy/offline/$script" "$BUNDLE_DIR/$script"
done
{
  printf 'GitCommit %s\n' "$(git rev-parse HEAD)"
  printf 'CreatedAt %s\n' "$(date -Is)"
  echo 'Source working-tree'
  echo 'UpdatePaths src'
} > "$BUNDLE_DIR/VERSION.txt"
cat > "$BUNDLE_DIR/README.txt" <<'HELP'
适用于 deploy/offline/docker-compose.production.yml 的代码挂载部署。
解压后进入本目录，将服务器项目安装目录作为参数。

仅检查
  bash upgrade.sh /opt/echox-call --check-only

检查并升级
  bash upgrade.sh /opt/echox-call

升级前检查包内校验和、运行环境、依赖、代码导入和磁盘空间。
检查通过后自动备份 src（默认保留三份），同步当前代码，重启已有业务容器。
config、migrations、requirements.txt、compose 文件用于版本对比。
如预检提示迁移或配置差异，请先核对对应提示再执行升级。
备份位于项目安装目录下的 src.bak.<时间戳>。
本包包含打包时工作区内的最新代码，包括未提交的提示词修改。
HELP
(
  cd "$BUNDLE_DIR"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
ARCHIVE="$OUTPUT_DIR/$RELEASE_NAME.tar.gz"
tar -czf "$ARCHIVE.partial" -C "$STAGING_DIR" "$RELEASE_NAME"
mv "$ARCHIVE.partial" "$ARCHIVE"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
printf '已生成 %s\n' "$ARCHIVE"
