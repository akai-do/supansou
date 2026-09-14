"""网盘加速模块：百度网盘分享直链解析 + aria2 多线程下载 + 带宽调度提速。

独立 Blueprint（url_prefix=/api/accel），在 app.create_app() 中调用
register_accel(app) 即可，不与主搜索 API 耦合。

布局（第三版加入提速引擎）：
  store.py      sqlite 设置/任务表/账号池（backend/data/accel.db，gitignored）
  baidu.py      分享解析链（verify → 元数据 → 列目录 → sharedownload）
  aria2.py      aria2c 进程/外部 RPC 管理与 JSON-RPC 客户端（含动态改参）
  tuning.py     加速档位 → aria2 参数的唯一推导处
  scheduler.py  带宽调度器：慢任务让路 / 并发槽分配 / 连接爬坡 / 限速归因
  pool.py       多账号池（账号级限速的根治手段）
  segments.py   分段跨账号下载与合并
  booster.py    客户端加速控制器：自研选择性 Hook DLL 的注入与闭环调速
  routes.py     REST 端点与访问控制
"""
import logging
import os
import threading

from flask import Blueprint

from . import tuning

accel_bp = Blueprint("accel", __name__)

# 提速配置在 settings 表里的键
CFG_KEY = "speedup"


def register_accel(app):
    from .aria2 import Aria2Manager
    from .baidu import BaiduShareClient
    from .booster import BoosterManager
    from .pool import AccountPool
    from .routes import register_routes
    from .scheduler import BandwidthScheduler
    from .segments import SegmentCoordinator
    from .store import AccelStore

    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    db_path = os.path.join(backend_root, "data", "accel.db")
    store = AccelStore(db_path)

    download_dir = os.getenv("ACCEL_DOWNLOAD_DIR") or os.path.join(
        os.path.dirname(backend_root), "downloads")
    os.makedirs(download_dir, exist_ok=True)

    # ---- 提速配置：进程内持有 + 落库，读多写少用 RLock 保护 ----
    cfg_lock = threading.RLock()
    cfg = tuning.resolve_config(store.get_json(CFG_KEY))

    def cfg_provider() -> dict:
        with cfg_lock:
            return dict(cfg)

    def cfg_update(new: dict) -> dict:
        with cfg_lock:
            merged = tuning.resolve_config({**cfg, **(new or {})})
            cfg.clear()
            cfg.update(merged)
            store.set_json(CFG_KEY, merged)
            return dict(merged)

    manager = Aria2Manager(download_dir, cfg)

    def primary_provider():
        return (store.get_setting("bduss") or "", store.get_setting("stoken") or "")

    pool = AccountPool(store, BaiduShareClient, primary_provider)
    segments = SegmentCoordinator(store, manager, pool, cfg_provider)

    scheduler = BandwidthScheduler(store, manager, cfg_provider,
                                  hooks=[segments.on_tick])

    # ---- 客户端加速控制器（自研选择性 Hook DLL 的注入与闭环调速） ----
    tools_dir = os.path.join(os.path.dirname(backend_root), "tools", "booster")
    booster = BoosterManager(tools_dir)

    register_routes(accel_bp, store, manager, BaiduShareClient,
                    cfg_provider=cfg_provider, cfg_update=cfg_update,
                    pool=pool, segments=segments, scheduler=scheduler,
                    booster=booster)
    app.register_blueprint(accel_bp, url_prefix="/api/accel")

    # 调度器随进程启动；多 worker 部署（gunicorn 多进程）时只让一个起来，
    # 其余用 ACCEL_NO_SCHEDULER=1 关掉，避免多个控制环互相抢 pause/unpause。
    if os.getenv("ACCEL_NO_SCHEDULER") != "1":
        scheduler.start()

    logging.getLogger("accel").info(
        "[加速] 模块已注册（数据库 %s，下载目录 %s，档位 %s，提速引擎 %s）",
        db_path, download_dir, cfg.get("profile"),
        "开启" if os.getenv("ACCEL_NO_SCHEDULER") != "1" else "关闭")
