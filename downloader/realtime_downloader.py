# -*- coding: utf-8 -*-
"""实时监控 QQ 消息并下载媒体文件"""

import asyncio, websockets, json, os, sys, time, re, hashlib
import shutil, logging, threading, subprocess, socket, atexit, requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, quote, urlparse

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path: sys.path.insert(0, str(BASE_DIR))

from app.core.config import apply_settings_to_napcat_webui, load_settings
from app.core.napcat_process import kill_process_tree
from app.core.process_lock import acquire_monitor_lock, release_monitor_lock

# ----- 配置 -----
SETTINGS = load_settings()
NAPCAT_HTTP_URL = SETTINGS["napcat"]["http_url"].rstrip("/")
ACCESS_TOKEN = SETTINGS["napcat"]["access_token"]
NAPCAT_UIN = SETTINGS["napcat"]["uin"]
NAPCAT_AUTO_START = SETTINGS["napcat"]["auto_start"]
WEBSOCKET_HOST = SETTINGS["websocket"]["host"]
WEBSOCKET_PORT = SETTINGS["websocket"]["port"]
WEBSOCKET_TOKEN = SETTINGS["websocket"]["token"]
MONITOR_ALL_PRIVATE = SETTINGS["monitor"]["all_private"]
MONITOR_ALL_GROUP = SETTINGS["monitor"]["all_group"]
MONITOR_PRIVATE_UINS = SETTINGS["monitor"]["private_uins"]
MONITOR_GROUP_IDS = SETTINGS["monitor"]["group_ids"]
BATCH_INTERVAL = SETTINGS["download"]["batch_interval"]
MAX_CONCURRENT_MSGS = SETTINGS["download"]["max_concurrent_msgs"]
DOWNLOAD_TIMEOUT = SETTINGS["download"]["timeout"]
RETRY_NETWORK_ERRORS = SETTINGS["download"]["retry_network_errors"]
ENABLE_MD5_DEDUP = SETTINGS["download"]["enable_md5_dedup"]
MD5_CACHE_FILE = SETTINGS["download"]["md5_cache_file"]
DOWNLOAD_IMAGES = SETTINGS["download"]["download_images"]
DOWNLOAD_VIDEOS = SETTINGS["download"]["download_videos"]
PARSE_FORWARD = SETTINGS["download"]["parse_forward"]
PARSE_REPLY = SETTINGS["download"]["parse_reply"]

API_TIMEOUT = max(5, min(8, int(DOWNLOAD_TIMEOUT)))
MEDIA_INFO_TIMEOUT = max(4, min(6, int(DOWNLOAD_TIMEOUT)))
DOWNLOAD_CONNECT_TIMEOUT = 5
DOWNLOAD_READ_TIMEOUT = max(15, min(30, int(DOWNLOAD_TIMEOUT)))

DATA_ROOT = BASE_DIR / "ALL_Fold"
LOG_DIR = DATA_ROOT / "logs"; ERROR_DIR = DATA_ROOT / "errors"
LOG_DIR.mkdir(parents=True, exist_ok=True); ERROR_DIR.mkdir(parents=True, exist_ok=True)

log_filename = LOG_DIR / f"realtime_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(level=getattr(logging, SETTINGS["app"].get("log_level", "INFO").upper(), logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_filename, encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

error_handler = logging.FileHandler(ERROR_DIR / f"error_{datetime.now().strftime('%Y%m%d')}.log", encoding='utf-8')
error_handler.setLevel(logging.WARNING)
error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(error_handler)

msg_cache = {}; forward_cache = {}; md5_cache = {}
cache_lock = threading.Lock(); pending_msg_ids = set(); pending_lock = threading.Lock()
semaphore = asyncio.Semaphore(MAX_CONCURRENT_MSGS)

def cache_path_to_abs(value):
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path

def path_for_cache(value):
    path = Path(value)
    try:
        return str(path.resolve().relative_to(BASE_DIR.resolve()))
    except Exception:
        return str(path)

