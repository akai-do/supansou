"""Booster Pilot（M0.5 v2）：OpenSpeedy 闭环自动调速（数据校准版）。

v2 变更（基于 2026-09-13 实测 CSV 校准）：
- 判塌陷改用 10s 窗口均值对比（相邻样本抖动 237KB/s，逐样本判据必然误判）；
- 塌陷需连续 2 个快照成立（持续性），基线取此前 3 个窗口均值；
- 底档塌陷改为 RECOVER 动作（断开重连），不再无意义的"降档"；
- 连续 3 次 RECOVER 无效 → 判定瓶颈在服务端，进入长冷却并明示。

用法：
  python scripts/booster_pilot.py --list              # 查看进程/bridge/当前倍率
  python scripts/booster_pilot.py --inject            # 注入所有百度进程并启用加速
  python scripts/booster_pilot.py --auto --target 8   # 闭环自动调速（推荐）
  python scripts/booster_pilot.py --factor 10         # 固定倍率监控（对照实验）
"""
import argparse
import csv
import os
import sys
import time
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from accel.openspeedy import (SpeedSampler, bridge_call, enable,  # noqa: E402
                              find_bridge_exe, inject, list_baidu_processes,
                              set_speed)

LADDER = [1, 2, 3, 4, 5, 6, 8, 10, 15]
FLOOR_BPS = 100 * 1024          # 速率地板
WIN_SEC = 10                    # 快照窗口
COLLAPSE_RATIO = 0.45           # recent < 45% × base 判为塌陷
COLLAPSE_SNAPS = 2              # 连续 2 个快照成立
BASE_WINDOWS = 3                # 基线 = 前 3 个窗口均值
RECOVER_LIMIT = 3               # 连续恢复次数上限，超过判服务端限速
SERVER_COOLDOWN = 600           # 服务端限速判定后的长冷却（秒）


def fmt(bps: float) -> str:
    return f"{bps / 1e6:.2f}MB/s"


def mean(seq):
    return sum(seq) / len(seq) if seq else 0.0


