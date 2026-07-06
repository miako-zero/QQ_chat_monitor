#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容旧入口；实际监控核心已迁移到 downloader/realtime_downloader.py。"""

import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from downloader.realtime_downloader import main


if __name__ == "__main__":
    asyncio.run(main())
