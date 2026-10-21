#!/usr/bin/env python3
"""仓库根目录快捷入口：转发到 backend/source_audit.py（保持 scripts/ 统一用法）。"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

runpy.run_path(os.path.join(BACKEND, "source_audit.py"), run_name="__main__")
