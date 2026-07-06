from __future__ import annotations

import sys
import re
import secrets
from pathlib import Path
from urllib.parse import quote, urlparse

from PyQt6.QtCore import QPoint, QSize, QTimer, Qt, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QImage, QImageReader, QPainter, QPixmap, QPolygon, QTextCursor
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QProcess, QProcessEnvironment

from app.core.archive import MediaItem, scan_media
from app.core.config import apply_settings_to_napcat_onebot_network, apply_settings_to_napcat_webui, load_settings, save_settings
from app.core.napcat_process import kill_project_napcat_processes, list_project_napcat_processes
from app.core.paths import DATA_ROOT, NAPCAT_DIR, PYTHON_EXE, ROOT_DIR
from app.core.process_lock import active_lock_pid, remove_stale_lock


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

from app.gui.archive_viewer import ArchiveViewer


MEDIA_ICON_READY_ROLE = Qt.ItemDataRole.UserRole.value + 1
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".suf"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}


def scaled_pixmap(path: Path, size: QSize) -> QPixmap:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    image_size = reader.size()
    if image_size.isValid() and image_size.width() > 0 and image_size.height() > 0:
        reader.setScaledSize(image_size.scaled(size, Qt.AspectRatioMode.KeepAspectRatio))
        pixmap = QPixmap.fromImage(reader.read())
        if not pixmap.isNull():
            return pixmap
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return pixmap
    return pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)


