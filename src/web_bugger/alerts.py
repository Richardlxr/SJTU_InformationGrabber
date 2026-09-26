"""
抓取失败告警 - 按页面跟踪连续失败次数，并决定何时发送告警邮件

规则：
  - 某个页面连续失败达到阈值（FAILURE_ALERT_THRESHOLD）后才算「持续失败」，
    偶发的单次超时不会触发告警；
  - 持续失败的页面若最近一个间隔（FAILURE_ALERT_INTERVAL）内没提醒过，就发告警 ——
    所以新出问题的页面会立即告警，不会被其他早已在报错的页面「掩盖」；
  - 同一页面每个间隔内最多提醒一次；页面时好时坏也不会绕过这个限制；
  - 告警发送失败时退避一段时间再重试，避免 SMTP 故障时每轮都去登录邮箱。

状态（每个页面的连续失败次数、上次提醒时间）持久化到 JSON 文件，
重启服务或 `--once` 定时运行时都不会丢失，因此时间使用墙上时钟（epoch 秒）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from web_bugger.storage import atomic_write_text

logger = logging.getLogger(__name__)

# 告警邮件发送失败后，至少等待这么久（秒）再重试
ALERT_RETRY_BACKOFF = 1800


class FailureTracker:
    """按页面跟踪连续抓取失败，并决定何时发送告警"""

    def __init__(self, path: Path | None, *, threshold: int, interval: int) -> None:
        """
        Args:
            path: 状态文件路径；为 None 时只保存在内存中
            threshold: 页面连续失败多少次后视为持续失败
            interval: 同一页面两次提醒之间的最小间隔（秒）
        """
        self._path = path
        self._threshold = max(1, threshold)
        self._interval = max(0, interval)
        self._streaks: dict[str, int] = {}
        self._alerted_at: dict[str, float] = {}
        self._retry_after: float | None = None
        self._load()
        self._saved = self._serialize()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def observe(self, failed_urls: Iterable[str]) -> dict[str, int]:
        """
        记录一轮抓取结果：失败页面连续次数 +1，其余页面归零。

        Returns:
            本轮恢复正常的页面 -> 恢复前的连续失败次数
        """
        failed = set(failed_urls)
        recovered = {u: n for u, n in self._streaks.items() if u not in failed}
        self._streaks = {u: self._streaks.get(u, 0) + 1 for u in failed}
        return recovered

    def streak(self, url: str) -> int:
        """页面当前的连续失败次数"""
        return self._streaks.get(url, 0)

    def failing(self) -> list[str]:
        """连续失败次数已达阈值的页面（按 URL 排序）"""
        return sorted(u for u, n in self._streaks.items() if n >= self._threshold)

    def is_new(self, url: str) -> bool:
        """页面在本次故障中尚未被提醒过（已恢复且超过间隔的提醒记录会被清理）"""
        return url not in self._alerted_at

    def alert_due(self, now: float) -> bool:
        """此刻是否应该发送告警"""
        # 时钟回拨导致剩余退避时间超过 ALERT_RETRY_BACKOFF 时视为已到期
        if self._retry_after is not None and 0 < self._retry_after - now <= ALERT_RETRY_BACKOFF:
            return False
        return any(self._expired(u, now) for u in self.failing())

    def mark_alerted(self, urls: Iterable[str], now: float) -> None:
        """告警发送成功：记录提醒时间，并清理过期记录"""
        for url in urls:
            self._alerted_at[url] = now
        self._retry_after = None
        self._alerted_at = {
            u: t
            for u, t in self._alerted_at.items()
            if u in self._streaks or not self._expired(u, now)
        }

    def mark_send_failed(self, now: float) -> None:
        """告警发送失败：退避一段时间后再重试"""
        self._retry_after = now + ALERT_RETRY_BACKOFF

    def save(self) -> None:
        """持久化状态（内容没变则跳过；失败只记日志，不影响主流程）"""
        if self._path is None:
            return
        text = self._serialize()
        if text == self._saved:
            return
        try:
            atomic_write_text(self._path, text)
        except OSError as e:
            logger.error("保存告警状态文件失败（%s）: %s", self._path, e)
            return
        self._saved = text

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _serialize(self) -> str:
        payload = {
            "streaks": self._streaks,
            "alerted_at": self._alerted_at,
            "retry_after": self._retry_after,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    def _expired(self, url: str, now: float) -> bool:
        """距上次提醒是否已满一个间隔（时钟回拨时视为已满，宁可多提醒一次）"""
        last = self._alerted_at.get(url)
        if last is None:
            return True
        elapsed = now - last
        return elapsed < 0 or elapsed >= self._interval

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("读取告警状态文件失败（%s），将从空状态开始: %s", self._path, e)
            return
        if not isinstance(data, dict):
            logger.warning("告警状态文件格式异常（%s），将从空状态开始", self._path)
            return

        streaks = data.get("streaks")
        if isinstance(streaks, dict):
            self._streaks = {
                u: n
                for u, n in streaks.items()
                if isinstance(u, str) and isinstance(n, int) and not isinstance(n, bool) and n > 0
            }
        alerted_at = data.get("alerted_at")
        if isinstance(alerted_at, dict):
            self._alerted_at = {
                u: float(t)
                for u, t in alerted_at.items()
                if isinstance(u, str) and isinstance(t, (int, float)) and not isinstance(t, bool)
            }
        retry_after = data.get("retry_after")
        if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
            self._retry_after = float(retry_after)
