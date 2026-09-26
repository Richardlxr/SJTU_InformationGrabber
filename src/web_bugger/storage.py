"""
持久化存储模块 - 用 JSON 文件记录已见过的公告，避免重复通知
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

from web_bugger.models import Announcement

logger = logging.getLogger(__name__)


class Storage:
    """基于 JSON 文件的已读公告存储

    文件内容是一个 URL 字符串数组。写入采用「临时文件 + os.replace」原子替换，
    避免进程被强杀/断电时把文件截断成空，导致下次运行把所有公告当成新公告重发。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: set[str] = self._load()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

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
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("读取已存储公告文件失败（%s），本次视为空: %s", self._path, e)
            return set()

        if not raw.strip():
            # 空文件 = 尚无记录（正常首次运行），不要当成损坏文件
            return set()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("已存储公告文件不是合法 JSON（%s）: %s", self._path, e)
            self._quarantine()
            return set()

        if not isinstance(data, list):
            logger.error(
                "已存储公告文件格式异常（期望 JSON 数组，实际为 %s），已忽略（%s）",
                type(data).__name__,
                self._path,
            )
            self._quarantine()
            return set()

        urls = {item for item in data if isinstance(item, str) and item}
        skipped = len(data) - len(urls)
        if skipped:
            logger.warning("已存储公告文件中 %d 条非字符串记录已被忽略", skipped)
        return urls

    def _quarantine(self) -> None:
        """把损坏的状态文件改名备份，避免静默丢弃后又被下一次写入覆盖"""
        backup = self._path.with_suffix(self._path.suffix + ".corrupt")
        try:
            os.replace(self._path, backup)
            logger.error("损坏的状态文件已备份为 %s", backup)
        except OSError as e:
            logger.warning("备份损坏的状态文件失败: %s", e)

    def _save(self) -> None:
        payload = json.dumps(sorted(self._seen), ensure_ascii=False, indent=2)
        try:
            atomic_write_text(self._path, payload)
        except OSError as e:
            logger.error("保存已存储公告文件失败: %s", e)


def atomic_write_text(path: Path, text: str) -> None:
    """
    以「临时文件 + fsync + os.replace」原子写入文本文件。

    Raises:
        OSError: 写入失败（临时文件会被清理，原文件保持不变）
    """
    tmp_path: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