def add_play_overlay(pixmap: QPixmap) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    pixmap = QPixmap(pixmap)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = max(14, min(pixmap.width(), pixmap.height()) // 5)
    center = pixmap.rect().center()
    painter.setBrush(QColor(0, 0, 0, 150))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(center, radius, radius)
    tri = QPolygon([
        QPoint(center.x() - radius // 3, center.y() - radius // 2),
        QPoint(center.x() - radius // 3, center.y() + radius // 2),
        QPoint(center.x() + radius // 2, center.y()),
    ])
    painter.setBrush(QColor(255, 255, 255, 230))
    painter.drawPolygon(tri)
    painter.end()
    return pixmap


def video_thumbnail_pixmap(path: Path, size: QSize) -> QPixmap:
    try:
        import cv2
    except Exception:
        return QPixmap()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return QPixmap()
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count > 10:
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(30, frame_count - 1))
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, 500)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok or frame is None:
            return QPixmap()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = frame.shape
        qimage = QImage(frame.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimage)
        if pixmap.isNull():
            return pixmap
        pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        return add_play_overlay(pixmap)
    finally:
        cap.release()


def media_thumbnail_pixmap(item: MediaItem, size: QSize) -> QPixmap:
    if item.media_type == "video":
        return video_thumbnail_pixmap(item.path, size)
    return scaled_pixmap(item.path, size)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("QQ_Chat Monitor")
        self.resize(1180, 760)
        self.settings = load_settings()
        self.monitor_process: QProcess | None = None
        self.media_items: list[MediaItem] = []
        self.media_icon_cache: dict[tuple[str, float], QIcon] = {}
        self.media_icon_queue: list[int] = []
        self.video_thumb_queue: list[int] = []
        self.video_thumb_current_row: int | None = None
        self.qr_mtime: float | None = None

        self._build_ui()
        self._build_tray()
        self._load_settings_to_widgets()
        self.refresh_media()
        self.refresh_logs()

        self.media_timer = QTimer(self)
        self.media_timer.setInterval(3000)
        self.media_timer.timeout.connect(self.refresh_media)
        self.media_timer.start()

        self.media_icon_timer = QTimer(self)
        self.media_icon_timer.setInterval(25)
        self.media_icon_timer.timeout.connect(self._load_more_media_icons)
        self._schedule_media_icon_loading()

        self.log_timer = QTimer(self)
        self.log_timer.setInterval(2000)
        self.log_timer.timeout.connect(self.refresh_logs)
        self.log_timer.start()

        self.qr_timer = QTimer(self)
        self.qr_timer.setInterval(1000)
        self.qr_timer.timeout.connect(self.update_login_qr)
        self.qr_timer.start()
        self.update_login_qr()

        if self.settings["app"].get("auto_start_monitor_on_gui_launch"):
            self.start_monitor()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)

        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        for name in ("运行状态", "媒体归档", "监控对象", "下载设置", "NapCat 设置", "日志", "聊天记录归档", "使用教程"):
            self.nav.addItem(name)
        self.nav.currentRowChanged.connect(self._switch_page)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_status_page())
        self.pages.addWidget(self._build_media_page())
        self.pages.addWidget(self._build_targets_page())
        self.pages.addWidget(self._build_download_page())
        self.pages.addWidget(self._build_napcat_page())
        self.pages.addWidget(self._build_logs_page())
        self.pages.addWidget(self._build_archive_page())
        self.pages.addWidget(self._build_help_page())

        outer.addWidget(self.nav)
        outer.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(0)

    def _build_status_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        controls = QHBoxLayout()
        self.start_button = QPushButton("启动监控")
        self.stop_button = QPushButton("停止监控")
        self.open_archive_button = QPushButton("打开归档目录")
        self.open_webui_button = QPushButton("打开 NapCat WebUI")
        self.start_button.clicked.connect(self.start_monitor)
        self.stop_button.clicked.connect(self.stop_monitor)
        self.open_archive_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_ROOT))))
        self.open_webui_button.clicked.connect(self.open_webui)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.open_archive_button)
        controls.addWidget(self.open_webui_button)
        controls.addStretch(1)

        self.status_label = QLabel("监控未启动")
        self.status_label.setObjectName("statusLabel")
        self.status_log = QTextEdit()
        self.status_log.setReadOnly(True)
        self.status_log.setPlaceholderText("启动监控后，NapCat 的扫码登录、运行输出和下载日志会显示在这里。")

        body = QHBoxLayout()
        body.addWidget(self.status_log, 1)

        qr_box = QVBoxLayout()
        qr_title = QLabel("登录二维码")
        qr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label = QLabel("等待 NapCat 生成二维码")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(280, 280)
        self.qr_label.setStyleSheet("border: 1px solid #d4d7dd; background: #ffffff;")
        open_qr = QPushButton("打开二维码图片")
        open_qr.clicked.connect(self.open_login_qr)
        qr_hint = QLabel("使用手机 QQ 扫描这里的图片登录。")
        qr_hint.setWordWrap(True)
        qr_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_box.addWidget(qr_title)
        qr_box.addWidget(self.qr_label)
        qr_box.addWidget(open_qr)
        qr_box.addWidget(qr_hint)
        qr_box.addStretch(1)
        body.addLayout(qr_box)

        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addLayout(body, 1)
        return page

    def _build_media_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)

        left = QVBoxLayout()
        filters = QHBoxLayout()
        self.media_filter = QComboBox()
        self.media_filter.addItems(["全部", "图片", "视频"])
        self.media_filter.currentIndexChanged.connect(self.refresh_media)
        self.media_date_filter = QComboBox()
        self.media_date_filter.currentIndexChanged.connect(self.refresh_media)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_media)
        filters.addWidget(self.media_date_filter)
        filters.addWidget(self.media_filter)
        filters.addWidget(refresh)

        self.media_list = QListWidget()
        self.media_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.media_list.setIconSize(QSize(128, 96))
        self.media_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.media_list.setMovement(QListWidget.Movement.Static)
        self.media_list.itemSelectionChanged.connect(self.preview_selected_media)
        self.media_list.verticalScrollBar().valueChanged.connect(self._prioritize_visible_media_icons)
        left.addLayout(filters)
        left.addWidget(self.media_list, 1)

        right = QVBoxLayout()
        self.preview_stack = QStackedWidget()
        self.preview_label = QLabel("选择一个媒体文件进行预览")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumWidth(360)
        self.preview_label.setStyleSheet("border: 1px solid #d4d7dd; background: #f8f9fb;")
        self.preview_stack.addWidget(self.preview_label)

        video_page = QWidget()
        video_layout = QVBoxLayout(video_page)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumWidth(360)
        self.video_widget.setStyleSheet("background: #000000; border: 1px solid #d4d7dd;")
        video_controls = QHBoxLayout()
        self.video_play_button = QPushButton("播放")
        self.video_play_button.clicked.connect(self.toggle_video_playback)
        self.video_slider = QSlider(Qt.Orientation.Horizontal)
        self.video_slider.sliderMoved.connect(self.set_video_position)
        video_controls.addWidget(self.video_play_button)
        video_controls.addWidget(self.video_slider, 1)
        video_layout.addWidget(self.video_widget, 1)
        video_layout.addLayout(video_controls)
        self.preview_stack.addWidget(video_page)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.7)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.positionChanged.connect(self.update_video_position)
        self.media_player.durationChanged.connect(self.update_video_duration)
        self.media_player.playbackStateChanged.connect(self.update_video_button)

        self.video_thumb_sink = QVideoSink(self)
        self.video_thumb_player = QMediaPlayer(self)
        self.video_thumb_player.setVideoSink(self.video_thumb_sink)
        self.video_thumb_sink.videoFrameChanged.connect(self._on_video_thumbnail_frame)

        self.media_detail = QTextEdit()
        self.media_detail.setReadOnly(True)
        open_file = QPushButton("打开文件")
        open_file.clicked.connect(self.open_selected_media)
        open_folder = QPushButton("打开当前日期文件夹")
        open_folder.clicked.connect(self.open_media_date_folder)
        right.addWidget(self.preview_stack, 1)
        right.addWidget(self.media_detail)
        right.addWidget(open_file)
        right.addWidget(open_folder)

        layout.addLayout(left, 2)
        layout.addLayout(right, 1)
        return page

    def _build_targets_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.all_private_check = QCheckBox("监控所有私聊")
        self.all_group_check = QCheckBox("监控所有群聊")
        self.private_uins_edit = QTextEdit()
        self.private_uins_edit.setPlaceholderText("每行一个 QQ 号")
        self.group_ids_edit = QTextEdit()
        self.group_ids_edit.setPlaceholderText("每行一个群号")

        layout.addWidget(self.all_private_check)
        layout.addWidget(QLabel("私聊白名单"))
        layout.addWidget(self.private_uins_edit, 1)
        layout.addWidget(self.all_group_check)
        layout.addWidget(QLabel("群聊白名单"))
        layout.addWidget(self.group_ids_edit, 1)
        layout.addLayout(self._save_row())
        return page

    def _build_download_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self.batch_interval_spin = self._spin(0, 3600)
        self.concurrent_spin = self._spin(1, 32)
        self.timeout_spin = self._spin(5, 3600)
        self.retry_spin = self._spin(0, 10)
        self.md5_check = QCheckBox("启用 MD5 去重")
        self.images_check = QCheckBox("下载图片")
        self.videos_check = QCheckBox("下载视频")
        self.forward_check = QCheckBox("解析合并转发")
        self.reply_check = QCheckBox("解析引用消息")
        self.video_autoplay_check = QCheckBox("视频预览默认自动播放")

        form.addRow("批量等待秒数", self.batch_interval_spin)
        form.addRow("消息并发数", self.concurrent_spin)
        form.addRow("下载超时秒数", self.timeout_spin)
        form.addRow("网络重试次数", self.retry_spin)
        form.addRow(self.md5_check)
        form.addRow(self.images_check)
        form.addRow(self.videos_check)
        form.addRow(self.forward_check)
        form.addRow(self.reply_check)
        form.addRow(self.video_autoplay_check)

        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(self._save_row())
        return page

    def _build_napcat_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self.http_url_edit = QLineEdit()
        self.access_token_edit = QLineEdit()
        self.access_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.websocket_host_edit = QLineEdit()
        self.websocket_port_spin = self._spin(1, 65535)
        self.websocket_token_edit = QLineEdit()
        self.websocket_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.uin_edit = QLineEdit()
        self.auto_start_napcat_check = QCheckBox("启动监控时自动启动 NapCat")
        self.webui_host_edit = QLineEdit()
        self.webui_port_spin = self._spin(1, 65535)
        self.webui_token_edit = QLineEdit()
        self.webui_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.napcat_config_preview = QTextEdit()
        self.napcat_config_preview.setReadOnly(True)
        self.napcat_config_preview.setMinimumHeight(170)

        form.addRow("HTTP API 地址", self.http_url_edit)
        form.addRow("HTTP Token", self.access_token_edit)
        form.addRow("WebSocket 监听地址", self.websocket_host_edit)
        form.addRow("WebSocket 端口", self.websocket_port_spin)
        form.addRow("WebSocket Token", self.websocket_token_edit)
        form.addRow("默认登录 QQ", self.uin_edit)
        form.addRow(self.auto_start_napcat_check)
        form.addRow("WebUI Host", self.webui_host_edit)
        form.addRow("WebUI 端口", self.webui_port_spin)
        form.addRow("WebUI Token", self.webui_token_edit)

        buttons = QHBoxLayout()
        save = QPushButton("保存设置")
        generate = QPushButton("生成默认网络配置")
        copy_network = QPushButton("复制 NapCat 填写内容")
        apply = QPushButton("写入 NapCat 配置")
        open_webui = QPushButton("打开 WebUI")
        save.clicked.connect(lambda: self.save_settings_from_widgets())
        generate.clicked.connect(self.generate_default_network_config)
        copy_network.clicked.connect(self.copy_napcat_network_config)
        apply.clicked.connect(self.apply_napcat_config)
        open_webui.clicked.connect(self.open_webui)
        buttons.addWidget(save)
        buttons.addWidget(generate)
        buttons.addWidget(copy_network)
        buttons.addWidget(apply)
        buttons.addWidget(open_webui)
        buttons.addStretch(1)

        hint = QLabel("首次登录时可以先保存默认 QQ 和网络设置，再写入 NapCat 配置；二维码和 NapCat 输出会显示在运行状态页。")
        hint.setWordWrap(True)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("NapCat 网络配置对应内容"))
        layout.addWidget(self.napcat_config_preview)
        layout.addWidget(hint)
        layout.addStretch(1)
        self._connect_network_preview_signals()
        return page

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.file_log = QTextEdit()
        self.file_log.setReadOnly(True)
        toolbar = QHBoxLayout()
        refresh = QPushButton("刷新日志")
        refresh.clicked.connect(self.refresh_logs)
        clear_btn = QPushButton("清空显示")
        clear_btn.clicked.connect(self.clear_log_display)
        clear_file = QPushButton("删除日志文件")
        clear_file.clicked.connect(self.clear_log_file)
        toolbar.addWidget(refresh)
        toolbar.addWidget(clear_btn)
        toolbar.addWidget(clear_file)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        layout.addWidget(self.file_log, 1)
        return page

    def _build_help_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        actions = QHBoxLayout()
        go_status = QPushButton("去运行状态")
        go_napcat = QPushButton("去 NapCat 设置")
        go_targets = QPushButton("去监控对象")
        go_media = QPushButton("去媒体归档")
        go_status.clicked.connect(lambda: self.nav.setCurrentRow(0))
        go_media.clicked.connect(lambda: self.nav.setCurrentRow(1))
        go_targets.clicked.connect(lambda: self.nav.setCurrentRow(2))
        go_napcat.clicked.connect(lambda: self.nav.setCurrentRow(4))
        actions.addWidget(go_status)
        actions.addWidget(go_napcat)
        actions.addWidget(go_targets)
        actions.addWidget(go_media)
        actions.addStretch(1)

        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setHtml(self._help_html())

        layout.addLayout(actions)
        layout.addWidget(self.help_text, 1)
        return page

    def _help_html(self) -> str:
        return """
        <h2>QQ_Chat Monitor 使用教程</h2>

<h3>启动说明</h3>
<ul>
  <li>双击 <b>启动图形界面.bat</b> 后，命令行窗口会显示"正在加载"的提示。</li>
  <li>图形界面会在几秒后弹出，命令行窗口会自动关闭。</li>
  <li>如果双击后什么都没有发生，请用记事本打开 bat 文件检查路径是否正确。</li>
</ul>

        <h3>首次配置：只按按钮点</h3>
        <ol>
          <li>打开 <b>NapCat 设置</b> 页面。</li>
          <li>点击 <b>生成默认网络配置</b>。</li>
          <li>在 <b>默认登录 QQ</b> 填入要登录的 QQ 号。</li>
          <li>点击 <b>保存设置</b>。</li>
          <li>点击 <b>写入 NapCat 配置</b>。</li>
          <li>打开 <b>监控对象</b> 页面，填要监控的好友 QQ 或群号，然后点击 <b>保存配置</b>。</li>
          <li>回到 <b>运行状态</b> 页面，点击 <b>启动监控</b>。</li>
          <li>用手机 QQ 扫右侧二维码登录。</li>
        </ol>

        <h3>以后日常使用</h3>
        <ol>
          <li>双击 <b>启动图形界面.bat</b>。</li>
          <li>进入 <b>运行状态</b>，点击 <b>启动监控</b>。</li>
          <li>如果出现二维码就扫码；如果已经登录，等待自动登录完成。</li>
          <li>让被监控对象发送图片或视频。</li>
          <li>进入 <b>媒体归档</b> 查看下载结果。</li>
        </ol>

        <h3>极简监控模式</h3>
        <p>如果不需要图形界面，双击 <b>启动监控下载.bat</b>。它只启动监控下载核心，占用更少资源。</p>

        <h3>下载归档保存在哪里</h3>
        <p>媒体文件按日期和对象分开保存，聊天记录仍然保存在日期目录下。</p>
        <pre>
ALL_Fold/日期/chat.json
ALL_Fold/日期/private_QQ号/image
ALL_Fold/日期/private_QQ号/video
ALL_Fold/日期/group_群号/image
ALL_Fold/日期/group_群号/video
        </pre>
        <p>媒体归档和聊天记录归档都会自动扫描这些目录；旧版 <b>media/image</b>、<b>media/video</b> 结构也仍然兼容。</p>

        <h3>聊天记录归档</h3>
        <ul>
          <li><b>聊天记录归档</b> 页面可以按日期和对象查看记录。</li>
          <li>合并聊天记录可以点开查看，里面继续嵌套的合并聊天记录也会尽量还原。</li>
          <li>图片和视频会优先按记录中的本地路径查找；找不到时会用文件名、日期目录和 MD5 缓存兜底。</li>
        </ul>

        <h3>看到这些日志，说明配置基本正确</h3>
        <pre>
NapCat HTTP 服务端口已就绪
WebSocket 服务端已启动
WebSocket 连接已建立
批量处理开始
下载成功
        </pre>

        <h3>NapCat 网络配置到底做了什么</h3>
        <p><b>写入 NapCat 配置</b> 会自动写入两类配置：</p>
        <ul>
          <li><b>HTTP服务器</b>：NapCat 在 localhost:3000 提供消息详情和文件下载接口。</li>
          <li><b>Websocket客户端</b>：NapCat 主动连接本工具的 WebSocket 地址，默认是 ws://localhost:18082。</li>
        </ul>
        <p>所以不需要自己新建 Websocket服务器。</p>

        <h3>没有下载时按这个顺序检查</h3>
        <ol>
          <li><b>监控对象</b> 是否填了正确的好友 QQ 或群号。</li>
          <li>日志是否有 <b>WebSocket 连接已建立</b>。</li>
          <li>日志是否有 <b>NapCat HTTP 服务端口已就绪</b>。</li>
          <li>修改 NapCat 设置后，是否重新启动过 NapCat 或重新启动监控。</li>
          <li>被监控对象发的是否是图片或视频。</li>
        </ol>

        <h3>关闭窗口 = 最小化到托盘</h3>
<ul>
  <li>点击窗口右上角的 <b>✕</b>，程序不会退出，而是最小化到右下角任务栏托盘。</li>
  <li>双击托盘图标可以恢复窗口。</li>
  <li>在托盘图标上 <b>右键 → 退出</b> 才能完全关闭程序。</li>
  <li>第一次关闭窗口时可以选择“最小化到托盘”或“直接退出”，也可以勾选不再提醒。</li>
  <li>彻底退出或停止监控后，如果检测到当前项目的 NapCat/node 仍在运行，会询问是否一起关闭。</li>
</ul>

<h3>按钮说明</h3>
        <ul>
          <li><b>生成默认网络配置</b>：生成 localhost 默认端口和随机 Token。</li>
          <li><b>保存设置</b>：保存本工具配置。</li>
          <li><b>写入 NapCat 配置</b>：把 HTTP服务器、Websocket客户端和 WebUI 设置写入 NapCat。</li>
          <li><b>启动监控</b>：启动 NapCat 和下载核心。</li>
          <li><b>停止监控</b>：停止当前 GUI 启动的监控进程。</li>
        </ul>
        """

    def _save_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        save = QPushButton("保存配置")
        reload_button = QPushButton("重新载入")
        save.clicked.connect(lambda: self.save_settings_from_widgets())
        reload_button.clicked.connect(self.reload_settings)
        row.addWidget(save)
        row.addWidget(reload_button)
        row.addStretch(1)
        return row

    def _spin(self, low: int, high: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        return spin


    def _build_archive_page(self) -> QWidget:
        """聊天记录归档页面"""
        try:
            viewer = ArchiveViewer(self)
            return viewer
        except Exception as e:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel(f"加载归档界面失败: {e}"))
            import traceback
            layout.addWidget(QLabel(traceback.format_exc()))
            return page

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        menu = self.tray.contextMenu() or None
        from PyQt6.QtWidgets import QMenu

        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        quit_action = QAction("退出", self)
        show_action.triggered.connect(self.show_normal)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)

    def _load_settings_to_widgets(self) -> None:
        s = self.settings
        self.all_private_check.setChecked(s["monitor"]["all_private"])
        self.all_group_check.setChecked(s["monitor"]["all_group"])
        self.private_uins_edit.setPlainText("\n".join(s["monitor"]["private_uins"]))
        self.group_ids_edit.setPlainText("\n".join(s["monitor"]["group_ids"]))
        self.batch_interval_spin.setValue(s["download"]["batch_interval"])
        self.concurrent_spin.setValue(s["download"]["max_concurrent_msgs"])
        self.timeout_spin.setValue(s["download"]["timeout"])
        self.retry_spin.setValue(s["download"]["retry_network_errors"])
        self.md5_check.setChecked(s["download"]["enable_md5_dedup"])
        self.images_check.setChecked(s["download"]["download_images"])
        self.videos_check.setChecked(s["download"]["download_videos"])
        self.forward_check.setChecked(s["download"]["parse_forward"])
        self.reply_check.setChecked(s["download"]["parse_reply"])
        self.video_autoplay_check.setChecked(s["app"].get("video_preview_autoplay", False))
        self.http_url_edit.setText(s["napcat"]["http_url"])
        self.access_token_edit.setText(s["napcat"]["access_token"])
        self.websocket_host_edit.setText(s["websocket"]["host"])
        self.websocket_port_spin.setValue(s["websocket"]["port"])
        self.websocket_token_edit.setText(s["websocket"]["token"])
        self.uin_edit.setText(s["napcat"]["uin"])
        self.auto_start_napcat_check.setChecked(s["napcat"]["auto_start"])
        self.webui_host_edit.setText(s["napcat"]["webui_host"])
        self.webui_port_spin.setValue(s["napcat"]["webui_port"])
        self.webui_token_edit.setText(s["napcat"]["webui_token"])
        self.update_network_config_preview()

    def _collect_settings(self) -> dict:
        settings = load_settings()
        settings["monitor"]["all_private"] = self.all_private_check.isChecked()
        settings["monitor"]["all_group"] = self.all_group_check.isChecked()
        settings["monitor"]["private_uins"] = self._lines(self.private_uins_edit)
        settings["monitor"]["group_ids"] = self._lines(self.group_ids_edit)
        settings["download"]["batch_interval"] = self.batch_interval_spin.value()
        settings["download"]["max_concurrent_msgs"] = self.concurrent_spin.value()
        settings["download"]["timeout"] = self.timeout_spin.value()
        settings["download"]["retry_network_errors"] = self.retry_spin.value()
        settings["download"]["enable_md5_dedup"] = self.md5_check.isChecked()
        settings["download"]["download_images"] = self.images_check.isChecked()
        settings["download"]["download_videos"] = self.videos_check.isChecked()
        settings["download"]["parse_forward"] = self.forward_check.isChecked()
        settings["download"]["parse_reply"] = self.reply_check.isChecked()
        settings["app"]["video_preview_autoplay"] = self.video_autoplay_check.isChecked()
        settings["napcat"]["http_url"] = self.http_url_edit.text().strip() or "http://localhost:3000"
        settings["napcat"]["access_token"] = self.access_token_edit.text().strip()
        settings["websocket"]["host"] = self.websocket_host_edit.text().strip() or "localhost"
        settings["websocket"]["port"] = self.websocket_port_spin.value()
        settings["websocket"]["token"] = self.websocket_token_edit.text().strip()
        settings["napcat"]["uin"] = self.uin_edit.text().strip()
        settings["napcat"]["auto_start"] = self.auto_start_napcat_check.isChecked()
        settings["napcat"]["webui_host"] = self.webui_host_edit.text().strip() or "::"
        settings["napcat"]["webui_port"] = self.webui_port_spin.value()
        settings["napcat"]["webui_token"] = self.webui_token_edit.text().strip()
        return settings

    def _lines(self, edit: QTextEdit) -> list[str]:
        return [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]

    def save_settings_from_widgets(self, notify: bool = True) -> None:
        self.settings = self._collect_settings()
        save_settings(self.settings)
        if notify:
            QMessageBox.information(self, "已保存", "配置已保存。重启监控后生效。")

    def _connect_network_preview_signals(self) -> None:
        for edit in (
            self.http_url_edit,
            self.access_token_edit,
            self.websocket_host_edit,
            self.websocket_token_edit,
            self.webui_host_edit,
            self.webui_token_edit,
        ):
            edit.textChanged.connect(self.update_network_config_preview)
        for spin in (self.websocket_port_spin, self.webui_port_spin):
            spin.valueChanged.connect(self.update_network_config_preview)

    def generate_default_network_config(self, *args) -> None:
        import socket
        token = secrets.token_urlsafe(18)
        self.http_url_edit.setText("http://localhost:3000")
        self.access_token_edit.setText(token)
        self.websocket_host_edit.setText("localhost")
        ws_port = 18082
        for _ in range(20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", ws_port))
                    break
                except OSError:
                    ws_port += 1
        self.websocket_port_spin.setValue(ws_port)
        self.websocket_token_edit.setText(token)
        self.webui_host_edit.setText("::")
        self.webui_port_spin.setValue(6099)
        self.update_network_config_preview()
        QMessageBox.information(self, "已生成", "已生成默认地址和随机 Token。保存后，按下方内容填写 NapCat 网络配置。")

    def _http_host_port(self) -> tuple[str, int]:
        parsed = urlparse(self.http_url_edit.text().strip() or "http://localhost:3000")
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 3000)
        return host, port

    def napcat_network_config_text(self) -> str:
        http_host, http_port = self._http_host_port()
        http_token = self.access_token_edit.text().strip()
        ws_host = self.websocket_host_edit.text().strip() or "localhost"
        ws_port = self.websocket_port_spin.value()
        ws_token = self.websocket_token_edit.text().strip()
        ws_url = f"ws://{ws_host}:{ws_port}"
        if ws_token:
            ws_url = f"{ws_url}?access_token={quote(ws_token, safe='')}"

        return "\n".join(
            [
                "NapCat WebUI -> 网络配置",
                "",
                "1. HTTP服务器",
                f"   Host / 监听地址: {http_host}",
                f"   Port / 端口: {http_port}",
                f"   Token / Access Token: {http_token or '(留空)'}",
                "",
                "2. Websocket客户端",
                f"   URL / 地址: {ws_url}",
                f"   Token / Access Token: {ws_token or '(留空)'}",
                "",
                "不用新建 Websocket服务器；这里是 NapCat 作为客户端连接 QQ_Chat Monitor。",
            ]
        )

    def update_network_config_preview(self, *args) -> None:
        if hasattr(self, "napcat_config_preview"):
            self.napcat_config_preview.setPlainText(self.napcat_network_config_text())

    def copy_napcat_network_config(self, *args) -> None:
        QApplication.clipboard().setText(self.napcat_network_config_text())
        QMessageBox.information(self, "已复制", "NapCat 网络配置对应内容已复制到剪贴板。")

    def reload_settings(self, *args) -> None:
        self.settings = load_settings()
        self._load_settings_to_widgets()

    def apply_napcat_config(self, *args) -> None:
        self.save_settings_from_widgets(notify=False)
        webui_ok = apply_settings_to_napcat_webui(self.settings)
        network_ok, network_msg = apply_settings_to_napcat_onebot_network(self.settings)
        lines = []
        lines.append("WebUI 配置：已写入。" if webui_ok else "WebUI 配置：未找到配置文件。")
        lines.append(f"网络配置：{network_msg}")
        if network_ok:
            lines.append("请重启 NapCat 或重新启动监控后生效。")
        QMessageBox.information(self, "NapCat 配置", "\n".join(lines))

    def start_monitor(self, *args) -> None:
        if self.monitor_process and self.monitor_process.state() != QProcess.ProcessState.NotRunning:
            return
        remove_stale_lock()
        locked_pid = active_lock_pid()
        if locked_pid:
            QMessageBox.warning(
                self,
                "监控已在运行",
                f"检测到已有监控程序正在运行，PID: {locked_pid}。\n\n请先关闭已有监控后再启动，避免重复下载和端口冲突。",
            )
            self.status_label.setText(f"监控已在运行 (PID: {locked_pid})")
            return
        self.save_settings_from_widgets(notify=False)
        self.monitor_process = QProcess(self)
        self.monitor_process.setWorkingDirectory(str(ROOT_DIR))
        self.monitor_process.setProgram(str(PYTHON_EXE if PYTHON_EXE.exists() else Path(sys.executable)))
        self.monitor_process.setArguments([str(ROOT_DIR / "main.py"), "--minimal"])
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("NO_COLOR", "1")
        self.monitor_process.setProcessEnvironment(env)
        self.monitor_process.readyReadStandardOutput.connect(self._read_process_output)
        self.monitor_process.readyReadStandardError.connect(self._read_process_output)
        self.monitor_process.finished.connect(self._monitor_finished)
        self.monitor_process.start()
        self.status_label.setText("监控运行中")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop_monitor(self, *args) -> None:
        if not self.monitor_process:
            self._offer_stop_leftover_napcat()
            return
        if self.monitor_process.state() != QProcess.ProcessState.NotRunning:
            self.monitor_process.terminate()
            if not self.monitor_process.waitForFinished(5000):
                self.monitor_process.kill()
        self._monitor_finished()
        self._offer_stop_leftover_napcat()

    def _offer_stop_leftover_napcat(self) -> None:
        processes = list_project_napcat_processes()
        if not processes:
            return
        detail = "\n".join(
            f"PID {p.get('ProcessId')}: {p.get('ExecutablePath') or ''}"
            for p in processes[:6]
        )
        reply = QMessageBox.question(
            self,
            "检测到 NapCat 仍在运行",
            "检测到当前项目的 NapCat/node 进程仍在运行。\n\n是否现在关闭它们？\n\n默认选择“否”，避免误关外部手动启动的 NapCat。\n\n" + detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            killed = kill_project_napcat_processes()
            QMessageBox.information(self, "NapCat 清理", f"已尝试关闭 {killed} 个当前项目 NapCat/node 进程。")

    def _monitor_finished(self, *args) -> None:
        self.status_label.setText("监控未启动")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _read_process_output(self) -> None:
        if not self.monitor_process:
            return
        raw = bytes(self.monitor_process.readAllStandardOutput())
        raw += bytes(self.monitor_process.readAllStandardError())
        data = self._decode_process_output(raw)
        if data:
            self.status_log.moveCursor(QTextCursor.MoveOperation.End)
            self.status_log.insertPlainText(data)
            self.status_log.moveCursor(QTextCursor.MoveOperation.End)

    def _decode_process_output(self, raw: bytes) -> str:
        if not raw:
            return ""
        for encoding in ("utf-8", "gbk"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        return ANSI_RE.sub("", text)

    def update_login_qr(self, *args) -> None:
        if not hasattr(self, "qr_label"):
            return
        qr_path = NAPCAT_DIR / "napcat" / "cache" / "qrcode.png"
        if not qr_path.exists():
            self.qr_mtime = None
            self.qr_label.setPixmap(QPixmap())
            self.qr_label.setText("等待 NapCat 生成二维码")
            return
        try:
            mtime = qr_path.stat().st_mtime
        except OSError:
            return
        if self.qr_mtime == mtime and self.qr_label.pixmap() is not None:
            return
        pixmap = QPixmap(str(qr_path))
        if pixmap.isNull():
            return
        self.qr_mtime = mtime
        self.qr_label.setText("")
        self.qr_label.setPixmap(
            pixmap.scaled(
                self.qr_label.size() - QSize(16, 16),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def open_login_qr(self, *args) -> None:
        qr_path = NAPCAT_DIR / "napcat" / "cache" / "qrcode.png"
        if qr_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(qr_path)))
        else:
            QMessageBox.information(self, "二维码", "NapCat 还没有生成二维码图片。")

    def refresh_media(self, *args) -> None:
        items = scan_media()
        # 更新日期下拉
        if hasattr(self, "media_date_filter"):
            all_dates = sorted(set(item.date for item in items), reverse=True)
            current_date = self.media_date_filter.currentText() if self.media_date_filter.count() > 0 else ""
            self.media_date_filter.blockSignals(True)
            self.media_date_filter.clear()
            self.media_date_filter.addItem("全部日期")
            for d in all_dates:
                self.media_date_filter.addItem(d)
            if current_date in all_dates:
                self.media_date_filter.setCurrentText(current_date)
            self.media_date_filter.blockSignals(False)
            selected_date = self.media_date_filter.currentText()
            if selected_date and selected_date != "全部日期":
                items = [item for item in items if item.date == selected_date]
        # 类型筛选
        filter_text = self.media_filter.currentText() if hasattr(self, "media_filter") else "全部"
        if filter_text == "图片":
            items = [item for item in items if item.media_type == "image"]
        elif filter_text == "视频":
            items = [item for item in items if item.media_type == "video"]
        current_paths = [str(item.path) for item in items]
        old_paths = [self.media_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.media_list.count())]
        if current_paths == old_paths and not args:
            return
        self.media_items = items
        self.media_list.clear()
        self.media_icon_queue = []
        self.video_thumb_queue = []
        self.video_thumb_current_row = None
        if hasattr(self, "video_thumb_player"):
            self.video_thumb_player.stop()
        for idx, item in enumerate(items):
            list_item = QListWidgetItem(item.path.name)
            list_item.setData(Qt.ItemDataRole.UserRole, str(item.path))
            if item.media_type in ("image", "video"):
                if idx < 30:
                    cache_key = (str(item.path), item.modified)
                    icon = self.media_icon_cache.get(cache_key)
                    if icon is None and item.media_type == "image":
                        pixmap = media_thumbnail_pixmap(item, QSize(128, 96))
                        icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
                        if not icon.isNull():
                            if len(self.media_icon_cache) > 500:
                                self.media_icon_cache.clear()
                            self.media_icon_cache[cache_key] = icon
                    if icon is None:
                        icon = QIcon()
                    if icon.isNull() and item.media_type == "video":
                        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
                        list_item.setData(MEDIA_ICON_READY_ROLE, False)
                        self.video_thumb_queue.append(idx)
                    else:
                        list_item.setData(MEDIA_ICON_READY_ROLE, True)
                else:
                    icon = self.style().standardIcon(
                        QStyle.StandardPixmap.SP_MediaPlay
                        if item.media_type == "video"
                        else QStyle.StandardPixmap.SP_FileIcon
                    )
                    list_item.setData(MEDIA_ICON_READY_ROLE, False)
                    if item.media_type == "video":
                        self.video_thumb_queue.append(idx)
                    else:
                        self.media_icon_queue.append(idx)
                if not icon.isNull():
                    list_item.setIcon(icon)
            else:
                list_item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                list_item.setData(MEDIA_ICON_READY_ROLE, True)
            self.media_list.addItem(list_item)
        self._schedule_media_icon_loading()
        QTimer.singleShot(250, self._start_next_video_thumbnail)
        QTimer.singleShot(0, self._prioritize_visible_media_icons)

    def _schedule_media_icon_loading(self) -> None:
        if self.media_icon_queue and hasattr(self, "media_icon_timer") and not self.media_icon_timer.isActive():
            self.media_icon_timer.start()

    def _prioritize_visible_media_icons(self, *args) -> None:
        if not hasattr(self, "media_list") or not self.media_icon_queue:
            return
        rect = self.media_list.viewport().rect()
        top_index = self.media_list.indexAt(rect.topLeft())
        bottom_index = self.media_list.indexAt(rect.bottomLeft())
        if not top_index.isValid():
            return
        top = max(0, top_index.row() - 4)
        bottom = bottom_index.row() if bottom_index.isValid() else min(self.media_list.count() - 1, top + 30)
        bottom = min(self.media_list.count() - 1, bottom + 12)
        visible_rows = [
            row for row in range(top, bottom + 1)
            if row in self.media_icon_queue
        ]
        visible_video_rows = [
            row for row in range(top, bottom + 1)
            if row in self.video_thumb_queue
        ]
        if visible_rows:
            rest = [row for row in self.media_icon_queue if row not in visible_rows]
            self.media_icon_queue = visible_rows + rest
        if visible_video_rows:
            rest = [row for row in self.video_thumb_queue if row not in visible_video_rows]
            self.video_thumb_queue = visible_video_rows + rest
            self._start_next_video_thumbnail()
        self._schedule_media_icon_loading()

    def _load_more_media_icons(self) -> None:
        if not self.media_icon_queue:
            self.media_icon_timer.stop()
            return
        batch_size = 6
        loaded = 0
        while self.media_icon_queue and loaded < batch_size:
            row = self.media_icon_queue.pop(0)
            if row < 0 or row >= self.media_list.count() or row >= len(self.media_items):
                continue
            list_item = self.media_list.item(row)
            if list_item.data(MEDIA_ICON_READY_ROLE):
                continue
            media_item = self.media_items[row]
            if media_item.media_type not in ("image", "video"):
                continue
            if media_item.media_type == "video":
                if row not in self.video_thumb_queue:
                    self.video_thumb_queue.append(row)
                self._start_next_video_thumbnail()
                loaded += 1
                continue
            cache_key = (str(media_item.path), media_item.modified)
            icon = self.media_icon_cache.get(cache_key)
            if icon is None:
                pixmap = media_thumbnail_pixmap(media_item, QSize(128, 96))
                icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
                if len(self.media_icon_cache) > 500:
                    self.media_icon_cache.clear()
                self.media_icon_cache[cache_key] = icon
            if not icon.isNull():
                list_item.setIcon(icon)
            list_item.setData(MEDIA_ICON_READY_ROLE, True)
            loaded += 1
        if not self.media_icon_queue:
            self.media_icon_timer.stop()

    def _start_next_video_thumbnail(self) -> None:
        if not hasattr(self, "video_thumb_player") or self.video_thumb_current_row is not None:
            return
        while self.video_thumb_queue:
            row = self.video_thumb_queue.pop(0)
            if row < 0 or row >= self.media_list.count() or row >= len(self.media_items):
                continue
            item = self.media_items[row]
            list_item = self.media_list.item(row)
            if item.media_type != "video" or list_item.data(MEDIA_ICON_READY_ROLE):
                continue
            cache_key = (str(item.path), item.modified)
            cached = self.media_icon_cache.get(cache_key)
            if cached and not cached.isNull():
                list_item.setIcon(cached)
                list_item.setData(MEDIA_ICON_READY_ROLE, True)
                continue
            self.video_thumb_current_row = row
            self.video_thumb_player.stop()
            self.video_thumb_player.setSource(QUrl.fromLocalFile(str(item.path)))
            self.video_thumb_player.setPosition(500)
            self.video_thumb_player.play()
            return
        self.video_thumb_current_row = None

    def _on_video_thumbnail_frame(self, frame) -> None:
        row = self.video_thumb_current_row
        if row is None:
            return
        image = frame.toImage()
        if image.isNull():
            return
        self.video_thumb_player.stop()
        self.video_thumb_current_row = None
        if row < self.media_list.count() and row < len(self.media_items):
            item = self.media_items[row]
            pixmap = QPixmap.fromImage(image).scaled(
                QSize(128, 96),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            pixmap = add_play_overlay(pixmap)
            icon = QIcon(pixmap)
            if not icon.isNull():
                self.media_list.item(row).setIcon(icon)
                self.media_list.item(row).setData(MEDIA_ICON_READY_ROLE, True)
                if len(self.media_icon_cache) > 500:
                    self.media_icon_cache.clear()
                self.media_icon_cache[(str(item.path), item.modified)] = icon
        QTimer.singleShot(0, self._start_next_video_thumbnail)

    def _selected_media_path(self) -> Path | None:
        items = self.media_list.selectedItems()
        if not items:
            return None
        return Path(items[0].data(Qt.ItemDataRole.UserRole))

    def preview_selected_media(self, *args) -> None:
        path = self._selected_media_path()
        if not path:
            return
        object_name = ""
        for item in self.media_items:
            if item.path == path:
                object_name = getattr(item, "object_name", "")
                break
        detail = f"文件名: {path.name}\n归档对象: {object_name or '-'}\n路径: {path}\n大小: {path.stat().st_size / 1024:.1f} KB"
        self.media_detail.setPlainText(detail)
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            self.media_player.stop()
            self.preview_stack.setCurrentIndex(0)
            selected_items = self.media_list.selectedItems()
            if selected_items and not selected_items[0].data(MEDIA_ICON_READY_ROLE):
                icon_pixmap = scaled_pixmap(path, QSize(128, 96))
                if not icon_pixmap.isNull():
                    selected_items[0].setIcon(QIcon(icon_pixmap))
                    selected_items[0].setData(MEDIA_ICON_READY_ROLE, True)
            pixmap = scaled_pixmap(path, self.preview_label.size())
            if not pixmap.isNull():
                self.preview_label.setPixmap(pixmap)
                return
        if suffix in VIDEO_SUFFIXES:
            selected_items = self.media_list.selectedItems()
            if selected_items and not selected_items[0].data(MEDIA_ICON_READY_ROLE):
                row = self.media_list.row(selected_items[0])
                if 0 <= row < len(self.media_items):
                    icon_pixmap = media_thumbnail_pixmap(self.media_items[row], QSize(128, 96))
                    if not icon_pixmap.isNull():
                        selected_items[0].setIcon(QIcon(icon_pixmap))
                        selected_items[0].setData(MEDIA_ICON_READY_ROLE, True)
                    elif row not in self.video_thumb_queue:
                        self.video_thumb_queue.insert(0, row)
                        self._start_next_video_thumbnail()
            self.preview_stack.setCurrentIndex(1)
            self.video_slider.setValue(0)
            self.media_player.setSource(QUrl.fromLocalFile(str(path)))
            if self.settings.get("app", {}).get("video_preview_autoplay", False):
                self.media_player.play()
                self.video_play_button.setText("暂停")
            else:
                self.media_player.pause()
                self.video_play_button.setText("播放")
            return
        self.media_player.stop()
        self.preview_stack.setCurrentIndex(0)
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("该文件暂不支持内嵌预览，可点击打开文件")

    def toggle_video_playback(self, *args) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def set_video_position(self, position: int) -> None:
        self.media_player.setPosition(position)

    def update_video_position(self, position: int) -> None:
        if not self.video_slider.isSliderDown():
            self.video_slider.setValue(position)

    def update_video_duration(self, duration: int) -> None:
        self.video_slider.setRange(0, max(0, duration))

    def update_video_button(self, state: QMediaPlayer.PlaybackState) -> None:
        self.video_play_button.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放")

    def open_selected_media(self, *args) -> None:
        path = self._selected_media_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_media_date_folder(self, *args) -> None:
        selected_path = self._selected_media_path()
        if selected_path and selected_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected_path.parent)))
            return
        if not hasattr(self, "media_date_filter"):
            return
        selected_date = self.media_date_filter.currentText()
        if not selected_date or selected_date == "全部日期":
            if self.media_items:
                selected_date = self.media_items[0].date
            else:
                return
        folder_path = DATA_ROOT / selected_date
        if folder_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))
        else:
            folder_path = DATA_ROOT / selected_date
            if folder_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))

    def open_webui(self, *args) -> None:
        port = self.webui_port_spin.value() if hasattr(self, "webui_port_spin") else self.settings["napcat"]["webui_port"]
        QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{port}"))

    def clear_log_display(self, *args) -> None:
        """仅清空日志显示区域，不清除日志文件。定时器下次触发时会重新加载最新内容"""
        # 暂停定时器防止在清空过程中刷新
        if hasattr(self, "log_timer") and self.log_timer.isActive():
            self.log_timer.stop()
        self.file_log.clear()
        # 记录清空标记，refresh_logs 先跳过加载
        self._log_cleared = True
        # 短暂延迟后恢复定时器，给用户看到清空效果
        QTimer.singleShot(500, self._restart_log_timer)

    def _restart_log_timer(self) -> None:
        if hasattr(self, "log_timer") and not self.log_timer.isActive():
            self.log_timer.start()

    def clear_log_file(self, *args) -> None:
        log_dir = DATA_ROOT / "logs"
        if not log_dir.exists():
            return
        reply = QMessageBox.question(self, "删除日志", "确定要删除所有日志文件吗？\n此操作不可撤销。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        import shutil
        for f in log_dir.glob("realtime_*.log"):
            try:
                f.unlink()
            except Exception:
                pass
        self.file_log.clear()
        QMessageBox.information(self, "已删除", "日志文件已删除。")

    def refresh_logs(self, *args) -> None:
        log_dir = DATA_ROOT / "logs"
        if not log_dir.exists() or not hasattr(self, "file_log"):
            return
        logs = sorted(log_dir.glob("realtime_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return
        try:
            text = logs[0].read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        current = self.file_log.toPlainText()
        # 如果刚被清空过，跳过本次加载，由下一次定时器正常刷新
        if getattr(self, "_log_cleared", False):
            self._log_cleared = False
            return
        # 计算当前滚动位置
        scrollbar = self.file_log.verticalScrollBar()
        at_bottom = True
        if scrollbar is not None:
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 5
        # 只追加新内容，避免整体替换导致滚动到顶部
        if text[-20000:] != current:
            self.file_log.setPlainText(text[-20000:])
            # 如果之前已在底部则保持滚动到底部，否则保持当前位置
            if at_bottom:
                cursor = self.file_log.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.file_log.setTextCursor(cursor)

    def show_normal(self, *args) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.activateWindow()

    def closeEvent(self, event) -> None:
        dont_ask = self.settings.get("_close_choice", "")
        if dont_ask == "minimize":
            event.ignore()
            self.hide()
            return
        if dont_ask == "quit":
            self.quit_app()
            return

        from PyQt6.QtWidgets import QCheckBox, QMessageBox
        cb = QCheckBox("不再询问，记住我的选择")
        msg = QMessageBox(self)
        msg.setWindowTitle("退出确认")
        msg.setText("关闭窗口后要执行什么操作？")
        msg.setInformativeText("最小化到托盘：程序在后台继续运行，双击托盘图标恢复。\n直接退出：完全关闭程序。")
        msg.setIcon(QMessageBox.Icon.Question)
        btn_minimize = msg.addButton("最小化到托盘", QMessageBox.ButtonRole.AcceptRole)
        btn_quit = msg.addButton("直接退出", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_minimize)
        msg.setCheckBox(cb)
        msg.exec()

        if msg.clickedButton() == btn_quit:
            if cb.isChecked():
                self.settings["_close_choice"] = "quit"
                from app.core.config import save_settings
                save_settings(self.settings)
            self.quit_app()
        else:
            if cb.isChecked():
                self.settings["_close_choice"] = "minimize"
                from app.core.config import save_settings
                save_settings(self.settings)
            event.ignore()
            self.hide()

    def quit_app(self, *args) -> None:
        self.stop_monitor()
        QApplication.quit()


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    return app.exec()
