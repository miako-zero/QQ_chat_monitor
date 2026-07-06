from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .paths import NAPCAT_DIR


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def kill_process_tree(pid: int | str) -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=_creationflags(),
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def list_project_napcat_processes(napcat_dir: Path = NAPCAT_DIR) -> list[dict]:
    if os.name != "nt":
        return []
    root = str(napcat_dir.resolve()).lower()
    ps_root = root.replace("'", "''")
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.CommandLine -and $_.CommandLine.ToLower().Contains("
        + f"'{ps_root}'"
        + ") "
        "} | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=_creationflags(),
            timeout=10,
        )
        text = (result.stdout or "").strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        allowed_names = {"node.exe", "cmd.exe", "conhost.exe"}
        filtered = []
        for item in data:
            if not isinstance(item, dict) or not item.get("ProcessId"):
                continue
            exe = Path(str(item.get("ExecutablePath") or "")).name.lower()
            if exe in allowed_names:
                filtered.append(item)
        return filtered
    except Exception:
        return []


def kill_project_napcat_processes(napcat_dir: Path = NAPCAT_DIR) -> int:
    killed = 0
    current_pid = os.getpid()
    for proc in list_project_napcat_processes(napcat_dir):
        pid = int(proc.get("ProcessId"))
        if pid == current_pid:
            continue
        if kill_process_tree(pid):
            killed += 1
    return killed
