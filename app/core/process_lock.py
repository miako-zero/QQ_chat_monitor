# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from app.core.paths import ROOT_DIR


LOCK_FILE = ROOT_DIR / ".monitor.lock"


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return bool(ok) and exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def active_lock_pid() -> int | None:
    if not LOCK_FILE.exists():
        return None
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return pid if is_pid_running(pid) else None


def remove_stale_lock() -> None:
    if LOCK_FILE.exists() and active_lock_pid() is None:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def acquire_monitor_lock() -> tuple[bool, int | None]:
    remove_stale_lock()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(LOCK_FILE), flags)
    except FileExistsError:
        return False, active_lock_pid()
    except OSError:
        return False, active_lock_pid()

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True, os.getpid()


def release_monitor_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass

