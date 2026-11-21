#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
usage() {
  cat <<'HELP'
用法
  bash upgrade.sh 项目安装目录 [--check-only]

示例
  bash upgrade.sh /opt/echox-call --check-only
  bash upgrade.sh /opt/echox-call

执行完整性校验和兼容性预检，通过后备份 src、同步新代码并重启已有业务容器。
--check-only 仅执行校验和预检。
HELP
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != "--check-only" ) ]]; then
  usage >&2
  exit 2
fi
if [[ ! -d "$1" ]]; then
  echo "项目安装目录不存在 $1" >&2
  exit 1
fi
INSTALL_DIR="$(cd "$1" && pwd)"
# 解压后的包应放在安装目录 src 以外，防止同步时删除来源。
case "$BUNDLE_DIR/" in
  "$INSTALL_DIR/src/"*)
    echo '请将升级包解压到项目 src 目录以外后再运行。' >&2
    exit 1
    ;;
esac
cd "$BUNDLE_DIR"
sha256sum --check SHA256SUMS
bash "$BUNDLE_DIR/check_update.sh" --source "$BUNDLE_DIR/project" --install-dir "$INSTALL_DIR"
if [[ "${2:-}" == "--check-only" ]]; then
  exit 0
fi
bash "$BUNDLE_DIR/update.sh" --source "$BUNDLE_DIR/project" --install-dir "$INSTALL_DIR" --paths src