# ----- 进程锁 -----
_lock_acquired = False

def acquire_lock():
    global _lock_acquired
    ok, pid = acquire_monitor_lock()
    if not ok:
        message = f"监控程序已经在运行，PID: {pid or '未知'}。请先关闭已有监控后再启动。"
        logger.error(message)
        print(f"[ERROR] {message}")
        sys.exit(1)
    _lock_acquired = True
    return True

def release_lock():
    global _lock_acquired
    if _lock_acquired:
        release_monitor_lock()
        _lock_acquired = False

atexit.register(release_lock)


# ----- MD5 缓存 -----
def load_md5_cache():
    global md5_cache
    cache_path = BASE_DIR / MD5_CACHE_FILE
    if ENABLE_MD5_DEDUP and cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8-sig') as f:
                with cache_lock: md5_cache = json.load(f)
            logger.info(f"加载了 {len(md5_cache)} 条 MD5 缓存记录")
        except Exception as e: logger.warning(f"加载 MD5 缓存失败: {e}")

def save_md5_cache():
    if ENABLE_MD5_DEDUP:
        cache_path = BASE_DIR / MD5_CACHE_FILE
        with cache_lock: tmp = md5_cache.copy()
        with open(cache_path, 'w', encoding='utf-8') as f: json.dump(tmp, f, indent=2)

# ----- NapCat API -----
def _api_headers():
    headers = {'Content-Type': 'application/json'}
    if ACCESS_TOKEN:
        headers['Authorization'] = f'Bearer {ACCESS_TOKEN}'
    return headers

