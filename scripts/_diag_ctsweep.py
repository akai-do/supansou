# -*- coding: utf-8 -*-
"""临时验收：clienttype 全扫描 + origin=dlna + type 变体，寻找不被限速的直链形态。"""
import sys
import json
import time
import sqlite3

import requests

sys.path.insert(0, "backend")
from accel.baidu import BaiduShareClient, normalize_share_text  # noqa: E402

# 1) 杀掉 aria2 释放带宽
subprocess_killed = False
try:
    import subprocess
    subprocess.run(["taskkill", "/F", "/IM", "aria2c.exe"], capture_output=True)
    subprocess_killed = True
except Exception:
    pass
print("aria2 killed:", subprocess_killed)

db = sqlite3.connect("backend/data/accel.db")
db.row_factory = sqlite3.Row
bduss = db.execute("SELECT value FROM accel_settings WHERE key='bduss'").fetchone()["value"]

norm = normalize_share_text("https://pan.baidu.com/s/1QgVrAecdoDjmwBhAaKsb6Q?pwd=kick")
surl, pwd = norm["surl"], norm["pwd"]
client = BaiduShareClient(bduss)
ctx = client.resolve(surl, pwd)
dirs = [f for f in ctx["root_files"] if f["is_dir"]]
sub = client.list_dir(ctx, dirs[0]["path"])
fid = "821950993956444"  # 45MB 样本
ref = f"{PAN}/s/{surl}" if False else f"https://pan.baidu.com/s/{surl}"
from accel.baidu import PAN  # noqa: E402
ref = f"{PAN}/s/{surl}"


def issue_dlink(clienttype, origin=None, typ="nolimit"):
    sign, ts = client._fetch_sign(surl)
    params = {"app_id": 250528, "channel": "chunlei", "clienttype": clienttype,
              "web": 1, "sign": sign, "timestamp": ts}
    if origin:
        params["origin"] = origin
    r = client._post("/api/sharedownload", params=params,
                     data={"encrypt": "0",
                           "extra": json.dumps({"sekey": ctx["seckey"]}),
                           "fid_list": json.dumps([int(fid)]),
                           "primaryid": ctx["shareid"], "product": "share",
                           "type": typ, "uk": ctx["uk"]},
                     refer=ref)
    j = r.json()
    if j.get("errno") != 0:
        return None, f"errno={j.get('errno')} {j.get('show_msg', '')[:30]}"
    lst = j.get("list") or []
    return (lst[0]["dlink"] if lst else None), "ok"


def measure(dlink, ua="netdisk;", seconds=8):
    s = requests.Session()
    s.headers.update({"User-Agent": ua, "Cookie": f"BDUSS={client.bduss}"})
    t0 = time.time()
    got = 0
    try:
        r = s.get(dlink, stream=True, timeout=(10, 10), allow_redirects=True)
        for chunk in r.iter_content(65536):
            got += len(chunk)
            if time.time() - t0 > seconds or got >= 4 * 1024 * 1024:
                break
        r.close()
    except Exception as e:
        return f"异常 {e.__class__.__name__}"
    dt = time.time() - t0
    return f"{got/1e6:.2f}MB/{dt:.1f}s = {got/dt/1e6:.2f}MB/s" if dt > 0.5 else "太短"


print("== clienttype 扫描（type=nolimit）==")
for ct in (0, 1, 2, 3, 4, 5, 7, 8, 11, 12):
    try:
        dl, err = issue_dlink(ct)
    except Exception as e:
        print(f"ct={ct}: 发券失败 {e.__class__.__name__}")
        continue
    if not dl:
        print(f"ct={ct}: 不可用（{err}）")
        continue
    print(f"ct={ct}: {measure(dl)}")

print("== 其他变体（ct=0）==")
for tag, kw in (("origin=dlna", {"origin": "dlna"}), ("type=share", {"typ": "share"})):
    try:
        dl, err = issue_dlink(0, **kw)
    except Exception as e:
        print(f"{tag}: 失败 {e.__class__.__name__}")
        continue
    print(f"{tag}: {'不可用 ' + err if not dl else measure(dl)}")
