# -*- coding: utf-8 -*-
"""Hook 生效的地面真值验证（x86 与 x64 双靶子）。

不依赖控制器自检字段：靶子进程 winclock_probe 自己把「被 hook 的 QPC 时长」
与「真实墙钟时长」的比值写进文件。文件里的 ratio 就是最终判据——
1.0 = hook 没生效，5.0 = hook 生效且倍率正确。

流程（每个位数各跑一遍）：
  1. 启动靶子（先不注入）→ 期望 ratio ≈ 1.0（基线）
  2. inject → 期望 ratio ≈ 5.0
  3. setspeed 8 → 期望 ratio ≈ 8.0（验证运行时可改 + 无跳变）
  4. eject   → 期望 ratio ≈ 1.0（验证自卸载 + hook 摘除干净）
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools", "booster")
BOOSTER = os.path.join(TOOLS, "booster.exe")
OUTDIR = os.path.join(ROOT, "backend", "data")
os.makedirs(OUTDIR, exist_ok=True)


def run(cmd):
    r = subprocess.run([BOOSTER] + cmd, capture_output=True, timeout=25,
                       encoding="mbcs", errors="replace")
    return ((r.stdout or "") + (r.stderr or "")).strip()


def pids_of(name):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                       capture_output=True, encoding="mbcs", errors="replace")
    out = []
    for line in (r.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            try:
                out.append(int(parts[1]))
            except ValueError:
                pass
    return out


def last_ratio(path):
    """读探针文件最后一行的 ratio。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if "ratio=" in ln]
        if lines:
            for tok in lines[-1].split():
                if tok.startswith("ratio="):
                    return float(tok.split("=")[1])
    except OSError:
        pass
    return None


def verify(bits):
    exe = os.path.join(TOOLS, f"winclock{bits}.exe")
    out = os.path.join(OUTDIR, f"probe_{bits}.txt")
    if os.path.exists(out):
        os.remove(out)
    print(f"\n{'='*60}\n=== {bits} 位靶子：{os.path.basename(exe)} ===\n{'='*60}")

    proc = subprocess.Popen([exe, out, "60"])
    time.sleep(3)
    pids = pids_of(os.path.basename(exe))
    if not pids:
        print("FAIL: 靶子未启动")
        proc.kill()
        return False
    pid = pids[0]
    print(f"[target] pid={pid}")

    results = {}

    def sample(label, wait=4.0):
        time.sleep(wait)
        r = last_ratio(out)
        results[label] = r
        print(f"  {label:<22} ratio={r}")
        return r

    print("[基线] 未注入")
    baseline = sample("baseline")

    print(f"[inject] {run(['inject', str(pid)])[:70]}")
    injected = sample("inject F=5")

    print(f"[setspeed] {run(['setspeed', '8', str(pid)])[:70]}")
    changed = sample("setspeed F=8")

    print("[status]")
    print("  " + run(["status", str(pid)]).replace("\n", "\n  "))

    print(f"[eject] {run(['eject', str(pid)])[:70]}")
    ejected = sample("after eject", wait=5.0)

    ok = True
    def check(name, got, want, tol=0.35):
        nonlocal ok
        good = got is not None and abs(got - want) <= tol
        print(f"  {'PASS' if good else 'FAIL'}  {name}: got={got} want≈{want}")
        ok = ok and good

    print(f"\n--- {bits} 位判定 ---")
    check("基线 ratio ≈ 1.0", baseline, 1.0)
    check("注入后 ratio ≈ 5.0", injected, 5.0)
    check("改速后 ratio ≈ 8.0", changed, 8.0)
    check("卸载后 ratio ≈ 1.0", ejected, 1.0)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return ok


def main():
    allok = True
    for bits in (64, 32):
        allok = verify(bits) and allok
    print("\n" + "=" * 60)
    print("总判定:", "全部通过 ✔" if allok else "存在失败 ✘")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