def _api_post(endpoint, payload, timeout=API_TIMEOUT):
    resp = requests.post(
        f"{NAPCAT_HTTP_URL}/{endpoint.lstrip('/')}",
        json=payload,
        headers=_api_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()

def get_message_details(msg_id):
    msg_id = str(msg_id)
    with cache_lock:
        if msg_id in msg_cache: return msg_cache[msg_id]
    try:
        data = _api_post("get_msg", {'message_id': msg_id}, API_TIMEOUT)
        if data.get('status') == 'ok':
            with cache_lock: msg_cache[msg_id] = data
            return data
        else:
            logger.warning(f"获取消息 {msg_id} 失败: {data.get('wording', '未知错误')}")
            return None
    except Exception as e:
        logger.error(f"请求普通消息异常 {msg_id}: {e}")
        return None

def get_forward_msg(msg_id):
    msg_id = str(msg_id)
    with cache_lock:
        if msg_id in forward_cache: return forward_cache[msg_id]
    try:
        data = _api_post("get_forward_msg", {'message_id': msg_id}, API_TIMEOUT)
        if data.get('status') == 'ok':
            with cache_lock: forward_cache[msg_id] = data
            return data
        else:
            logger.warning(f"获取合并转发 {msg_id} 失败: {data.get('wording', '未知错误')}")
            return None
    except Exception as e:
        logger.error(f"请求合并转发异常 {msg_id}: {e}")
        return None

# ----- 文件下载 -----
executor = ThreadPoolExecutor(max_workers=5)

def download_file(url, save_path, overwrite=False):
    if os.path.exists(save_path):
        if overwrite: os.remove(save_path)
        else: return Path(save_path)
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = str(save_path) + '.tmp'
    headers = {}
    if ACCESS_TOKEN: headers['Authorization'] = f'Bearer {ACCESS_TOKEN}'
    for attempt in range(RETRY_NETWORK_ERRORS + 1):
        try:
            r = requests.get(url, headers=headers, timeout=(DOWNLOAD_CONNECT_TIMEOUT, DOWNLOAD_READ_TIMEOUT), stream=True)
            r.raise_for_status()
            hasher = hashlib.md5()
            with open(temp_path, 'wb') as tf:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        tf.write(chunk)
                        hasher.update(chunk)
            file_md5 = hasher.hexdigest()
            if ENABLE_MD5_DEDUP:
                with cache_lock:
                    if file_md5 in md5_cache:
                        existing_path = cache_path_to_abs(md5_cache[file_md5])
                        if existing_path.exists():
                            logger.info(f"跳过重复内容 (已存在: {existing_path})")
                            os.remove(temp_path); return existing_path
                        logger.warning(f"MD5 缓存路径不存在，重新保存: {existing_path}")
                        md5_cache.pop(file_md5, None)
            os.replace(temp_path, save_path)
            logger.info(f"下载成功: {save_path} (MD5: {file_md5[:8]}...)")
            if ENABLE_MD5_DEDUP:
                with cache_lock: md5_cache[file_md5] = path_for_cache(save_path)
                save_md5_cache()
            return save_path
        except Exception as e:
            if os.path.exists(temp_path): os.remove(temp_path)
            if attempt < RETRY_NETWORK_ERRORS:
                logger.warning(f"下载失败，准备重试 {attempt+1}/{RETRY_NETWORK_ERRORS}: {str(url)[:80]}... : {e}")
                time.sleep(1)
            else:
                logger.error(f"下载失败 {str(url)[:80]}... : {e}")
                return False
    return False
# chat.json formatting
def build_chat_entry(msg_id, api_data):
    import time as tm
    entry = dict(id=str(msg_id), seq=str(msg_id), timestamp=0, time='',
        sender=dict(uid='', uin='', name=''),
        type='type_1', message_type='', group_id='',
        content=dict(text='', html='', elements=[], resources=[], mentions=[]),
        recalled=False, system=False)
    if not api_data or not isinstance(api_data, dict):
        return entry
    data = api_data.get('data', {}) or api_data
    ts = data.get('time') or api_data.get('time')
    if ts and isinstance(ts, (int, float)):
        import time as _tm
        entry['timestamp'] = int(ts)
        entry['time'] = _tm.strftime('%Y-%m-%d %H:%M:%S', _tm.localtime(int(ts)))
    sender = data.get('sender', {}) or {}
    entry['sender']['uin'] = str(sender.get('user_id', ''))
    entry['sender']['name'] = sender.get('nickname', '')
    entry['message_type'] = data.get('message_type', '')
    entry['group_id'] = str(data.get('group_id', ''))
    msg_arr = data.get('message', [])
    if not msg_arr:
        msg_arr = api_data.get('message', [])
    text_parts = []
    elements = []
    for seg in msg_arr:
        if not isinstance(seg, dict):
            continue
        st = seg.get('type', '')
        sd = seg.get('data', {})
        if st == 'text':
            t = sd.get('text', '')
            if t:
                text_parts.append(t)
                elements.append(dict(type='text', data=dict(text=t)))
        elif st in ('image', 'video'):
            fn = sd.get('file', '') or sd.get('file_id', '') or ''
            media_data = dict(
                filename=fn,
                size=str(sd.get('file_size', '') or sd.get('size', '')),
                url=sd.get('url', '') or ''
            )
            if sd.get('local_path'):
                media_data['local_path'] = sd.get('local_path')
            elements.append(dict(type=st, data=media_data))
        elif st == 'reply':
            elements.append(dict(type='reply', data=dict(id=str(sd.get('id', '')))))
        elif st == 'forward':
            elements.append(dict(type='forward', data=dict(
                id=str(sd.get('id', '')), content=sd.get('content', []), text=sd.get('desc', '[合并转发]')
            )))
        elif st == 'at':
            elements.append(dict(type='at', data=dict(qq=str(sd.get('qq', '')), text=sd.get('text', ''))))
        elif st == 'face':
            elements.append(dict(type='face', data=dict(id=str(sd.get('id', '')))))
        elif st == 'file':
            elements.append(dict(type='file', data=dict(name=sd.get('name', ''), size=str(sd.get('size', '')), url=sd.get('url', ''))))
        elif st == 'node':
            content = sd.get('content', [])
            if not content and isinstance(sd.get('message'), list):
                content = sd.get('message', [])
            elements.append(dict(type='forward', data=dict(
                id=str(sd.get('id', '')), content=content, text=sd.get('desc', '[合并转发]')
            )))
        else:
            elements.append(dict(type=st, data={k: str(v) for k, v in sd.items()}))
    entry['content']['text'] = ' '.join(text_parts) if text_parts else ''
    entry['content']['elements'] = elements
    return entry

def _message_payload(api_data):
    if not isinstance(api_data, dict):
        return {}
    data = api_data.get('data', {})
    return data if isinstance(data, dict) else api_data

def _message_segments(api_data):
    if isinstance(api_data, list):
        return api_data
    data = _message_payload(api_data)
    message = data.get('message', [])
    if not message:
        message = data.get('content', [])
    if not message:
        message = data.get('messages', [])
    if not message:
        message = data.get('nodes', [])
    if not message:
        message = api_data.get('message', []) if isinstance(api_data, dict) else []
    return message if isinstance(message, list) else []

def _safe_filename(value, fallback):
    name = Path(str(value or '')).name.strip()
    if not name:
        name = fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip(' .')
    return name or fallback

def _suffix_for_media(media_type, filename, url):
    for source in (filename, url):
        suffix = Path(urlparse(str(source or '')).path).suffix.lower()
        if suffix:
            return suffix
    return '.mp4' if media_type == 'video' else '.jpg'

def _unique_media_path(media_dir, media_type, filename, msg_id, url):
    suffix = _suffix_for_media(media_type, filename, url)
    fallback = f'{media_type}_{msg_id}{suffix}'
    safe = _safe_filename(filename, fallback)
    if not Path(safe).suffix:
        safe += suffix
    short = hashlib.md5(f'{msg_id}|{url}|{safe}'.encode('utf-8', errors='ignore')).hexdigest()[:8]
    stem = Path(safe).stem[:80] or media_type
    final_name = f'{msg_id}_{short}_{stem}{Path(safe).suffix}'
    return Path(media_dir) / media_type / final_name

def _normalize_forward_messages(forward_data):
    data = _message_payload(forward_data)
    candidates = data.get('messages') or data.get('message') or data.get('nodes') or []
    if isinstance(candidates, dict):
        candidates = candidates.get('messages') or candidates.get('nodes') or []
    return candidates if isinstance(candidates, list) else []

def _raw_message(api_data):
    if not isinstance(api_data, dict):
        return ""
    data = _message_payload(api_data)
    return str(data.get('raw_message') or api_data.get('raw_message') or "")

def _extract_cq_file(raw_message, media_type):
    if not raw_message:
        return ""
    match = re.search(r'\[CQ:' + re.escape(media_type) + r',file=([^,\]]+)', raw_message)
    return match.group(1) if match else ""

def _media_url_from_api(media_type, file_id):
    if not file_id:
        return ""
    endpoint = "get_image" if media_type == "image" else "get_file"
    for key in ("file_id", "file"):
        try:
            data = _api_post(endpoint, {key: file_id}, MEDIA_INFO_TIMEOUT)
        except Exception as e:
            logger.debug(f"{endpoint} failed for {file_id}: {e}")
            continue
        if data.get("status") == "ok":
            payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
            url = payload.get("url") or payload.get("file") or payload.get("path")
            if url:
                return str(url)
    return f"{NAPCAT_HTTP_URL}/download?file={quote(str(file_id), safe='')}"

def _unwrap_forward_node(node):
    if not isinstance(node, dict):
        return None
    node_data = node.get("data", node)
    if not isinstance(node_data, dict):
        return None
    if "content" in node_data and "message" not in node_data:
        node_data = dict(node_data)
        node_data["message"] = node_data.get("content") or []
    return node_data

def _object_folder_name(api_data):
    data = _message_payload(api_data)
    sender = data.get("sender", {}) if isinstance(data.get("sender", {}), dict) else {}
    message_type = str(data.get("message_type") or "")
    if message_type == "group" and data.get("group_id"):
        base = f"group_{data.get('group_id')}"
    else:
        uin = sender.get("user_id") or data.get("user_id") or "unknown"
        base = f"private_{uin}"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(base)).strip(" .")
    return base[:120] or "unknown"

