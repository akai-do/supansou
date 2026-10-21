#!/usr/bin/env bash
# 在【旧服务器】上执行：把最新数据同步到七牛新机并重启后端。
# 用途：DNS 切换前 10 分钟跑一次，把 00:20 之后的新索引、需求板、评论带过去。
set -euo pipefail

NEW_IP=38.127.133.6
NEW_PORT=10029
B=/root/migrate-20261021
OLD_DATA=/www/wwwroot/dupansou/backend/data

echo "== 1/3 旧机做一致性备份 =="
python3 - <<'PY'
import sqlite3
for s, d in [("/www/wwwroot/dupansou/backend/data/index.db", "/root/migrate-20261021/index.db"),
             ("/www/wwwroot/dupansou/backend/data/accel.db", "/root/migrate-20261021/accel.db")]:
    a = sqlite3.connect(s)
    b = sqlite3.connect(d)
    a.backup(b)
    b.close()
    a.close()
    print("ok:", s)
PY

echo "== 2/3 传到新机 =="
scp -P "$NEW_PORT" -o BatchMode=yes "$B/index.db" "$B/accel.db" "root@$NEW_IP:$B/"

echo "== 3/3 新机停后端 → 换库 → 起后端 =="
ssh -p "$NEW_PORT" -o BatchMode=yes "root@$NEW_IP" '
set -e
cd /www/wwwroot/dupansou
docker compose stop backend
install -m 600 /root/migrate-20261021/index.db backend/data/index.db
install -m 600 /root/migrate-20261021/accel.db backend/data/accel.db
rm -f backend/data/index.db-wal backend/data/index.db-shm
docker compose start backend
for i in $(seq 1 20); do
  if curl -fsS --max-time 3 http://127.0.0.1:5000/api/health; then echo; break; fi
  sleep 2
done
'
echo "同步完成。现在可以去改 DNS 了。"
