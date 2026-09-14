"""aria2 接入：外部 RPC（调试用）或内置 aria2c 子进程（默认，用户零安装）。

端点优先级：
  1. ACCEL_ARIA2_RPC  —— 外部 JSON-RPC 地址（如 http://127.0.0.1:16800/jsonrpc，
                         联调 MotrixNext 时用），可选 ACCEL_ARIA2_SECRET
  2. ACCEL_ARIA2_PATH —— 内置二进制显式路径
  3. tools/aria2c(.exe) —— scripts/fetch_aria2.py 下载的位置（缺失时自动补拉）
  4. PATH 中的 aria2c

第三版新增（配合 tuning/scheduler 做动态提速）：
  * 启动参数全部来自 tuning.cli_args（档位可配，不再硬编码 split=16）
  * tell_active / tell_waiting / global_stat —— 调度器采样用
  * change_option / change_global_option —— 运行中改连接数与带宽闸门
  * add_download(..., options=...) —— 分段任务可传 Range / out / split 覆盖

安全约定：rpc-secret 不出本模块、不写日志；任务头里的 Cookie 只经内存传给 aria2。
"""
import atexit
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time

import requests

from .baidu import UA
from . import tuning

logger = logging.getLogger("accel")

RPC_TIMEOUT = 5
POLL_TRIES = 60
POLL_INTERVAL = 0.5

# 调度器采样需要的字段
_STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed",
                "uploadSpeed", "connections", "numSeeders", "errorCode",
                "errorMessage", "files", "dir", "bittorrent", "infoHash"]