def media_root_for_message(date_dir, api_data):
    return Path(date_dir) / _object_folder_name(api_data)

def process_message_node(api_data, media_root, depth=0, seen_forward_ids=None, seen_reply_ids=None):
    if seen_forward_ids is None:
        seen_forward_ids = set()
    if seen_reply_ids is None:
        seen_reply_ids = set()
    if depth > 8:
        logger.warning("forward parse depth limit reached")
        return
    if isinstance(api_data, list):
        for item in api_data:
            process_message_node(item, media_root, depth, seen_forward_ids, seen_reply_ids)
        return
    if not isinstance(api_data, dict):
        return
    data = _message_payload(api_data)
    msg_id = str(data.get('message_id') or data.get('id') or api_data.get('message_id') or int(time.time() * 1000))
    media_root = Path(media_root)
    raw_message = _raw_message(api_data)
    for index, seg in enumerate(_message_segments(api_data)):
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get('type', '')
        seg_data = seg.get('data', {}) if isinstance(seg.get('data', {}), dict) else {}
        if seg_type in ('image', 'video') and ((seg_type == 'image' and DOWNLOAD_IMAGES) or (seg_type == 'video' and DOWNLOAD_VIDEOS)):
            url = seg_data.get('url') or seg_data.get('file_url') or seg_data.get('download_url')
            file_id = seg_data.get('file') or seg_data.get('file_id') or seg_data.get('filename') or _extract_cq_file(raw_message, seg_type)
            if not url and file_id:
                url = _media_url_from_api(seg_type, file_id)
            if not url:
                logger.warning(f"skip {seg_type}: no download url, file_id={file_id or ''}")
                continue
            if str(url).startswith("/"):
                url = f"{NAPCAT_HTTP_URL}{url}"
            original_name = file_id or seg_data.get('file') or seg_data.get('filename') or ''
            target = _unique_media_path(media_root, seg_type, original_name, f'{msg_id}_{index}', url)
            actual_path = download_file(url, target)
            if actual_path:
                actual_path = Path(actual_path)
                seg_data['file'] = actual_path.name
                seg_data['filename'] = actual_path.name
                try:
                    seg_data['size'] = str(actual_path.stat().st_size)
                except OSError:
                    pass
                try:
                    seg_data['local_path'] = path_for_cache(actual_path)
                except Exception:
                    seg_data['local_path'] = str(actual_path)
        elif seg_type == 'reply' and PARSE_REPLY:
            reply_id = seg_data.get('id')
            if reply_id and str(reply_id) not in seen_reply_ids:
                seen_reply_ids.add(str(reply_id))
                reply_data = get_message_details(reply_id)
                if reply_data:
                    process_message_node(reply_data, media_root, depth + 1, seen_forward_ids, seen_reply_ids)
        elif seg_type == 'node' and PARSE_FORWARD:
            content = seg_data.get('content') or seg.get('content') or seg_data.get('message')
            if content:
                process_message_node(content, media_root, depth + 1, seen_forward_ids, seen_reply_ids)
                children = []
                for node in content if isinstance(content, list) else [content]:
                    node_data = _unwrap_forward_node(node)
                    if isinstance(node_data, dict):
                        children.append(build_chat_entry(node_data.get('message_id') or node_data.get('id') or msg_id, node_data))
                if children:
                    seg_data['content'] = children
        elif seg_type == 'forward' and PARSE_FORWARD:
            content = seg_data.get('content')
            if not isinstance(content, list) or not content:
                forward_id = seg_data.get('id')
                if not forward_id or str(forward_id) in seen_forward_ids:
                    continue
                seen_forward_ids.add(str(forward_id))
                forward_data = get_forward_msg(forward_id)
                if not forward_data:
                    continue
                content = _normalize_forward_messages(forward_data)
            children = []
            for node in content:
                node_data = _unwrap_forward_node(node)
                if isinstance(node_data, dict):
                    process_message_node(node_data, media_root, depth + 1, seen_forward_ids, seen_reply_ids)
                    children.append(build_chat_entry(node_data.get('message_id') or node_data.get('id') or msg_id, node_data))
            if children:
                seg_data['content'] = children
        elif isinstance(seg, dict) and "message" in seg:
            process_message_node(seg, media_root, depth + 1, seen_forward_ids, seen_reply_ids)

