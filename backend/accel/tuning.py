"""提速配置中心：档位 → aria2 全局参数 / 单任务参数。

把散落在 aria2.py 里的魔法数字收敛到这里，调度器（scheduler.py）与
REST 层（routes.py）都只依赖本模块导出的常量与构造函数。

为什么百度网盘需要多连接：
  百度 pcs 直链在 CDN 侧对**单条 TCP 连接**做限速（非会员档尤其明显），
  但同一 dlink 允许并发多条连接，因此 16~64 连接可以把带宽叠加上去；
  叠加到某个上限后不再增长，那就是**账号级限额**——此时只有换账号/换
  文件分流才能继续提升，本模块的 segmented 模式即为此而生。
"""
from __future__ import annotations

# ---------- 档位 ----------
# split            : aria2 单任务最大分片数（≈ 并发连接数上限）
# conn             : 同一服务器允许的最大连接数
# min_split        : 最小分片大小；越小 → 小文件也能开更多连接
# concurrent       : 允许同时处于 active 的下载任务数（调度器据此抢占）
# ramp_max         : 自适应爬坡允许达到的最大 split（<= split）
PROFILES = {
    "eco": {
        "label": "省流（低占用）",
        "split": 8, "conn": 8, "min_split": "1M",
        "concurrent": 1, "ramp_max": 8,
        "desc": "弱网/机械盘/被限速严重时用，连接最少、对其它任务打扰最小",
    },
    "balanced": {
        "label": "均衡（默认）",
        "split": 16, "conn": 16, "min_split": "1M",
        "concurrent": 2, "ramp_max": 24,
        "desc": "多数家用宽带的最优点，单任务 16 连接已能吃满百兆",
    },
    "turbo": {
        "label": "加速（推荐）",
        "split": 32, "conn": 32, "min_split": "512K",
        "concurrent": 3, "ramp_max": 48,
        "desc": "单连接被限速时叠加连接，同时允许 3 个任务并行分流",
    },
    "extreme": {
        "label": "极限（多账号必备）",
        "split": 64, "conn": 64, "min_split": "256K",
        "concurrent": 4, "ramp_max": 64,
        "desc": "64 连接 + 4 任务并发；配合多账号池才能跑出叠加带宽",
    },
}
DEFAULT_PROFILE = "turbo"

# ---------- 调度器默认值 ----------
DEFAULTS = {
    "profile": DEFAULT_PROFILE,
    # 0 表示取档位自带的 concurrent
    "max_concurrent": 0,
    # 慢任务让路：速度低于该值（字节/秒）且连续命中 slow_ticks 次就暂停让位
    "slow_threshold": 256 * 1024,
    "slow_ticks": 4,
    # 让路总开关；关掉后只做并发槽限制，不主动暂停任务
    "yield_enabled": True,
    # 让路后至少等这么久才允许恢复被让路的任务（秒）
    "yield_cooldown": 45,
    # 全局总带宽上限（字节/秒），0 = 不限
    "overall_limit": 0,
    # 自适应连接爬坡
    "ramp_enabled": True,
    "ramp_interval": 20,     # 同一任务两次调参的最小间隔（秒）
    "ramp_up_gain": 0.06,    # 速度提升超过 6% 才继续加连接
    "ramp_down_drop": 0.12,  # 速度下降超过 12% 立即回退一档
    # 多账号：单文件分段跨账号下载
    "segmented": False,
    "seg_min_size": 32 * 1024 * 1024,   # 小于该值的文件不分段
    "seg_max_parts": 4,                 # 单文件最多切成几段（≈ 用几个账号）
    # aria2 自身的自动并发优化（1.34+）
    "optimize_concurrent": True,
}

SCHED_TICK = 2.0          # 调度器采样间隔（秒）
EWMA_ALPHA = 0.45         # 速度指数滑动平均系数（越小越平滑）

