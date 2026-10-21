#!/usr/bin/env bash
# 在七牛云 LAS 新服务器（Ubuntu 24.04）上以 root 执行。
# 前置：/root/migrate-20261021/{site,data,nginx}.tar.gz + index.db + accel.db 已传上来。
set -euo pipefail

BUNDLE="${BUNDLE:-/root/migrate-20261021}"
APP_DIR="${APP_DIR:-/www/wwwroot/dupansou}"

for f in site.tar.gz data.tar.gz index.db accel.db; do
  [ -f "$BUNDLE/$f" ] || { echo "缺少 $BUNDLE/$f，先完成传输"; exit 1; }
done

echo "== 1/6 时区、swap、基础工具 =="
timedatectl set-timezone Asia/Shanghai 2>/dev/null || true
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl tar gnupg
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "已创建 2G swap"
else
  echo "已有 swap，跳过"
fi

echo "== 2/6 安装 Docker =="
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  . /etc/os-release
  # 香港机器优先官方源；官方不可达再退回阿里云镜像
  if curl -fsSL --connect-timeout 10 https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.gpg; then
    docker_repo=https://download.docker.com/linux/ubuntu
  else
    docker_repo=https://mirrors.aliyun.com/docker-ce/linux/ubuntu
    curl -fsSL --connect-timeout 10 "$docker_repo/gpg" -o /tmp/docker.gpg
  fi
  gpg --dearmor < /tmp/docker.gpg > /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] ${docker_repo} ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt_ok=0
  for i in 1 2 3; do
    if apt-get update -y; then apt_ok=1; break; fi
    echo "apt-get update 第 $i 次失败，清理后重试"
    apt-get clean
    sleep 5
  done
  if [ "$apt_ok" = "1" ] && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
    :
  else
    echo "Docker 官方源安装失败，改用 Ubuntu 自带 docker.io"
    rm -f /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker.io docker-compose-v2
  fi
  systemctl enable --now docker
else
  echo "Docker 已安装，跳过"
fi
docker --version
docker compose version

echo "== 3/6 还原站点文件 =="
mkdir -p "$APP_DIR/downloads"
tar xzf "$BUNDLE/site.tar.gz" -C "$APP_DIR"
tar xzf "$BUNDLE/data.tar.gz" -C "$APP_DIR/backend"
install -m 600 "$BUNDLE/index.db" "$APP_DIR/backend/data/index.db"
install -m 600 "$BUNDLE/accel.db" "$APP_DIR/backend/data/accel.db"
# 一致性备份已包含 WAL 内容，旧 WAL/SHM 不能带过来
rm -f "$APP_DIR/backend/data/index.db-wal" "$APP_DIR/backend/data/index.db-shm" \
      "$APP_DIR/backend/data/accel.db-wal" "$APP_DIR/backend/data/accel.db-shm"

echo "== 4/6 把后端端口改成 0.0.0.0:5000 =="
sed -i 's#"127.0.0.1:5000:5000"#"0.0.0.0:5000:5000"#' "$APP_DIR/docker-compose.yml"
grep -n '5000:5000' "$APP_DIR/docker-compose.yml"

echo "== 5/6 构建并启动（首次 3-8 分钟） =="
cd "$APP_DIR"
docker compose up -d --build

echo "== 6/6 健康检查 =="
ok=0
for _ in $(seq 1 40); do
  if curl -fsS --max-time 3 http://127.0.0.1:5000/api/health >/tmp/health.json 2>/dev/null; then
    cat /tmp/health.json; echo; ok=1; break
  fi
  sleep 3
done
docker compose ps
[ "$ok" = "1" ] || { echo "健康检查未通过，看 docker compose logs -f backend"; exit 1; }

cat <<'EOF'

完成。接下来在七牛控制台：
  1) 端口映射：内网 5000/TCP（用于直连 IP 测试）
  2) 域名访问：内网端口填 5000，绑定 www.xiaowusu.com / xiaowusu.com，签发或上传证书
  3) 按控制台给的 CNAME 目标去 DNS 服务商添加记录
EOF
