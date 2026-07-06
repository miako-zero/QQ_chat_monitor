#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQ_Chat Monitor")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="只启动监控下载核心，不加载 PyQt6 图形界面。",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动 PyQt6 图形界面。默认行为也是启动图形界面。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.minimal:
        from downloader.realtime_downloader import main as downloader_main

        result = asyncio.run(downloader_main())
        return int(result or 0)

    try:
        from app.main import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name == "PyQt6":
            print("未安装 PyQt6，无法启动图形界面。可以先使用 --minimal 启动极简监控模式。")
            return 2
        raise
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
