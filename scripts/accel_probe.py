"""加速模块解析链探测脚本（spike）。

对真实分享链接跑通全链：BDUSS 校验 → 提取码验证 → 元数据三策略 → 列目录
→ sharedownload 换 dlink → 推送 aria2 RPC 实际下载。用于：
  1. 首次实施时敲定当前可用的参数组合与错误码；
  2. 日后百度接口变动时定位失效环节。

用法（BDUSS 获取：登录 pan.baidu.com → F12 → Application → Cookies → 复制 BDUSS）:
  set ACCEL_PROBE_BDUSS=xxxx                     # Windows
  export ACCEL_PROBE_BDUSS=xxxx                  # Linux/macOS
  python scripts/accel_probe.py --url "https://pan.baidu.com/s/1xxxx?pwd=abcd"
  python scripts/accel_probe.py --url "..." --dir "/子目录"      # 列子目录
  python scripts/accel_probe.py --url "..." --download 1         # 推 1 个文件到 aria2 实测

  # 默认推送到本机 Motrix/MotrixNext RPC；也可自起 aria2c 指定端口
  python scripts/accel_probe.py --url "..." --download 1 --rpc http://127.0.0.1:16800/jsonrpc
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from accel.baidu import BaiduError, BaiduShareClient, normalize_share_text  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="百度网盘解析链探测")
    ap.add_argument("--url", required=True, help="分享链接（可含 ?pwd= 或混排提取码）")
    ap.add_argument("--pwd", default="", help="提取码（不填则从 URL/文本自动提取）")
    ap.add_argument("--dir", default="", help="要列出的子目录路径")
    ap.add_argument("--download", type=int, default=0, metavar="N",
                    help="把前 N 个文件推送到 aria2 实测下载")
    ap.add_argument("--rpc", default=os.getenv("ACCEL_ARIA2_RPC",
                                               "http://127.0.0.1:16800/jsonrpc"))
    ap.add_argument("--rpc-secret", default=os.getenv("ACCEL_ARIA2_SECRET", ""))
    ap.add_argument("--bduss", default=os.getenv("ACCEL_PROBE_BDUSS", ""))
    args = ap.parse_args()

    if not args.bduss:
        print("!! 请先设置环境变量 ACCEL_PROBE_BDUSS（或 --bduss）")
        return 1
    print(f"[0] BDUSS: ****{args.bduss[-4:]}")

    try:
        norm = normalize_share_text(args.url)
    except BaiduError as e:
        print(f"[1] 链接归一化失败: {e.msg}")
        return 1
    pwd = args.pwd or norm["pwd"]
    print(f"[1] surl={norm['surl']}  pwd={pwd or '(无)'}")

    client = BaiduShareClient(args.bduss)

    try:
        client.check_login()
        print("[2] BDUSS 登录态有效（/api/quota errno=0）")
    except BaiduError as e:
        print(f"[2] BDUSS 校验失败: {e.msg} (errno={e.errno})")
        return 1

    t0 = time.time()
    try:
        ctx = client.resolve(norm["surl"], pwd)
    except BaiduError as e:
        print(f"[3] 解析失败: {e.msg} (errno={e.errno}, kind={e.kind})")
        return 1
    print(f"[3] 解析成功（{time.time() - t0:.1f}s）: shareid={ctx['shareid']} "
          f"uk={ctx['uk']} sign={ctx['sign'][:8]}… ts={ctx['timestamp']}")
    print(f"    根目录 {len(ctx['root_files'])} 项")

    if args.dir:
        try:
            files = client.list_dir(ctx, args.dir)
        except BaiduError as e:
            print(f"[4] 列目录失败: {e.msg} (errno={e.errno})")
            return 1
        print(f"[4] 目录 {args.dir}: {len(files)} 项")
    else:
        files = ctx["root_files"]

    printable = files[:20]
    for f in printable:
        kind = "目录" if f["is_dir"] else "文件"
        print(f"    - [{kind}] {f['name']}  ({f['size']} B, fid={f['fid']})")
    if len(files) > len(printable):
        print(f"    … 共 {len(files)} 项，仅显示前 20")

    if not args.download:
        print("[5] 未指定 --download，直链环节未测试（正常结束）")
        return 0

    fids = [f["fid"] for f in files if not f["is_dir"]][:args.download]
    if not fids:
        print("[5] 没有可下载的文件")
        return 1
    try:
        dlinks = client.fetch_dlinks(ctx, fids)
    except BaiduError as e:
        print(f"[5] 换直链失败: {e.msg} (errno={e.errno}, kind={e.kind})")
        return 1
    print(f"[5] 取得 {len(dlinks)} 条直链：")
    for d in dlinks:
        print(f"    - {d['filename']}  dlink 前缀: {d['dlink'][:60]}…")

    import requests as _rq
    pushed = []
    for d in dlinks:
        payload = {"jsonrpc": "2.0", "id": "probe",
                   "method": "aria2.addUri",
                   "params": [[d["dlink"]], {
                       "out": d["filename"],
                       "header": [f"Cookie: {client.cookie_header()}",
                                  f"Referer: https://pan.baidu.com/s/{norm['surl']}"],
                       "split": "16", "max-connection-per-server": "16",
                       "min-split-size": "1M"}]}
        if args.rpc_secret:
            payload["params"] = ["token:" + args.rpc_secret] + payload["params"]
        r = _rq.post(args.rpc, json=payload, timeout=5).json()
        if "error" in r:
            print(f"[6] aria2 推送失败: {r['error'].get('message')}")
            return 1
        pushed.append(r["result"])
        print(f"[6] 已推送 aria2: gid={r['result']}  {d['filename']}")

    print("[7] 下载状态（10s 采样）:")
    for i in range(5):
        time.sleep(2)
        lines = []
        for gid in pushed:
            payload = {"jsonrpc": "2.0", "id": "probe",
                       "method": "aria2.tellStatus"}
            payload["params"] = (["token:" + args.rpc_secret] if args.rpc_secret else []) \
                + [gid, ["status", "completedLength", "totalLength", "downloadSpeed"]]
            r = _rq.post(args.rpc, json=payload, timeout=5).json()
            s = r.get("result") or {}
            done = int(s.get("completedLength") or 0)
            total = int(s.get("totalLength") or 0)
            speed = int(s.get("downloadSpeed") or 0)
            lines.append(f"gid={gid[:8]} {s.get('status')} "
                         f"{done / 1e6:.1f}/{total / 1e6:.1f}MB {speed / 1e6:.2f}MB/s")
        print(f"    t={2 * (i + 1)}s  " + "  |  ".join(lines))
    print("OK 全链路探测完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
