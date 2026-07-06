from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import DATA_ROOT


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".suf"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}


@dataclass(frozen=True)
class MediaItem:
    path: Path
    media_type: str
    date: str
    object_name: str
    size: int
    modified: float


def _media_type_for_path(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _iter_media_files(date_dir: Path):
    # New layout: ALL_Fold/date/object/image|video/file
    for object_dir in date_dir.iterdir():
        if not object_dir.is_dir():
            continue
        if object_dir.name in {"logs", "errors"}:
            continue
        if object_dir.name == "media":
            # Backward compatibility for the old layout.
            for media_type_dir in object_dir.iterdir():
                if media_type_dir.is_dir():
                    for path in media_type_dir.rglob("*"):
                        yield path, "media"
            continue
        for media_type_dir in object_dir.iterdir():
            if media_type_dir.is_dir() and media_type_dir.name.lower() in {"image", "video"}:
                for path in media_type_dir.rglob("*"):
                    yield path, object_dir.name


def list_media_dates(data_root: Path = DATA_ROOT) -> list[str]:
    if not data_root.exists():
        return []
    return sorted(
        [p.name for p in data_root.iterdir() if p.is_dir() and p.name not in {"logs", "errors"}],
        reverse=True,
    )


def scan_media(data_root: Path = DATA_ROOT, date_filter: str | None = None) -> list[MediaItem]:
    items: list[MediaItem] = []
    if not data_root.exists():
        return items
    date_dirs = []
    if date_filter and date_filter != "全部日期":
        p = data_root / date_filter
        if p.is_dir():
            date_dirs = [p]
    else:
        date_dirs = [p for p in data_root.iterdir() if p.is_dir() and p.name not in {"logs", "errors"}]
    for date_dir in date_dirs:
        for path, object_name in _iter_media_files(date_dir):
            if not path.is_file():
                continue
            media_type = _media_type_for_path(path)
            if not media_type:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append(
                MediaItem(
                    path=path,
                    media_type=media_type,
                    date=date_dir.name,
                    object_name=object_name,
                    size=stat.st_size,
                    modified=stat.st_mtime,
                )
            )
    items.sort(key=lambda item: item.modified, reverse=True)
    return items