def main():
    ap = argparse.ArgumentParser(description="百度网盘客户端闭环自动调速（驱动 OpenSpeedy）")
    ap.add_argument("--list", action="store_true", help="查看进程/bridge/当前倍率")
    ap.add_argument("--inject", action="store_true", help="注入所有百度进程并启用")
    ap.add_argument("--factor", type=float, default=0, help="固定倍率（0=闭环自动）")
    ap.add_argument("--auto", action="store_true", help="闭环自动调速")
    ap.add_argument("--target", type=float, default=8.0, help="目标速度 MB/s（自动模式）")
    ap.add_argument("--start-factor", type=float, default=5.0, help="自动模式起始倍率")
    ap.add_argument("--seconds", type=int, default=0, help="运行秒数（0=直到 Ctrl+C）")
    ap.add_argument("--csv", default="", help="CSV 输出路径")
    args = ap.parse_args()

    bridge = find_bridge_exe()
    print(f"[bridge] {bridge or '未找到（请确认 OpenSpeedy 已安装）'}")

    procs = list_baidu_processes()
    print(f"[进程] {len(procs)} 个："
          + (", ".join(f"{p['name']}({p['pid']})" for p in procs[:5])
             + (f" …共{len(procs)}" if len(procs) > 5 else "") or "客户端未运行"))

    if args.list:
        if bridge:
            try:
                print(f"[GETSPEED] {bridge_call(bridge, 'GETSPEED')}")
            except Exception as e:
                print(f"[GETSPEED] 失败: {e.__class__.__name__}（OpenSpeedy 未运行？）")
        return 0

    if not procs:
        print("!! 百度网盘客户端未运行，请先打开客户端并开始一个下载")
        return 1
    if not bridge:
        print("!! 未找到 OpenSpeedy（bridge32/64.exe），请确认安装位置")
        return 1

    pids = [p["pid"] for p in procs]

    if args.inject:
        for p in procs:
            try:
                print(f"[inject] {p['name']}({p['pid']}): {inject(bridge, p['pid'])[:60]}")
            except Exception as e:
                print(f"[inject] {p['pid']} 失败: {e.__class__.__name__}")
    for pid in pids:
        try:
            enable(bridge, pid)
        except Exception:
            pass

    auto = args.auto or args.factor <= 0
    ladder_idx = min(range(len(LADDER)),
                     key=lambda i: abs(LADDER[i] - (args.factor or args.start_factor)))
    factor = LADDER[ladder_idx]
    try:
        print(f"[speed] {set_speed(bridge, factor)}")
    except Exception as e:
        print(f"!! SETSPEED 失败: {e.__class__.__name__}（请先在 OpenSpeedy 界面完成注入，"
              f"或以管理员重试）")
        return 1
    print(f"[模式] {'闭环自动' if auto else f'固定 {factor}x'}"
          f"{'，目标 ' + str(args.target) + 'MB/s' if auto else ''} — Ctrl+C 停止\n")

    sampler = SpeedSampler()
    csv_path = args.csv or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backend", "data", "booster_pilot.csv")
    csv_file = open(csv_path, "w", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(["t", "factor", "speed_bps", "state"])

    recent = deque(maxlen=8)          # 最近 8s 样本
    base_hist = deque(maxlen=BASE_WINDOWS)  # 历史快照（每 WIN_SEC 一个均值）
    last_snapshot = time.time()
    collapse_snaps = 0
    low_since = 0.0
    recover_count = 0
    server_cooldown_until = 0.0
    last_change = time.time()
    ewma = 0.0
    state = "RAMP"
    t_start = time.time()
    log_lines = []

    def note(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line)
        log_lines.append(line)

    def reset_baseline(cur_speed):
        base_hist.clear()
        recent.clear()
        base_hist.append(cur_speed)
        last_change = time.time()
        return last_change

    note(f"起始倍率 {factor}x")
    t_last_sample = time.time()
    try:
        while True:
            time.sleep(1.0)
            pids_now = [p["pid"] for p in list_baidu_processes()] or pids
            speed = sampler.sample(pids_now)
            now = time.time()
            ewma = ewma * 0.6 + speed * 0.4
            recent.append(speed)
            csv_writer.writerow([round(now - t_start), factor, round(ewma), state])
            csv_file.flush()

            if now - t_last_sample >= WIN_SEC and len(recent) >= 5:
                recent_avg = mean(recent)
                base_avg = mean(base_hist) if base_hist else 0.0
                t_last_sample = now
                # 快照推进
                if len(base_hist) >= BASE_WINDOWS:
                    base_hist.popleft()
                # 注意：base 保存的是"上一个窗口"的均值（窗口结束后再入列）
                prev_windows = list(base_hist)
                base_hist.append(recent_avg)
                if len(prev_windows) >= 1:
                    base_avg = mean(prev_windows) if len(prev_windows) >= 2 \
                        else prev_windows[0]
                else:
                    base_avg = 0.0
                recent_avg_snap = mean(recent)

                if not auto:
                    continue

                # 服务端限速长冷却中
                if now < server_cooldown_until:
                    print(f"  {round(now - t_start)}s  [服务端限速冷却中 "
                          f"{round(server_cooldown_until - now)}s] {fmt(recent_avg_snap)}")
                    continue

                # 归零恢复
                if base_avg > FLOOR_BPS and recent_avg_snap < FLOOR_BPS:
                    if not low_since:
                        low_since = now
                    elif now - low_since > 10:
                        note("速率归零超 10s → 恢复：全部进程重新 ENABLE")
                        for pid in pids_now:
                            try:
                                enable(bridge, pid)
                            except Exception:
                                pass
                        low_since = now
                else:
                    low_since = 0.0

                # 塌陷判定（窗口对比 + 持续性）
                if base_avg > 500 * 1024 and len(prev_windows) >= 2 \
                        and recent_avg_snap < base_avg * COLLAPSE_RATIO:
                    collapse_snaps += 1
                else:
                    collapse_snaps = 0

                if collapse_snaps >= COLLAPSE_SNAPS and now - last_change > 8:
                    collapse_snaps = 0
                    if ladder_idx > 0:
                        new_idx = max(0, ladder_idx - 2)
                        note(f"塌陷（{fmt(recent_avg_snap)} < 45%×基线 {fmt(base_avg)}）"
                             f"→ 倍率 {factor}→{LADDER[new_idx]}")
                        ladder_idx = new_idx
                        factor = LADDER[ladder_idx]
                        set_speed(bridge, factor)
                        base_hist.clear()
                        base_hist.append(recent_avg_snap)
                        recent.clear()
                        last_change = reset_baseline(recent_avg_snap)
                        state = "BACKOFF"
                    else:
                        recover_count += 1
                        if recover_count > RECOVER_LIMIT:
                            server_cooldown_until = now + SERVER_COOLDOWN
                            recover_count = 0
                            note("连续恢复无效 → 判定瓶颈在服务端账号限速"
                                 f"（非倍率问题），冷却 {SERVER_COOLDOWN // 60} 分钟后自动重探。"
                                 "建议明天再试或换文件。")
                            state = "SERVER_LIMITED"
                        else:
                            note(f"底档塌陷 → RECOVER#{recover_count}："
                                 f"断开重连（DISABLE 3s → ENABLE），倍率回 {LADDER[2]}x")
                            for pid in pids_now:
                                try:
                                    bridge_call(bridge, f"DISABLE {pid}")
                                except Exception:
                                    pass
                            time.sleep(3)
                            for pid in pids_now:
                                try:
                                    enable(bridge, pid)
                                except Exception:
                                    pass
                            ladder_idx = 2
                            factor = LADDER[ladder_idx]
                            set_speed(bridge, factor)
                            base_hist.clear()
                            recent.clear()
                            last_change = reset_baseline(0)
                            state = "RECOVER"

                # 上探
                if (now - last_change > 45 and base_avg > 0
                        and recent_avg_snap >= base_avg * 0.85
                        and recent_avg_snap < args.target * 1e6
                        and ladder_idx < len(LADDER) - 1
                        and now > server_cooldown_until):
                    new_idx = ladder_idx + 1
                    note(f"稳定（{fmt(recent_avg_snap)} ≥ 85% 基线）→ 上探 {LADDER[new_idx]}x")
                    ladder_idx = new_idx
                    factor = LADDER[ladder_idx]
                    set_speed(bridge, factor)
                    base_hist.clear()
                    base_hist.append(recent_avg_snap)
                    last_change = time.time()
                    state = "RAMP"
                elif recent_avg_snap >= args.target * 1e6 * 0.9:
                    state = "HOLD"

            if args.seconds and now - t_start > args.seconds:
                note("到达限时，正常退出")
                break
    except KeyboardInterrupt:
        note("手动停止")
    finally:
        csv_file.close()
        note(f"CSV 已保存: {csv_path}")
        if log_lines:
            print("\n".join(log_lines[-8:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
