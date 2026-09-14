# -*- coding: utf-8 -*-
"""临时验收：检查当前 BDUSS 账号的会员身份与状态。"""
import sys
import sqlite3

sys.path.insert(0, "backend")
from accel.baidu import BaiduShareClient  # noqa: E402

db = sqlite3.connect("backend/data/accel.db")
db.row_factory = sqlite3.Row
row = db.execute("SELECT value FROM accel_settings WHERE key='bduss'").fetchone()
bduss = row["value"] if row else ""
print("当前 BDUSS:", f"****{bduss[-4:]}" if bduss else "(未设置)")

client = BaiduShareClient(bduss)
r = client._get("/rest/2.0/membership/user/info",
                params={"method": "query", "clienttype": 0, "app_id": 250528, "web": 1})
j = r.json()
ui = j.get("user_info") or {}
print("errno:", j.get("errno"), "| error_code:", j.get("error_code"))
print("用户名:", ui.get("username") or ui.get("baidu_name") or ui.get("netdisk_name"))
print("uk:", ui.get("uk"))
print("is_svip(超级会员):", ui.get("is_svip"), "| is_vip(普通会员):", ui.get("is_vip"))
print("vip_identity:", ui.get("vip_identity"))
# 会员到期时间
for k in ("vip_end_at", "svip_end_at", "buy_vip_end_at", "vip_expire"):
    if ui.get(k):
        print(f"{k}:", ui.get(k))
# quota 复核
try:
    q = client._get("/api/quota", params={"checkfree": 1, "checkexpire": 1,
                                          "clienttype": 0, "web": 1}).json()
    print("quota errno:", q.get("errno"), "| 总容量:", round((q.get("total") or 0)/1e9, 1), "GB")
except Exception as e:
    print("quota 检查失败:", e.__class__.__name__)
