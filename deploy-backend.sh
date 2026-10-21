#!/usr/bin/env bash
# 一键同步后端代码到线上。
#
# 用法（Git Bash）:
#   ./deploy-backend.sh
#   DEPLOY_HOST=1.2.3.4 DEPLOY_USER=root DEPLOY_PORT=22 ./deploy-backend.sh
#
# Windows 双击: deploy-backend.bat
#
# 流程:
#   1. 打包 backend/（排除 data/、缓存、日志）
#   2. 服务器上备份 index.db
#   3. 停 backend → 新代码放到 backend.new → 切换目录（旧目录保留）
#   4. docker compose build backend → up -d --no-build backend
#   5. 公网验证
# backend/data 不会被覆盖；构建/启动失败会自动回滚到旧目录。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-8.217.147.228}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN:-xiaowusu.com}"
DEPLOY_DIR="${DEPLOY_DIR:-/www/wwwroot/dupansou}"
REMOTE_TMP="/tmp/dupansou-backend-$$.tar.gz"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)

for cmd in ssh scp tar; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "错误: 找不到 $cmd"; exit 1; }
done

cd "$ROOT_DIR/backend"
[ -f app.py ] || { echo "错误: backend/app.py 不存在"; exit 1; }
[ -f Dockerfile ] || { echo "错误: backend/Dockerfile 不存在"; exit 1; }
[ -f .dockerignore ] || { echo "错误: backend/.dockerignore 不存在（用来排除 data/）"; exit 1; }

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
  ssh "${SSH_OPTS[@]}" -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" "rm -f '$REMOTE_TMP'" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> 1/5 打包后端（排除 data/、缓存、日志、tests）"
tar -czf "$TMP_DIR/backend-src.tar.gz" \
  --exclude='./data' --exclude='./data/*' \
  --exclude='./__pycache__' --exclude='*/__pycache__' --exclude='*/__pycache__/*' \
  --exclude='*.pyc' --exclude='*.log' \
  --exclude='./tests' --exclude='./tests/*' .
if tar -tzf "$TMP_DIR/backend-src.tar.gz" | grep -q '^\./data/'; then
  echo "错误: 打包内容包含 data/，已中止"; exit 1
fi

echo "==> 2/5 上传"
scp -P "$DEPLOY_PORT" "${SSH_OPTS[@]}" \
  "$TMP_DIR/backend-src.tar.gz" "$DEPLOY_USER@$DEPLOY_HOST:$REMOTE_TMP"

echo "==> 3/5 线上备份并切换代码"
ssh "${SSH_OPTS[@]}" -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" \
  "DEPLOY_DIR='$DEPLOY_DIR' REMOTE_TMP='$REMOTE_TMP' bash -s" <<'REMOTE'
set -euo pipefail
cd "$DEPLOY_DIR"
mkdir -p /www/backup
TS="$(date +%F-%H%M%S)"

echo "    - 解压新代码到 backend.new"
rm -rf backend.new
mkdir backend.new
tar -xzf "$REMOTE_TMP" -C backend.new
[ -f backend.new/app.py ] || { echo "错误: 上传包缺少 app.py"; exit 1; }
[ -f backend.new/Dockerfile ] || { echo "错误: 上传包缺少 Dockerfile"; exit 1; }
if [ -f backend/.dockerignore ] && [ ! -f backend.new/.dockerignore ]; then
  cp backend/.dockerignore backend.new/.dockerignore
fi

echo "    - 停止 backend"
docker compose stop backend || true

echo "    - 备份 index.db"
sqlite3 backend/data/index.db ".backup /www/backup/index-$TS.db"

echo "    - 复制数据到新目录"
cp -a backend/data backend.new/data

echo "    - 切换目录（旧代码保留作回滚）"
mv backend "backend.old.$TS"
mv backend.new backend

echo "    - 构建新镜像"
if ! docker compose build backend; then
  echo "构建失败，回滚到旧代码..."
  rm -rf backend
  mv "backend.old.$TS" backend
  docker compose up -d --no-build backend || true
  exit 1
fi

echo "    - 启动 backend"
if ! docker compose up -d --no-build backend; then
  echo "启动失败，回滚到旧代码..."
  docker compose stop backend || true
  rm -rf backend
  mv "backend.old.$TS" backend
  docker compose build backend || true
  docker compose up -d --no-build backend || true
  exit 1
fi

rm -f "$REMOTE_TMP"
echo "    - 当前状态"
docker compose ps
sleep 2
echo "    - 最近日志"
docker compose logs --tail=40 backend
echo "    旧代码: backend.old.$TS（确认稳定后可删除）"
REMOTE

echo "==> 4/5 公网验证"
sleep 2
curl -fsS -m 20 "https://$DEPLOY_DOMAIN/api/public/settings" >/dev/null
curl -fsS -m 20 "https://$DEPLOY_DOMAIN/" >/dev/null
echo "==> 5/5 完成: https://$DEPLOY_DOMAIN"
echo "如果接口异常，查看: ssh $DEPLOY_USER@$DEPLOY_HOST 'cd $DEPLOY_DIR && docker compose logs --tail=100 backend'"
