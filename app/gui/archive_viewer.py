# -*- coding: utf-8 -*-
"""聊天记录归档浏览界面"""
import hashlib, json, os, time as time_module
from collections import defaultdict
from pathlib import Path
from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QCursor, QImageReader, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QScrollArea,
    QLabel, QPushButton, QFrame, QComboBox, QDialog, QSlider
)

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT_DIR / "ALL_Fold"
MD5_CACHE_FILE = ROOT_DIR / "downloaded_md5.json"
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".suf", ".mp4", ".avi", ".mov", ".mkv"}
_MEDIA_INDEX = None
_HASH_CACHE = {}
_THUMB_CACHE = {}

def load_chat(date_str):
    p = DATA_ROOT / date_str / "chat.json"
    if not p.exists(): return []
    try:
        with open(p, 'r', encoding='utf-8') as f: return json.load(f)
    except: return []

def get_media_dir(date_str):
    return DATA_ROOT / date_str

def load_md5_cache():
    if not MD5_CACHE_FILE.exists():
        return {}
    try:
        with open(MD5_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def clear_media_index():
    global _MEDIA_INDEX
    _MEDIA_INDEX = None

def _is_media_file(path):
    if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
        return False
    parts = {part.lower() for part in path.parts}
    return "media" in parts or "image" in parts or "video" in parts

def get_media_index():
    global _MEDIA_INDEX
    if _MEDIA_INDEX is not None:
        return _MEDIA_INDEX

    name_map = defaultdict(list)
    size_ext_map = defaultdict(list)
    files_by_ext = defaultdict(list)
    md5_map = {}

    for md5, value in load_md5_cache().items():
        fp = Path(value)
        if not fp.is_absolute():
            fp = ROOT_DIR / fp
        if fp.exists():
            md5_map[str(md5).lower()] = fp

    if DATA_ROOT.exists():
        for fp in DATA_ROOT.rglob("*"):
            try:
                if not _is_media_file(fp):
                    continue
                suffix = fp.suffix.lower()
                stat = fp.stat()
                name_map[fp.name.lower()].append(fp)
                size_ext_map[(str(stat.st_size), suffix)].append(fp)
                files_by_ext[suffix].append(fp)
            except OSError:
                pass

    _MEDIA_INDEX = {
        "name": name_map,
        "size_ext": size_ext_map,
        "files_by_ext": files_by_ext,
        "md5": md5_map,
    }
    return _MEDIA_INDEX

def _under_dir(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False

def _prefer_same_date(paths, media_dir):
    same_date = [p for p in paths if _under_dir(p, media_dir)]
    return same_date or paths

def _candidate_path(value):
    fp = Path(str(value))
    if not fp.is_absolute():
        fp = ROOT_DIR / fp
    return fp

def _looks_like_md5(text):
    return len(text) == 32 and all(ch in "0123456789abcdef" for ch in text.lower())

def _file_md5(path):
    key = str(path)
    if key in _HASH_CACHE:
        return _HASH_CACHE[key]
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    value = h.hexdigest()
    _HASH_CACHE[key] = value
    return value

def _find_by_content_md5(md5, suffix, size):
    if not _looks_like_md5(md5):
        return None
    index = get_media_index()
    if size and suffix:
        candidates = index["size_ext"].get((str(size), suffix), [])
    elif suffix:
        candidates = index["files_by_ext"].get(suffix, [])
    else:
        candidates = [p for paths in index["files_by_ext"].values() for p in paths]

    matches = []
    for fp in candidates:
        try:
            if _file_md5(fp).lower() == md5.lower():
                matches.append(fp)
        except OSError:
            pass
    return matches[0] if matches else None

def thumbnail_for(path, width=300, height=300):
    key = (str(path), width, height)
    if key in _THUMB_CACHE:
        return _THUMB_CACHE[key]
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scaled = size.scaled(QSize(width, height), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(scaled)
        pm = QPixmap.fromImage(reader.read())
    else:
        pm = QPixmap(str(path))
    if pm.isNull():
        pm = QPixmap(str(path))
    if pm.isNull():
        return pm
    thumb = pm if pm.width() <= width and pm.height() <= height else pm.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
    if len(_THUMB_CACHE) > 300:
        _THUMB_CACHE.clear()
    _THUMB_CACHE[key] = thumb
    return thumb

def resolve_media_path(data, media_dir):
    filename = data.get("filename", "")
    index = get_media_index()
    candidates = []
    for key in ("path", "file_path", "local_path", "downloaded_path", "saved_path"):
        value = data.get(key)
        if value:
            candidates.append(_candidate_path(value))

    if filename:
        candidates.append(media_dir / "media" / filename)
        for media_type in ("image", "video"):
            candidates.extend(media_dir.glob(f"*/{media_type}/{filename}"))

    for fp in candidates:
        if fp.exists():
            return fp

    if filename and media_dir.exists():
        matches = index["name"].get(filename.lower(), [])
        matches = _prefer_same_date(matches, media_dir)
        if matches:
            return matches[0]

    stem = Path(filename).stem.lower() if filename else ""
    if stem:
        cached_path = index["md5"].get(stem)
        if cached_path:
            return cached_path

    size = str(data.get("size", "")).strip()
    suffix = Path(filename).suffix.lower() if filename else ""
    if size and suffix:
        matches = index["size_ext"].get((size, suffix), [])
        matches = _prefer_same_date(matches, media_dir)
        if len(matches) == 1:
            return matches[0]

    fp = _find_by_content_md5(stem, suffix, size)
    if fp:
        return fp

    return None

def get_dates():
    if not DATA_ROOT.exists(): return []
    return sorted([d.name for d in DATA_ROOT.iterdir()
                   if d.is_dir() and (d / "chat.json").exists()], reverse=True)

def get_senders(date_str):
    records = load_chat(date_str)
    seen = {}
    for rec in records:
        s = rec.get("sender", {}); uin = s.get("uin",""); name = s.get("name","")
        mt = rec.get("message_type",""); gid = rec.get("group_id","")
        key = (mt, gid if mt == "group" else uin)
        if key not in seen:
            disp = f"[群:{gid}] {name}" if mt == "group" and gid else f"[私聊] {name} ({uin})"
            seen[key] = {"uin":uin,"name":name,"message_type":mt,"group_id":gid,"display":disp}
    return list(seen.values())

class MediaPreviewDialog(QDialog):
    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("媒体预览")
        self.setMinimumSize(600, 500)
        layout = QVBoxLayout(self)
        path = Path(file_path)
        if path.suffix.lower() in (".jpg",".jpeg",".png",".gif",".bmp",".webp",".suf"):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                label = QLabel()
                scaled = pixmap.scaled(800, 600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(scaled); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                scroll = QScrollArea(); scroll.setWidget(label); scroll.setWidgetResizable(True)
                layout.addWidget(scroll)
        elif path.suffix.lower() in (".mp4",".avi",".mov",".mkv",".webm",".flv"):
            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumSize(560, 380)
            layout.addWidget(self.video_widget, 1)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(0.7)
            self.media_player = QMediaPlayer(self)
            self.media_player.setAudioOutput(self.audio_output)
            self.media_player.setVideoOutput(self.video_widget)
            self.media_player.setSource(QUrl.fromLocalFile(str(path)))
            controls = QHBoxLayout()
            self.play_btn = QPushButton("暂停")
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.sliderMoved.connect(self.media_player.setPosition)
            self.play_btn.clicked.connect(self._toggle_video)
            self.media_player.positionChanged.connect(lambda p: self.slider.setValue(p) if not self.slider.isSliderDown() else None)
            self.media_player.durationChanged.connect(lambda d: self.slider.setRange(0, max(0, d)))
            self.media_player.playbackStateChanged.connect(lambda s: self.play_btn.setText("暂停" if s == QMediaPlayer.PlaybackState.PlayingState else "播放"))
            controls.addWidget(self.play_btn)
            controls.addWidget(self.slider, 1)
            layout.addLayout(controls)
            self.media_player.play()
        else:
            layout.addWidget(QLabel(f"文件: {path.name}"))
        btn = QPushButton("关闭"); btn.clicked.connect(self.accept)
        hl = QHBoxLayout(); hl.addStretch(); hl.addWidget(btn); layout.addLayout(hl)

    def _toggle_video(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

class ForwardDialog(QDialog):
    def __init__(self, content_data, date_str, parent=None):
        super().__init__(parent)
        self.date_str = date_str
        self.media_dir = get_media_dir(date_str)
        self.setWindowTitle("合并转发"); self.setMinimumSize(560, 520)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        container = QWidget(); cl = QVBoxLayout(container); cl.setSpacing(4); cl.setContentsMargins(8,8,8,8)
        if content_data:
            for sub in content_data:
                w = self._render_sub(sub); cl.addWidget(w)
        else:
            cl.addWidget(QLabel("(无内容)"))
        cl.addStretch(); scroll.setWidget(container); layout.addWidget(scroll)
        btn = QPushButton("关闭"); btn.clicked.connect(self.accept)
        hl = QHBoxLayout(); hl.addStretch(); hl.addWidget(btn); layout.addLayout(hl)

    def _render_sub(self, msg):
        frame = QFrame()
        frame.setStyleSheet("background:#fff;border:1px solid #e0e0e0;border-radius:6px;")
        layout = QVBoxLayout(frame); layout.setSpacing(2); layout.setContentsMargins(8,6,8,6)
        sender = msg.get("sender",{}); name = sender.get("name","?"); uin = sender.get("uin","")
        t = msg.get("time",""); c = msg.get("content",{}); text = c.get("text",""); elems = c.get("elements",[])
        hl = QHBoxLayout()
        hl.addWidget(QLabel(f"<b style='color:#12b7f5;'>{name}</b>"))
        if uin: hl.addWidget(QLabel(f"<span style='color:#999;font-size:11px;'>({uin})</span>"))
        hl.addStretch()
        if t: hl.addWidget(QLabel(f"<span style='color:#999;font-size:11px;'>{t}</span>"))
        layout.addLayout(hl)
        if text:
            lbl = QLabel(text); lbl.setWordWrap(True)
            layout.addWidget(lbl)
        for e in elems:
            et = e.get("type",""); ed = e.get("data",{})
            if et == "text":
                lbl = QLabel(ed.get("text","")); lbl.setWordWrap(True)
                layout.addWidget(lbl)
            elif et == "image":
                self._render_img(ed, layout)
            elif et == "video":
                self._render_vid(ed, layout)
            elif et == "forward":
                self._render_fwd(ed, layout)
            elif et == "json":
                self._render_json(ed, layout)
            elif et == "reply":
                layout.addWidget(QLabel(f"<span style='color:#999;font-style:italic;'>回复 {ed.get('id','?')}</span>"))
            elif et == "at":
                layout.addWidget(QLabel(f"<span style='color:#12b7f5;'>{ed.get('text','@?')}</span>"))
            elif et == "face":
                layout.addWidget(QLabel("[表情]"))
            elif et == "file":
                layout.addWidget(QLabel(f"[文件] {ed.get('name','?')}"))
        return frame

    def _render_img(self, data, layout):
        fn = data.get("filename","")
        if not fn: return
        fp = resolve_media_path(data, self.media_dir)
        if not fp:
            layout.addWidget(QLabel(f"<span style='color:#999;font-style:italic;'>[图片: {fn} 未找到]</span>"))
            return
        pm = thumbnail_for(fp, 260, 220)
        if not pm.isNull():
            lbl = QLabel(); lbl.setPixmap(pm)
            lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            lbl.setToolTip(f"点击预览: {fn} ({fp.stat().st_size//1024}KB)")
            lbl.mousePressEvent = lambda e, p=fp: self._preview(p)
            layout.addWidget(lbl)
        else:
            btn = QPushButton(f"打开图片 {fn}")
            btn.clicked.connect(lambda checked, p=fp: self._preview(p))
            layout.addWidget(btn)

    def _render_vid(self, data, layout):
        fn = data.get("filename","")
        if not fn: return
        fp = resolve_media_path(data, self.media_dir)
        if fp:
            btn = QPushButton(f"▶ {fn} ({fp.stat().st_size//1024}KB)")
            btn.setStyleSheet("QPushButton{background:#f0f0f0;border:1px solid #d0d0d0;border-radius:4px;padding:6px 12px;text-align:left;}QPushButton:hover{background:#e0e0e0;}")
            btn.clicked.connect(lambda checked, p=fp: self._preview(p))
            layout.addWidget(btn)
        else:
            layout.addWidget(QLabel(f"<span style='color:#999;font-style:italic;'>[视频: {fn} 未找到]</span>"))

    def _render_fwd(self, data, layout):
        content = data.get("content", []); text = data.get("text", "[合并转发]")
        card = QFrame()
        card.setStyleSheet("QFrame{background:#f7f8fa;border:1px solid #d6d9de;border-radius:6px;padding:6px;}QFrame:hover{background:#eef3f8;}")
        card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cl = QVBoxLayout(card); cl.setSpacing(2); cl.setContentsMargins(8,6,8,6)
        cl.addWidget(QLabel(f"<b>{text}</b>"))
        if content:
            cl.addWidget(QLabel(f"<span style='color:#777;font-size:12px;'>共 {len(content)} 条消息，点击展开</span>"))
            for sub in content[:3]:
                sn = sub.get("sender",{}).get("name","?")
                sc = sub.get("content",{}).get("text","")[:60]
                if not sc:
                    sc = self._brief_elements(sub.get("content",{}).get("elements",[]))
                cl.addWidget(QLabel(f"<span style='color:#666;font-size:12px;'>{sn}: {sc}</span>"))
        card.mousePressEvent = lambda e, c=content: self._open_fwd(c)
        layout.addWidget(card)

    def _render_json(self, data, layout):
        raw = data.get("data") or data.get("text") or ""
        prompt = data.get("prompt") or ""
        try:
            parsed = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
            meta = parsed.get("meta", {})
            news = meta.get("news", {}) if isinstance(meta, dict) else {}
            title = news.get("title") or prompt or "[卡片消息]"
            desc = news.get("desc", "")
        except Exception:
            title, desc = prompt or "[卡片消息]", ""
        lbl = QLabel(f"<b>{title}</b><br><span style='color:#777;'>{desc}</span>")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("background:#f8f8f8;border:1px solid #e0e0e0;border-radius:4px;padding:6px;")
        layout.addWidget(lbl)

    def _brief_elements(self, elems):
        names = []
        for elem in elems:
            et = elem.get("type","")
            if et == "image": names.append("[图片]")
            elif et == "video": names.append("[视频]")
            elif et == "forward": names.append("[合并转发]")
            elif et == "text": names.append(elem.get("data",{}).get("text",""))
            elif et == "json": names.append("[卡片消息]")
        return " ".join([n for n in names if n]) or "[非文本消息]"

    def _preview(self, file_path):
        dlg = MediaPreviewDialog(str(file_path), self); dlg.exec()

    def _open_fwd(self, content):
        dlg = ForwardDialog(content, self.date_str, self); dlg.exec()

class ArchiveViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        ml = QVBoxLayout(self); ml.setContentsMargins(8,8,8,8); ml.setSpacing(6)

        tb = QHBoxLayout()
        tb.addWidget(QLabel("日期:"))
        self.date_cb = QComboBox(); self.date_cb.setMinimumWidth(160)
        self.date_cb.currentTextChanged.connect(self._on_date_changed)
        tb.addWidget(self.date_cb)
        self.prev_btn = QPushButton("< 前一天")
        self.prev_btn.clicked.connect(lambda: self._shift_day(-1))
        tb.addWidget(self.prev_btn)
        self.next_btn = QPushButton("后一天 >")
        self.next_btn.clicked.connect(lambda: self._shift_day(1))
        tb.addWidget(self.next_btn)
        tb.addStretch()
        tb.addWidget(QLabel("筛选:"))
        self.filter_cb = QComboBox(); self.filter_cb.setMinimumWidth(200)
        self.filter_cb.currentIndexChanged.connect(self._on_filter_changed)
        tb.addWidget(self.filter_cb)
        ref_btn = QPushButton("刷新")
        ref_btn.clicked.connect(self._refresh)
        tb.addWidget(ref_btn)
        ml.addLayout(tb)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        lp = QWidget(); ll = QVBoxLayout(lp); ll.setContentsMargins(0,0,0,0)
        ll.addWidget(QLabel("<b>归档日期</b>"))
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(140); self.tree.setMaximumWidth(260)
        self.tree.itemClicked.connect(self._on_tree_click)
        ll.addWidget(self.tree); splitter.addWidget(lp)

        rp = QWidget(); rl = QVBoxLayout(rp); rl.setContentsMargins(0,0,0,0)
        self.stat_label = QLabel(""); self.stat_label.setStyleSheet("color:#666;padding:4px;")
        rl.addWidget(self.stat_label)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.msg_box = QWidget(); self.msg_layout = QVBoxLayout(self.msg_box)
        self.msg_layout.setSpacing(2); self.msg_layout.setContentsMargins(4,4,4,4)
        self.msg_layout.addStretch()
        scroll.setWidget(self.msg_box); rl.addWidget(scroll)
        splitter.addWidget(rp); splitter.setSizes([200,800])
        ml.addWidget(splitter)

    def _load_data(self):
        self.tree.clear()
        dates = get_dates()
        self.date_cb.blockSignals(True); self.date_cb.clear()
        for d in dates: self.date_cb.addItem(d)
        self.date_cb.blockSignals(False)
        root = QTreeWidgetItem(self.tree, ["所有日期"]);
        root.setData(0, Qt.ItemDataRole.UserRole, "__all__")
        for d in dates:
            item = QTreeWidgetItem(root, [d]); item.setData(0, Qt.ItemDataRole.UserRole, d)
        self.tree.expandAll()
        if dates:
            self.date_cb.setCurrentText(dates[0])
            self._show_messages(dates[0])

    def _shift_day(self, direction):
        idx = self.date_cb.currentIndex() - direction
        if 0 <= idx < self.date_cb.count():
            self.date_cb.setCurrentIndex(idx)

    def _on_date_changed(self, ds):
        if ds: self._show_messages(ds); self._select_tree(ds)

    def _select_tree(self, ds):
        root = self.tree.topLevelItem(0)
        if root:
            for i in range(root.childCount()):
                if root.child(i).text(0) == ds:
                    self.tree.setCurrentItem(root.child(i)); return
        self.tree.setCurrentItem(None)

    def _on_tree_click(self, item, col):
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if d and d != "__all__" and d != self.date_cb.currentText():
            self.date_cb.setCurrentText(d)

    def _on_filter_changed(self, idx):
        ds = self.date_cb.currentText()
        if ds: self._show_messages(ds)

    def _refresh(self):
        clear_media_index()
        self._load_data()

    def _show_messages(self, date_str):
        records = load_chat(date_str)
        senders = get_senders(date_str)
        self.filter_cb.blockSignals(True); self.filter_cb.clear()
        self.filter_cb.addItem("全部消息", "__all__")
        for s in senders: self.filter_cb.addItem(s["display"], s)
        self.filter_cb.blockSignals(False)

        fd = self.filter_cb.currentData()
        if fd and fd != "__all__":
            filtered = []
            for rec in records:
                mt = rec.get("message_type",""); gid = rec.get("group_id","")
                uin = rec.get("sender",{}).get("uin","")
                if mt == "group" and fd.get("message_type") == "group":
                    if gid == fd.get("group_id"): filtered.append(rec)
                else:
                    if uin == fd.get("uin"): filtered.append(rec)
            records = filtered

        self.stat_label.setText(f"共 {len(records)} 条消息 ({date_str})")
        self._clear_msgs()
        for rec in records:
            w = self._render_msg(rec, date_str)
            if w: self.msg_layout.insertWidget(self.msg_layout.count()-1, w)

    def _clear_msgs(self):
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            if item and item.widget(): item.widget().deleteLater()

    def _render_msg(self, rec, date_str):
        if not rec or rec.get("system", False): return None
        sender = rec.get("sender",{}); name = sender.get("name","?"); uin = sender.get("uin","")
        mt = rec.get("message_type",""); gid = rec.get("group_id","")
        t = rec.get("time",""); c = rec.get("content",{}); text = c.get("text",""); elems = c.get("elements",[])

        frame = QFrame(); frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("QFrame{background:transparent;border:none;margin:2px 0;}")
        layout = QVBoxLayout(frame); layout.setSpacing(1); layout.setContentsMargins(8,4,8,4)

        hl = QHBoxLayout()
        hl.addWidget(QLabel(f"<b style='color:#12b7f5;font-size:13px;'>{name}</b>"))
        if mt == "group" and gid:
            hl.addWidget(QLabel(f"<span style='color:#999;font-size:11px;'>[群:{gid}]</span>"))
        hl.addStretch()
        if t: hl.addWidget(QLabel(f"<span style='color:#999;font-size:11px;'>{t}</span>"))
        layout.addLayout(hl)

        md = get_media_dir(date_str)
        for elem in elems:
            et = elem.get("type",""); ed = elem.get("data",{})
            if et == "text":
                lbl = QLabel(ed.get("text","")); lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size:14px;padding:2px 0;")
                layout.addWidget(lbl)
            elif et == "image":
                self._render_img(ed, md, layout)
            elif et == "video":
                self._render_vid(ed, md, layout)
            elif et == "forward":
                self._render_fwd(ed, date_str, layout)
            elif et == "reply":
                layout.addWidget(QLabel(f"<span style='color:#999;font-style:italic;'>回复 {ed.get('id','?')}</span>"))
            elif et == "at":
                layout.addWidget(QLabel(f"<span style='color:#12b7f5;'>{ed.get('text','@?')}</span>"))
            elif et == "face":
                layout.addWidget(QLabel(f"[表情]"))
            elif et == "file":
                layout.addWidget(QLabel(f"[文件] {ed.get('name','?')}"))

        if not elems and text:
            lbl = QLabel(text); lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size:14px;padding:2px 0;")
            layout.addWidget(lbl)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine); line.setStyleSheet("color:#e8e8e8;")
        layout.addWidget(line)
        return frame

    def _render_img(self, data, media_dir, layout):
        fn = data.get("filename","")
        if not fn: return
        fp = resolve_media_path(data, media_dir)
        if not fp:
            layout.addWidget(QLabel(f"<span style='color:#999;font-style:italic;'>[图片: {fn} 未找到]</span>"))
            return
        try:
            pm = thumbnail_for(fp, 300, 300)
            if not pm.isNull():
                lbl = QLabel(); lbl.setPixmap(pm)
                lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                lbl.setToolTip(f"点击预览: {fn} ({fp.stat().st_size//1024}KB)")
                lbl.mousePressEvent = lambda e, p=fp: self._preview(p)
                layout.addWidget(lbl)
            else:
                layout.addWidget(QLabel(f"<a href='#' style='color:#12b7f5;'>{fn}</a>"))
        except:
            layout.addWidget(QLabel(f"<span style='color:#999;'>{fn}</span>"))

    def _render_vid(self, data, media_dir, layout):
        fn = data.get("filename","")
        if not fn: return
        fp = resolve_media_path(data, media_dir)
        if fp:
            btn = QPushButton(f"▶ {fn} ({fp.stat().st_size//1024}KB)")
            btn.setStyleSheet("QPushButton{background:#f0f0f0;border:1px solid #d0d0d0;border-radius:4px;padding:6px 12px;text-align:left;}QPushButton:hover{background:#e0e0e0;}")
            btn.clicked.connect(lambda checked, p=fp: self._preview(p))
            layout.addWidget(btn)
        else:
            layout.addWidget(QLabel(f"<span style='color:#999;font-style:italic;'>[视频: {fn} 未找到]</span>"))

    def _render_fwd(self, data, date_str, layout):
        content = data.get("content", []); text = data.get("text", "[合并转发]")
        card = QFrame(); card.setStyleSheet("QFrame{background:#f5f5f5;border:1px solid #e0e0e0;border-radius:6px;padding:8px;}QFrame:hover{background:#e8e8e8;}")
        card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cl = QVBoxLayout(card); cl.setSpacing(2); cl.setContentsMargins(8,6,8,6)
        cl.addWidget(QLabel(f"<b>{text}</b>"))
        if content:
            cl.addWidget(QLabel(f"<span style='color:#999;font-size:12px;'>共 {len(content)} 条消息</span>"))
            for sub in content[:3]:
                sn = sub.get("sender",{}).get("name","?"); sc = sub.get("content",{}).get("text","")[:60]
                cl.addWidget(QLabel(f"<span style='color:#666;font-size:12px;padding-left:8px;'>{sn}: {sc or '[非文本消息]'}</span>"))
        card.mousePressEvent = lambda e, c=content, d=date_str: self._open_fwd(c, d)
        layout.addWidget(card)

    def _preview(self, file_path):
        dlg = MediaPreviewDialog(str(file_path), self); dlg.exec()

    def _open_fwd(self, content, date_str):
        dlg = ForwardDialog(content, date_str, self); dlg.exec()

__all__ = ["ArchiveViewer", "MediaPreviewDialog", "ForwardDialog"]