def process_one_message(msg_id, date_dir):
    start_time = __import__('datetime').datetime.now()
    logger.info(f'开始处理消息 {msg_id} 时间={start_time}')
    try:
        full = get_message_details(msg_id)
        if full:
            media_root = media_root_for_message(date_dir, full)
            media_root.mkdir(parents=True, exist_ok=True)
            process_message_node(full, media_root)
            logger.info(f'消息 {msg_id} 处理成功')
            return True, None, full
        else:
            logger.warning(f'消息 {msg_id} 获取失败')
            return False, '消息不存在', None
    except Exception as e:
        logger.error(f'消息 {msg_id} 处理异常: {e}')
        return False, str(e), None

# ----- 批量下载 -----
batch_running = False
batch_timer_running = False

async def schedule_batch_download():
    global batch_timer_running
    if batch_timer_running or batch_running: return
    batch_timer_running = True
    try:
        if BATCH_INTERVAL > 0: await asyncio.sleep(BATCH_INTERVAL)
        await batch_download()
    finally:
        batch_timer_running = False

async def batch_download():
    global batch_running
    if batch_running: return
    batch_running = True
    try:
        with pending_lock:
            if not pending_msg_ids:
                batch_running = False
                return
            msg_ids = list(pending_msg_ids)
            pending_msg_ids.clear()
        logger.info(f"批量处理开始，共 {len(msg_ids)} 条消息")
        today = datetime.now().strftime('%Y-%m-%d')
        date_dir = DATA_ROOT / today
        record_file = date_dir / 'chat.json'
        date_dir.mkdir(parents=True, exist_ok=True)
        records = []
        if record_file.exists():
            try:
                with open(record_file, 'r', encoding='utf-8') as f:
                    records = json.load(f)
            except:
                records = []
        records = _migrate_records(records)
        
        async def process_one(msg_id):
            async with semaphore:
                loop = asyncio.get_running_loop()
                success, error, api_data = await loop.run_in_executor(None, process_one_message, msg_id, str(date_dir))
                return msg_id, success, error, api_data
        
        tasks = [process_one(mid) for mid in msg_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_msgs = []
        for res in results:
            if isinstance(res, Exception):
                logger.error(f'任务异常: {res}')
                continue
            msg_id, success, error, api_data = res
            if success:
                entry = build_chat_entry(msg_id, api_data)
                success_msgs.append(entry)
            else:
                logger.error(f'消息 {msg_id} 最终失败: {error}')
        if success_msgs:
            records.extend(success_msgs)
            with open(record_file, 'w', encoding='utf-8') as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            logger.info(f'记录更新完成，共 {len(success_msgs)} 条成功')
        logger.info('批量处理完成')
    finally:
        batch_running = False
        with pending_lock:
            if pending_msg_ids:
                logger.info('发现积压消息，立即启动新一轮批量处理')
                asyncio.create_task(schedule_batch_download())

# ----- WebSocket 处理 -----
def extract_websocket_token(websocket):
    request = getattr(websocket, 'request', None)
    headers = getattr(request, 'headers', None) or getattr(websocket, 'request_headers', {}) or {}
    auth = headers.get('Authorization', '') if hasattr(headers, 'get') else ''
    if auth.startswith('Bearer '): return auth[7:]
    path = getattr(request, 'path', None) or getattr(websocket, 'path', '') or ''
    query = parse_qs(urlparse(path).query)
    token_values = query.get('token') or query.get('access_token') or []
    return token_values[0] if token_values else ''

async def handle_websocket(websocket):
    if WEBSOCKET_TOKEN:
        incoming_token = extract_websocket_token(websocket)
        if incoming_token != WEBSOCKET_TOKEN:
            logger.warning('WebSocket Token 校验失败，已拒绝连接')
            await websocket.close(code=1008, reason='invalid token')
            return
    logger.info('WebSocket 连接已建立')
    try:
        async for raw_message in websocket:
            try:
                event = json.loads(raw_message)
                if event.get('post_type') != 'message': continue
                msg_type = event.get('message_type')
                user_id = str(event.get('user_id', ''))
                group_id = str(event.get('group_id', ''))
                if msg_type == 'private':
                    if not MONITOR_ALL_PRIVATE and user_id not in MONITOR_PRIVATE_UINS: continue
                elif msg_type == 'group':
                    if not MONITOR_ALL_GROUP and group_id not in MONITOR_GROUP_IDS: continue
                else: continue
                message_id = event.get('message_id')
                if not message_id: continue
                with pending_lock:
                    if message_id not in pending_msg_ids:
                        pending_msg_ids.add(message_id)
                        logger.debug(f'添加消息 ID {message_id} 到队列')
                if not batch_running and not batch_timer_running:
                    asyncio.create_task(schedule_batch_download())
            except Exception as e:
                logger.error(f'处理 WebSocket 消息时出错: {e}')
    except websockets.exceptions.ConnectionClosed:
        logger.warning('WebSocket 连接关闭')
    except Exception as e:
        logger.error(f'WebSocket 循环异常: {e}')

# ----- NapCat 管理 -----
napcat_process = None

def find_napcat_bat():
    node_bat = BASE_DIR / 'NapCat.Shell.Windows.Node' / 'napcat.bat'
    if node_bat.exists(): return node_bat
    base = BASE_DIR / 'NapCat.Shell.Windows.OneKey'
    for child in base.glob('NapCat.*.Shell'):
        boot_bat = child / 'bootmain' / 'napcat.bat'
        if boot_bat.exists(): return boot_bat
    fallback = base / 'bootmain' / 'napcat.bat'
    if fallback.exists(): return fallback
    launcher = BASE_DIR / 'NapCat.Shell.Windows.Node' / 'napcat' / 'launcher-user.bat'
    if launcher.exists(): return launcher
    return None

def get_listening_process(port):
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=5,
        )
        pid = None
        suffix = f":{int(port)}"
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                local_addr = parts[1].strip()
                if local_addr.endswith(suffix):
                    pid = parts[-1]
                    break
        if not pid:
            return None
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\"; "
                f"if($p){{[pscustomobject]@{{Pid={pid};Path=$p.ExecutablePath;CommandLine=$p.CommandLine}} | ConvertTo-Json -Compress}}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=10,
        )
        text = (result.stdout or "").strip()
        return json.loads(text) if text else {"Pid": pid, "Path": "", "CommandLine": ""}
    except Exception:
        return None

