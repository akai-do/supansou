"""下载 aria2c 二进制到 tools/ 目录（GitHub 发布分发方案，仓库不含二进制）。

支持平台：
  Windows x64  → aria2 官方 release zip
  Linux x64    → q3aql/aria2-static-builds 静态编译
  Linux arm64  → 同上（资源缺失时给出手动指引）
  macOS        → 提示 brew install aria2（PATH 回退天然支持）

用法：python scripts/fetch_aria2.py
加速模块首次启动若缺二进制会自动调用本脚本（ACCEL_NO_AUTO_FETCH=1 可关闭）。
"""
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = os.path.join(REPO_ROOT, "tools")

ARIA2_VERSION = "1.37.0"
URLS = {
    ("Windows", "x64"): ("https://github.com/aria2/aria2/releases/download/"
                         f"release-{ARIA2_VERSION}/"
                         f"aria2-{ARIA2_VERSION}-win-64bit-build1.zip", "zip"),
    ("Linux", "x64"): ("https://github.com/q3aql/aria2-static-builds/releases/"
                       f"download/v{ARIA2_VERSION}/"
                       f"aria2-{ARIA2_VERSION}-linux-gnu-64bit-build1.tar.bz2",
                       "tar"),
    ("Linux", "arm64"): ("https://github.com/q3aql/aria2-static-builds/releases/"
                         f"download/v{ARIA2_VERSION}/"
                         f"aria2-{ARIA2_VERSION}-linux-gnu-arm64-build1.tar.bz2",
                         "tar"),
}


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64"):
        return "x64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def _download(url: str, dst: str):
    print(f"下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "DuPanSou-Archive"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dst, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  {got / 1e6:.1f}/{total / 1e6:.1f} MB", end="", flush=True)
        print()


def main() -> int:
    system = platform.system()  # Windows / Linux / Darwin
    arch = _arch()

    if system == "Darwin":
        print("macOS 请直接安装系统 aria2（加速模块会自动从 PATH 找到）：")
        print("  brew install aria2")
        return 1

    key = (system, arch)
    if key not in URLS:
        print(f"暂无 {system}/{arch} 预编译资源，请手动安装 aria2 并加入 PATH。")
        return 1

    url, kind = URLS[key]
    os.makedirs(TOOLS, exist_ok=True)
    exe_name = "aria2c.exe" if system == "Windows" else "aria2c"
    target = os.path.join(TOOLS, exe_name)
    if os.path.isfile(target):
        print(f"已存在 {target}，跳过（如需更新请先删除）")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, os.path.basename(url))
        try:
            _download(url, archive)
        except OSError as e:
            print(f"下载失败：{e}\n请检查网络，或到 {url} 手动下载，"
                  f"将 aria2c 解压到 {target}")
            return 1

        if kind == "zip":
            with zipfile.ZipFile(archive) as z:
                member = next(n for n in z.namelist()
                              if n.endswith("aria2c.exe"))
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive, "r:bz2") as t:
                member = next(n for n in t.getnames()
                              if n.endswith("/aria2c"))
                t.extract(member, tmp)
                extracted = os.path.join(tmp, member)
                shutil.move(extracted, target)
            mode = os.stat(target).st_mode
            os.chmod(target, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"aria2c {ARIA2_VERSION} 已就绪：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