class Aria2Error(Exception):
    pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _repo_root() -> str:
    # backend/accel/aria2.py → 仓库根
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Aria2Manager:
    def __init__(self, download_dir: str, cfg: dict | None = None):
        self.download_dir = download_dir
        self.mode = "external" if os.getenv("ACCEL_ARIA2_RPC") else "builtin"
        self._rpc_url = os.getenv("ACCEL_ARIA2_RPC", "")
        self._external_secret = os.getenv("ACCEL_ARIA2_SECRET", "")
        self.cfg = cfg or tuning.resolve_config(None)
        self._proc = None
        self._secret = ""
        self._port = 0
        self._logf = None
        self._lock = threading.Lock()
        # 本地 RPC 复用连接：调度器每 2 秒要打 4~6 次 RPC，
        # 每次新建 TCP 在 Windows 上会留下大量 TIME_WAIT
        self._sess = requests.Session()
        self._sess.mount("http://127.0.0.1", requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=8))
        atexit.register(self.shutdown)

    # ---------- 配置 ----------
    def apply_config(self, cfg: dict):
        """热更新配置，并把全局选项推给 aria2（失败不抛，仅记日志）。"""
        self.cfg = cfg
        if self.mode == "external":
            return
        try:
            self.change_global_option(tuning.global_options(cfg))
        except Exception as e:
            logger.warning("[加速] 全局选项热更新失败: %s", e.__class__.__name__)

    # ---------- 进程管理 ----------
    def _find_binary(self):
        p = os.getenv("ACCEL_ARIA2_PATH")
        if p and os.path.isfile(p):
            return p
        name = "aria2c.exe" if os.name == "nt" else "aria2c"
        cand = os.path.join(_repo_root(), "tools", name)
        if os.path.isfile(cand):
            return cand
        return shutil.which("aria2c")

    def _auto_fetch(self):
        """首次启动缺二进制时尝试 scripts/fetch_aria2.py（可 ACCEL_NO_AUTO_FETCH=1 关闭）。"""
        if os.getenv("ACCEL_NO_AUTO_FETCH") == "1":
            return False
        script = os.path.join(_repo_root(), "scripts", "fetch_aria2.py")
        if not os.path.isfile(script):
            return False
        logger.info("[加速] 未找到 aria2c，尝试自动下载（仅首次）…")
        try:
            subprocess.run(
                ["python" if os.name == "nt" else "python3", script],
                cwd=_repo_root(), timeout=300, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning("[加速] aria2 自动下载失败: %s", e.__class__.__name__)
            return False
        return self._find_binary() is not None

    def _start_builtin(self):
        binary = self._find_binary()
        if not binary and self._auto_fetch():
            binary = self._find_binary()
        if not binary:
            raise Aria2Error(
                "未找到 aria2c：请先运行 python scripts/fetch_aria2.py 下载，"
                "或安装系统 aria2 后加入 PATH")

        self._port = _free_port()
        self._secret = secrets.token_hex(8)
        # 注意：布尔选项必须 --xxx=true 连写；分开写（"--continue", "true"）
        # 会被 aria2 当成下载 URI 直接退出（Unrecognized URI: true）
        args = tuning.cli_args(binary, self._port, self._secret,
                               self.download_dir, self.cfg,
                               stop_pid=os.getpid())
        # stderr 落盘（backend/data/aria2.log），启动失败时可诊断
        logdir = os.path.join(_repo_root(), "backend", "data")
        os.makedirs(logdir, exist_ok=True)
        self._logf = open(os.path.join(logdir, "aria2.log"), "ab")
        kwargs = {"stdout": self._logf, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._proc = subprocess.Popen(args, **kwargs)
        for _ in range(POLL_TRIES):
            if self._proc.poll() is not None:
                break
            try:
                self._call("aria2.getVersion")
                logger.info("[加速] 内置 aria2c 已启动（端口 %s，%s 档，%s 并发）",
                            self._port, self.cfg.get("profile"),
                            tuning.global_options(self.cfg)["max-concurrent-downloads"])
                return
            except Exception:
                time.sleep(POLL_INTERVAL)
        # 启动失败时把 aria2 自己的报错透出来（多半是某个选项名不对），
        # 否则用户只能看到一句"RPC 无响应"，没法自助排查
        raise Aria2Error("aria2c 启动失败（RPC 无响应）：" + self._tail_log())

    def _tail_log(self) -> str:
        """读 aria2 日志尾部，剥掉空行取最后一行有意义的文本。"""
        path = os.path.join(_repo_root(), "backend", "data", "aria2.log")
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                f.seek(max(0, size - 4096))
                raw = f.read().decode("utf-8", "replace")
        except OSError:
            return "请查看 backend/data/aria2.log"
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return "aria2 无输出，请查看 backend/data/aria2.log"
        return lines[-1][:300]

    def ensure_running(self):
        """保证 RPC 可用；内置模式必要时拉起子进程。"""
        if self.mode == "external":
            self._call("aria2.getVersion")
            return
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._call("aria2.getVersion")
                    return
                except Exception:
                    logger.warning("[加速] 内置 aria2c 失去响应，尝试重启")
            self._start_builtin()

    def ping(self) -> dict:
        """只探测不拉起，供 /status 使用。"""
        try:
            if self.mode == "external":
                ver = self._call("aria2.getVersion")
            else:
                if self._proc is None or self._proc.poll() is not None:
                    return {"online": False, "version": "", "mode": "builtin",
                            "dir": self.download_dir}
                ver = self._call("aria2.getVersion")
            return {"online": True, "version": (ver or {}).get("version", ""),
                    "mode": self.mode, "dir": self.download_dir}
        except Exception:
            return {"online": False, "version": "", "mode": self.mode,
                    "dir": self.download_dir}

    def shutdown(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        try:
            self._logf.close()
        except (OSError, AttributeError):
            pass

    # ---------- JSON-RPC ----------
    def _call(self, method, params=None):
        params = list(params or [])
        secret = self._external_secret if self.mode == "external" else self._secret
        if secret:
            params = ["token:" + secret] + params
        url = self._rpc_url or f"http://127.0.0.1:{self._port}/jsonrpc"
        payload = {"jsonrpc": "2.0", "id": secrets.token_hex(4),
                   "method": method, "params": params}
        try:
            r = self._sess.post(url, json=payload, timeout=RPC_TIMEOUT)
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            raise Aria2Error(f"aria2 RPC 请求失败：{e.__class__.__name__}")
        if "error" in j:
            raise Aria2Error(f"aria2 错误：{j['error'].get('message', '未知')}")
        return j.get("result")

    # ---------- 任务操作 ----------
    def add_download(self, dlink: str, filename: str, save_dir: str,
                     cookie_header: str, referer: str,
                     options: dict | None = None) -> str:
        """推送一个下载任务，返回 aria2 gid。Cookie 只在此处经内存传入。

        options 里的键会覆盖默认值（分段任务用 out / split / header 覆盖）。
        """
        self.ensure_running()
        opts = tuning.task_options(self.cfg)
        opts.update({
            "dir": save_dir,
            "out": filename,
            "header": [
                f"Cookie: {cookie_header}",
                # UA 必须与解析阶段一致，否则 dlink 可能被百度拒绝
                f"User-Agent: {UA}",
                f"Referer: {referer}",
            ],
        })
        for k, v in (options or {}).items():
            if k == "header" and isinstance(v, list):
                opts["header"] = list(opts.get("header") or []) + v
            else:
                opts[k] = v
        gid = self._call("aria2.addUri", [[dlink], opts])
        return gid

    def status(self, gid: str) -> dict:
        res = self._call("aria2.tellStatus",
                         [gid, ["status", "completedLength", "totalLength",
                                "downloadSpeed", "errorMessage", "connections",
                                "files"]])
        return {
            "status": res.get("status", ""),
            "completed": _i(res.get("completedLength")),
            "total": _i(res.get("totalLength")),
            "speed": _i(res.get("downloadSpeed")),
            "error": res.get("errorMessage") or "",
            "connections": _i(res.get("connections")),
        }

    # ---------- 调度器专用：批量查询与动态改参 ----------
    def tell_active(self) -> list:
        try:
            return self._call("aria2.tellActive", [_STATUS_KEYS]) or []
        except Aria2Error:
            return []

    def tell_waiting(self, offset: int = 0, num: int = 100) -> list:
        try:
            return self._call("aria2.tellWaiting", [offset, num, _STATUS_KEYS]) or []
        except Aria2Error:
            return []

    def global_stat(self) -> dict:
        try:
            return self._call("aria2.getGlobalStat") or {}
        except Aria2Error:
            return {}

    def global_options_now(self) -> dict:
        try:
            return self._call("aria2.getGlobalOption") or {}
        except Aria2Error:
            return {}

    def purge(self):
        try:
            self._call("aria2.purgeDownloadResult")
        except Aria2Error:
            pass

    def change_option(self, gid: str, options: dict):
        """热改单任务选项。对 active 任务，aria2 只在**重试/重开连接**时生效，
        因此调度器改 split 前后会走 pause→changeOption→unpause 流程。"""
        if not options:
            return
        self._call("aria2.changeOption", [gid, options])

    def change_global_option(self, options: dict):
        if not options:
            return
        self._call("aria2.changeGlobalOption", [options])

    def set_task_split(self, gid: str, split: int, repause: bool = True) -> bool:
        """把任务的连接数改成 split。需要重新排队才生效，故默认 pause/unpause。

        返回是否成功；失败静默（调度器下个 tick 会再试）。
        """
        try:
            was_active = False
            if repause:
                try:
                    st = self._call("aria2.tellStatus", [gid, ["status"]])
                    was_active = (st or {}).get("status") == "active"
                    if was_active:
                        self._call("aria2.pause", [gid])
                except Aria2Error:
                    pass
            self.change_option(gid, {
                "split": str(split),
                "max-connection-per-server": str(split),
                "min-split-size": self.cfg.get("min_split")
                or tuning.profile(self.cfg["profile"])["min_split"],
            })
            if was_active:
                self._call("aria2.unpause", [gid])
            return True
        except Aria2Error:
            return False

    def pause(self, gid: str):
        self._call("aria2.pause", [gid])

    def resume(self, gid: str):
        self._call("aria2.unpause", [gid])

    def cancel(self, gid: str):
        self._call("aria2.forceRemove", [gid])
