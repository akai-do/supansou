#!/usr/bin/env bash
# 一键同步前端布局到线上。
#
# 用法（Git Bash）:
#   ./deploy-frontend.sh
#   DEPLOY_HOST=1.2.3.4 DEPLOY_USER=root DEPLOY_PORT=22 ./deploy-frontend.sh
#
# Windows 双击: deploy-frontend.bat
#
# 这个脚本只做三件事:
#   1. 本地 npm run build
#   2. 把 frontend/dist 上传到服务器并覆盖
#   3. 验证公网 index.html 已经引用新的 JS
# 不会重启容器，也不会碰 backend/data、docker-compose.yml。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-8.217.147.228}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN:-xiaowusu.com}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/dupansou}"
WEB_USER="${WEB_USER:-www}"
REMOTE_TMP="/tmp/dupansou-frontend-$$.tar.gz"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)

# 优先把带 node.exe 的目录放到 PATH 最前面；否则 npm 在 Windows 上会找不到 node
for p in "/c/Users/LX/AppData/Local/Programs/nodejs" "/c/Users/LX/AppData/Local/nodejs22"; do
  if [ -x "$p/node.exe" ]; then
    export PATH="$p:$PATH"
    break
  fi
done

for cmd in node npm ssh scp tar; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "错误: 找不到 $cmd"; exit 1; }
done

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
  ssh "${SSH_OPTS[@]}" -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" "rm -f '$REMOTE_TMP'" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> 1/4 构建前端"
cd "$ROOT_DIR/frontend"
if [ ! -d node_modules ]; then
  echo "    node_modules 不存在，执行 npm install..."
  npm install
fi
npm run build

[ -f dist/index.html ] || { echo "错误: dist/index.html 不存在，构建失败"; exit 1; }
LOCAL_JS="$(grep -o 'assets/index-[^"]*\.js' dist/index.html | head -1 || true)"
[ -n "$LOCAL_JS" ] || { echo "错误: dist/index.html 中没有 assets/index-*.js"; exit 1; }
echo "    本地 JS: $LOCAL_JS"

echo "==> 2/4 打包并上传"
tar -czf "$TMP_DIR/frontend-dist.tar.gz" -C dist .
scp -P "$DEPLOY_PORT" "${SSH_OPTS[@]}" \
  "$TMP_DIR/frontend-dist.tar.gz" "$DEPLOY_USER@$DEPLOY_HOST:$REMOTE_TMP"

echo "==> 3/4 线上替换（不重启容器）"
ssh "${SSH_OPTS[@]}" -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" \
  "DEPLOY_DIR='$DEPLOY_DIR' DEPLOY_DOMAIN='$DEPLOY_DOMAIN' WEB_USER='$WEB_USER' REMOTE_TMP='$REMOTE_TMP' bash -s" <<'REMOTE'
set -euo pipefail
cd "$DEPLOY_DIR"
mkdir -p /www/backup
cp -a frontend/dist "/www/backup/frontend-dist-$(date +%F-%H%M%S)"

rm -rf frontend/dist.new
mkdir -p frontend/dist.new
tar -xzf "$REMOTE_TMP" -C frontend/dist.new
# 覆盖 index.html 和新增资源；旧的 hash 文件保留，老页面不会 404
cp -a frontend/dist.new/. frontend/dist/
rm -rf frontend/dist.new
chown -R "$WEB_USER:$WEB_USER" frontend/dist
chmod -R 755 frontend/dist
rm -f "$REMOTE_TMP"

echo "    线上资源:"
curl -sk -H "Host: $DEPLOY_DOMAIN" https://127.0.0.1/ | grep -o 'assets/index-[^"]*' | head -5
REMOTE

echo "==> 4/4 公网验证"
sleep 1
REMOTE_HTML="$(curl -fsS -m 20 "https://$DEPLOY_DOMAIN/?v=$(date +%s)")"
if ! printf '%s' "$REMOTE_HTML" | grep -q "$LOCAL_JS"; then
  echo "错误: 公网 index.html 中没有 $LOCAL_JS"
  echo "      可能是 CDN/浏览器缓存，或上传未生效。"
  exit 1
fi
echo "完成: https://$DEPLOY_DOMAIN"
echo "如果浏览器仍显示旧页面，请按 Ctrl+F5 强制刷新。"