# ---------- 诊断阈值 ----------
# 归因判定用
DIAG = {
    "idle_speed": 64 * 1024,         # 低于此速度视为"几乎没在动"
    "conn_gain_min": 0.15,           # 连接翻倍后速度至少涨这么多才算"连接有用"
    "cap_spread": 0.20,              # 多任务速度互相之间差异 < 20% → 疑似同一账号上限
    "local_cap_ratio": 0.85,         # 总速度 / overall_limit 超过该比例 → 触顶本地上限
}


def profile(name: str) -> dict:
    """取档位定义，未知档位回退默认。"""
    return PROFILES.get(name) or PROFILES[DEFAULT_PROFILE]


def resolve_config(saved: dict | None) -> dict:
    """把数据库里存的设置合并成一份完整配置（缺字段补默认）。"""
    cfg = dict(DEFAULTS)
    for k, v in (saved or {}).items():
        if k in cfg and v is not None and v != "":
            cfg[k] = v
    if cfg.get("profile") not in PROFILES:
        cfg["profile"] = DEFAULT_PROFILE
    return cfg


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def global_options(cfg: dict) -> dict:
    """调度器/启动器用的 aria2 **全局**选项。"""
    p = profile(cfg["profile"])
    concurrent = _int(cfg.get("max_concurrent")) or p["concurrent"]
    opts = {
        # 调度器接管并发，aria2 自身不要再额外排队
        "max-concurrent-downloads": str(concurrent),
        # 全局带宽闸门（0/缺失 = 不限制）
        "max-overall-download-limit": str(_int(cfg.get("overall_limit"))) + "B"
        if _int(cfg.get("overall_limit")) else "0",
        # 自动并发优化：aria2 会根据文件大小自己调 split，避免小文件开一堆连接
        "optimize-concurrent-downloads": "true" if cfg.get("optimize_concurrent") else "false",
        "file-allocation": "none",
        "continue": "true",
        "auto-file-renaming": "true",
        "allow-overwrite": "false",
        "summary-interval": "0",
        # 直链带签名，证书校验对本地速度无影响但要 RSA 计算，关掉省 CPU
        "check-certificate": "false",
        "max-tries": "5",
        "retry-wait": "2",
        "connect-timeout": "15",
        "timeout": "30",
        "max-file-not-found": "2",
    }
    return opts


def task_options(cfg: dict, split: int | None = None) -> dict:
    """单任务选项。split 显式传入时覆盖档位值（自适应爬坡用）。"""
    p = profile(cfg["profile"])
    sp = int(split or p["split"])
    return {
        "split": str(sp),
        "max-connection-per-server": str(sp if split is not None else p["conn"]),
        "min-split-size": p["min_split"],
        "continue": "true",
        "max-tries": "5",
        "retry-wait": "2",
    }


def clamp_split(cfg: dict, split: int) -> int:
    """限制自适应爬坡的取值范围：2 ~ 档位 ramp_max。"""
    p = profile(cfg["profile"])
    return max(2, min(int(split), int(p["ramp_max"])))


def cli_args(binary: str, port: int, secret: str, download_dir: str,
             cfg: dict, stop_pid: int | None = None) -> list[str]:
    """拼 aria2c 命令行。

    布尔选项必须 `--xxx=true` 连写：分写成两个 argv 会被 aria2 当成
    额外下载 URI 从而直接退出（Unrecognized URI: true）。
    """
    import os
    args = [
        binary, "--enable-rpc",
        "--rpc-listen-port", str(port),
        "--rpc-secret", secret,
        "--dir", download_dir,
        "--rpc-listen-all=false",
        # 控制文件（.aria2）自动保存，保证崩溃后可续传
        "--auto-save-interval=30",
    ]
    if stop_pid:
        args += ["--stop-with-process", str(stop_pid)]
    for k, v in global_options(cfg).items():
        args.append(f"--{k}={v}")
    if os.name == "nt":
        args += ["--console-log-level=warn"]
    return args
