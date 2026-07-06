from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .paths import CONFIG_DIR, NAPCAT_DIR, SETTINGS_FILE


DEFAULT_SETTINGS: dict[str, Any] = {
    "napcat": {
        "http_url": "http://localhost:3000",
        "access_token": "",
        "uin": "",
        "auto_start": True,
        "webui_host": "::",
        "webui_port": 6099,
        "webui_token": "",
    },
    "websocket": {
        "host": "localhost",
        "port": 18082,
        "token": "",
    },
    "monitor": {
        "all_private": False,
        "all_group": False,
        "private_uins": [],
        "group_ids": [],
    },
    "download": {
        "batch_interval": 1,
        "max_concurrent_msgs": 3,
        "timeout": 45,
        "retry_network_errors": 1,
        "enable_md5_dedup": True,
        "md5_cache_file": "downloaded_md5.json",
        "download_images": True,
        "download_videos": True,
        "parse_forward": True,
        "parse_reply": True,
    },
    "app": {
        "auto_start_monitor_on_gui_launch": False,
        "minimize_to_tray": True,
        "video_preview_autoplay": False,
        "log_level": "INFO",
    },
}


def _deep_merge(default: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(default)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _clean_id_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = []
    for value in values:
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return cleaned


def normalize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    settings["monitor"]["private_uins"] = _clean_id_list(settings["monitor"].get("private_uins"))
    settings["monitor"]["group_ids"] = _clean_id_list(settings["monitor"].get("group_ids"))
    settings["websocket"]["port"] = int(settings["websocket"].get("port") or 18082)
    settings["napcat"]["webui_port"] = int(settings["napcat"].get("webui_port") or 6099)
    settings["download"]["batch_interval"] = max(0, int(settings["download"].get("batch_interval") or 0))
    settings["download"]["max_concurrent_msgs"] = max(1, int(settings["download"].get("max_concurrent_msgs") or 1))
    settings["download"]["timeout"] = max(5, int(settings["download"].get("timeout") or 90))
    settings["download"]["retry_network_errors"] = max(0, int(settings["download"].get("retry_network_errors") or 0))
    return settings


def load_settings(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    else:
        current = {}
    settings = normalize_settings(_deep_merge(DEFAULT_SETTINGS, current))
    if not path.exists() or current != settings:
        save_settings(settings, path)
    return settings


def save_settings(settings: dict[str, Any], path: Path = SETTINGS_FILE) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    normalized = normalize_settings(_deep_merge(DEFAULT_SETTINGS, settings))
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")


def apply_settings_to_napcat_webui(settings: dict[str, Any]) -> bool:
    """Write the GUI-managed NapCat WebUI fields into NapCat"s config file."""
    webui_path = NAPCAT_DIR / "napcat" / "config" / "webui.json"
    if not webui_path.exists():
        return False
    try:
        data = json.loads(webui_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    napcat = settings.get("napcat", {})
    data["host"] = napcat.get("webui_host", data.get("host", "::"))
    data["port"] = int(napcat.get("webui_port") or data.get("port", 6099))
    token = napcat.get("webui_token", "")
    if token:
        data["token"] = token
    data["autoLoginAccount"] = napcat.get("uin", data.get("autoLoginAccount", ""))
    webui_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    return True


def find_available_port(preferred=18082, max_attempts=20):
    import socket
    port = preferred
    for _ in range(max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return port


def generate_default_settings():
    import secrets
    ws_port = find_available_port(18082)
    token = secrets.token_urlsafe(18)
    return {
        "napcat": {
            "http_url": "http://localhost:3000",
            "access_token": token,
            "uin": "",
            "auto_start": True,
            "webui_host": "::",
            "webui_port": 6099,
            "webui_token": "",
        },
        "websocket": {
            "host": "localhost",
            "port": ws_port,
            "token": token,
        },
        "monitor": {
            "all_private": False,
            "all_group": False,
            "private_uins": [],
            "group_ids": [],
        },
        "download": {
            "batch_interval": 1,
            "max_concurrent_msgs": 3,
            "timeout": 45,
            "retry_network_errors": 1,
            "enable_md5_dedup": True,
            "md5_cache_file": "downloaded_md5.json",
            "download_images": True,
            "download_videos": True,
            "parse_forward": True,
            "parse_reply": True,
        },
        "app": {
            "auto_start_monitor_on_gui_launch": False,
            "minimize_to_tray": True,
            "video_preview_autoplay": False,
            "log_level": "INFO",
        },
    }


def _replace_named_config(items: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    name = item.get("name")
    kept = [old for old in items if old.get("name") != name]
    kept.append(item)
    return kept


def apply_settings_to_napcat_onebot_network(settings: dict[str, Any]) -> tuple[bool, str]:
    """Write HTTP server and WebSocket client settings into NapCat"s OneBot config."""
    uin = str(settings.get("napcat", {}).get("uin", "")).strip()
    if not uin:
        return False, "请先填写默认登录 QQ。"

    config_path = NAPCAT_DIR / "napcat" / "config" / f"onebot11_{uin}.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    network = data.setdefault("network", {})
    network.setdefault("httpServers", [])
    network.setdefault("httpSseServers", [])
    network.setdefault("httpClients", [])
    network.setdefault("websocketServers", [])
    network.setdefault("websocketClients", [])
    network.setdefault("plugins", [])

    napcat = settings.get("napcat", {})
    websocket = settings.get("websocket", {})
    http_url = napcat.get("http_url", "http://localhost:3000")
    parsed = urlparse(http_url)
    http_host = parsed.hostname or "localhost"
    http_port = parsed.port or (443 if parsed.scheme == "https" else 3000)
    http_token = napcat.get("access_token", "")

    ws_host = websocket.get("host", "localhost") or "localhost"
    ws_port = int(websocket.get("port") or 18082)
    ws_token = websocket.get("token", "")
    ws_url = f"ws://{ws_host}:{ws_port}"
    if ws_token:
        ws_url = f"{ws_url}?access_token={quote(ws_token, safe='')}"

    network["httpServers"] = _replace_named_config(
        network.get("httpServers", []),
        {
            "name": "QQ_Chat HTTP Server",
            "enable": True,
            "host": http_host,
            "port": int(http_port),
            "token": http_token,
            "enableCors": True,
            "debug": False,
        },
    )
    network["websocketClients"] = _replace_named_config(
        network.get("websocketClients", []),
        {
            "name": "QQ_Chat WebSocket Client",
            "enable": True,
            "url": ws_url,
            "token": ws_token,
            "reconnectInterval": 5000,
            "heartInterval": 30000,
            "debug": False,
        },
    )

    data.setdefault("musicSignUrl", "")
    data.setdefault("enableLocalFile2Url", False)
    data.setdefault("parseMultMsg", False)
    data.setdefault("imageDownloadProxy", "")
    data.setdefault(
        "timeout",
        {
            "baseTimeout": 10000,
            "uploadSpeedKBps": 256,
            "downloadSpeedKBps": 256,
            "maxTimeout": 1800000,
        },
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True, f"已写入 {config_path.name}。重启 NapCat 后生效。"