def process_belongs_to_project(info):
    if not info:
        return False
    root = str(BASE_DIR).lower()
    napcat_root = str((BASE_DIR / "NapCat.Shell.Windows.Node")).lower()
    text = f"{info.get('Path', '')} {info.get('CommandLine', '')}".lower()
    return root in text or napcat_root in text

def start_napcat():
    global napcat_process
    napcat_bat = find_napcat_bat()
    if not napcat_bat:
        logger.error('NapCat 启动路径不存在')
        return
    try:
        show_napcat_console = not NAPCAT_UIN
        if show_napcat_console:
            logger.warning('首次使用或未填写默认 QQ：NapCat 窗口将保持可见，请在其中完成扫码登录。')
            napcat_process = subprocess.Popen([str(napcat_bat)], cwd=str(napcat_bat.parent))
        else:
            napcat_process = subprocess.Popen(
                [str(napcat_bat)],
                cwd=str(napcat_bat.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        logger.info(f'NapCat 已启动 (PID: {napcat_process.pid})')
    except Exception as e:
        logger.error(f'NapCat 启动失败: {e}')

def stop_napcat():
    global napcat_process
    if napcat_process and napcat_process.poll() is None:
        pid = napcat_process.pid
        if kill_process_tree(pid):
            logger.info(f'NapCat process tree stopped (PID: {pid})')
            return
        napcat_process.terminate()
        try:
            napcat_process.wait(timeout=5)
            logger.info('NapCat 已停止')
        except subprocess.TimeoutExpired:
            napcat_process.kill()
            logger.warning('NapCat 进程被强制终止')

# ----- 主函数 -----
async def main():
    acquire_lock()
    load_md5_cache()
    try:
        apply_settings_to_napcat_webui(SETTINGS)
    except Exception:
        logger.warning('写入 NapCat WebUI 配置失败，继续启动..')
    try:
        from app.core.config import apply_settings_to_napcat_onebot_network
        ok, msg = apply_settings_to_napcat_onebot_network(SETTINGS)
        if ok: logger.info(f'OneBot 网络配置: {msg}')
    except Exception:
        logger.warning('写入 OneBot 网络配置失败，继续启动..')
    parsed_http = urlparse(NAPCAT_HTTP_URL)
    http_host = parsed_http.hostname or '127.0.0.1'
    http_port = parsed_http.port or (443 if parsed_http.scheme == 'https' else 80)
    should_start_napcat = NAPCAT_AUTO_START
    existing_http_owner = get_listening_process(http_port)
    if existing_http_owner:
        if process_belongs_to_project(existing_http_owner):
            logger.info(f"NapCat HTTP 端口 {http_port} 已由当前项目占用，复用现有 NapCat (PID: {existing_http_owner.get('Pid')})。")
            should_start_napcat = False
        else:
            logger.error(f"NapCat HTTP 端口 {http_port} 已被其它程序占用，当前项目不能继续监控。占用 PID: {existing_http_owner.get('Pid')}，路径: {existing_http_owner.get('Path')}")
            logger.error('请先关闭其它 QQ_Chat/NapCat，或在图形界面里改成未占用的 HTTP 端口后重新写入 NapCat 配置。')
            return 1
    logger.info('正在启动 WebSocket 服务端...')
    try:
        ws_server = await websockets.serve(handle_websocket, WEBSOCKET_HOST, WEBSOCKET_PORT)
    except OSError as e:
        logger.error(f"WebSocket 端口 {WEBSOCKET_PORT} 启动失败，可能已有监控程序正在运行: {e}")
        return 1
    logger.info(f'WebSocket 服务端已启动，监听地址: ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}')
    if should_start_napcat:
        start_napcat()
    else:
        logger.info('已跳过自动启动 NapCat')
    logger.info('等待 NapCat HTTP 服务启动...')
    for i in range(30):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((http_host, http_port))
        sock.close()
        if result == 0:
            logger.info('NapCat HTTP 服务端口已就绪')
            break
        await asyncio.sleep(1)
    else:
        logger.warning('HTTP 服务端口未能在 30 秒内就绪，继续尝试..')
    await asyncio.Future()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('收到中断信号，正在退出..')
    finally:
        stop_napcat()

# chat.json 旧格式迁移
def _migrate_records(records):
    migrated = []
    for rec in records:
        if isinstance(rec, dict) and 'id' in rec:
            migrated.append(rec); continue
        if isinstance(rec, dict) and 'message_id' in rec:
            migrated.append(dict(
                id=str(rec['message_id']), seq=str(rec['message_id']),
                timestamp=0, time=rec.get('time', ''),
                sender=dict(uid='', uin='', name=''),
                type='type_1', message_type='', group_id='',
                content=dict(text='[旧格式记录]', html='', elements=[], resources=[], mentions=[]),
                recalled=False, system=False
            ))
        else:
            migrated.append(rec)
    return migrated
