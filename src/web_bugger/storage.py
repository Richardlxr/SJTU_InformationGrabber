"""
持久化存储模块 - 用 JSON 文件记录已见过的公告，避免重复通知
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from web_bugger.models import Announcement

logger = logging.getLogger(__name__)


class Storage:
    """基于 JSON 文件的已读公告存储"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: set[str] = self._load()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def seen_urls(self) -> frozenset[str]:
        """当前所有已读 URL（只读视图）"""
        return frozenset(self._seen)

    def is_seen(self, announcement: Announcement) -> bool:
        return announcement.url in self._seen

    def filter_new(self, announcements: list[Announcement]) -> list[Announcement]:
        """过滤出尚未见过的公告"""
        return [a for a in announcements if not self.is_seen(a)]

    def mark_seen(self, announcements: list[Announcement]) -> None:
        """将给定公告标记为已读并持久化"""
        for a in announcements:
            self._seen.add(a.url)
        self._save()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _load(self) -> set[str]:
        if not self._path.exists():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return set(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取已存储公告文件失败，将重新创建: %s", e)
            return set()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(sorted(self._seen), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("保存已存储公告文件失败: %s", e)
