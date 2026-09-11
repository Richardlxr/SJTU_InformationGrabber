"""
监控器 - 协调爬虫、存储、通知三大组件的核心编排类
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from web_bugger.config import AppConfig
from web_bugger.models import Announcement
from web_bugger.notifier import Notifier
from web_bugger.scraper import FetchResult, Scraper
from web_bugger.storage import Storage

logger = logging.getLogger(__name__)


class ScraperLike(Protocol):
    def fetch_with_status(self) -> FetchResult: ...


class StorageLike(Protocol):
    def filter_new(self, announcements: list[Announcement]) -> list[Announcement]: ...

    def mark_seen(self, announcements: list[Announcement]) -> None: ...


class NotifierLike(Protocol):
    def send(self, announcements: list[Announcement]) -> bool: ...

    def send_alert(self, subject: str, text: str) -> bool: ...


class Monitor:
    """
    公告监控器 —— 单次检查 / 初始化 / 守护运行的统一入口。

    Usage::

        config = AppConfig.from_env()
        monitor = Monitor(config)
        monitor.init()            # 首次初始化
        monitor.check_once()      # 单次检查
        monitor.run()             # 持续守护

    三个协作者都可以注入，便于测试与复用。
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        scraper: ScraperLike | None = None,
        storage: StorageLike | None = None,
        notifier: NotifierLike | None = None,
    ) -> None:
        self._config = config
        self._scraper: ScraperLike = scraper or Scraper(config.scraper)
        self._storage: StorageLike = storage or Storage(config.seen_file)
        self._notifier: NotifierLike = notifier or Notifier(config.smtp)
        self._consecutive_failures = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def init(self) -> bool:
        """
        首次初始化：抓取当前所有公告并标记为已读，不发送邮件。

        Returns:
            True 初始化成功；False 未抓取到任何公告（未写入任何状态）
        """
        result = self._scraper.fetch_with_status()
        if not result.items:
            logger.error(
                "未抓取到任何公告，初始化中止（失败页面: %s）",
                ", ".join(result.failed_urls) or "无",
            )
            return False

        self._storage.mark_seen(result.items)
        logger.info("初始化完成：将当前 %d 条公告全部标记为已读", len(result.items))
        if result.failed_urls:
            logger.warning(
                "注意：%d 个页面本次抓取失败，其公告未被标记，"
                "下次运行可能会通知这些页面上的全部内容: %s",
                len(result.failed_urls),
                ", ".join(result.failed_urls),
            )
        return True

    def check_once(self, *, dry_run: bool = False) -> int:
        """
        执行一次检查。

        Args:
            dry_run: 为 True 时不发送邮件、也不写入已读状态（完全无副作用）

        Returns:
            本次发现的新公告数量
        """
        result = self._scraper.fetch_with_status()
        self._track_failures(result, dry_run=dry_run)

        if not result.items:
            logger.warning(
                "未抓取到任何公告，可能是网络问题或页面结构变化（失败页面: %s）",
                ", ".join(result.failed_urls) or "无",
            )
            return 0

        new = self._storage.filter_new(result.items)
        if not new:
            logger.info("没有新公告")
            return 0

        self._log_new(new)

        if dry_run:
            # 关键：dry-run 必须无副作用，否则会静默吞掉这批通知
            logger.info("Dry-run 模式：仅打印，不发送邮件、不写入已读状态")
            return len(new)

        if not self._notifier.send(new):
            logger.error("邮件发送失败，不标记已读，下次重试")
            return len(new)

        self._storage.mark_seen(new)
        logger.info("已将 %d 条新公告标记为已读", len(new))
        return len(new)

    def run(self, *, dry_run: bool = False) -> None:
        """持续运行守护，定时检查并通知（按固定节拍调度，不累计漂移）。"""
        interval = self._config.check_interval
        logger.info("启动守护模式，每 %d 秒检查一次（Ctrl+C 停止）", interval)
        if dry_run:
            logger.warning("Dry-run 模式：不会发送任何邮件，也不会记录已读状态")

        while True:
            started = time.monotonic()
            try:
                self.check_once(dry_run=dry_run)
            except Exception:
                logger.exception("检查过程中出错")
            elapsed = time.monotonic() - started
            sleep_for = max(1.0, interval - elapsed)
            logger.info("等待 %.0f 秒后再次检查...", sleep_for)
            time.sleep(sleep_for)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """释放爬虫连接池（可重复调用）"""
        close = getattr(self._scraper, "close", None)
        if callable(close):
            close()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _track_failures(self, result: FetchResult, *, dry_run: bool) -> None:
        """连续抓取失败达到阈值时发送告警邮件"""
        if result.ok:
            if self._consecutive_failures:
                logger.info("抓取已恢复正常（此前连续失败 %d 次）", self._consecutive_failures)
            self._consecutive_failures = 0
            return

        self._consecutive_failures += 1
        logger.warning(
            "第 %d 次连续出现抓取失败（%d/%d 个页面）",
            self._consecutive_failures,
            len(result.failed_urls),
            result.pages_total,
        )

        threshold = max(1, self._config.failure_alert_threshold)
        should_alert = (
            self._consecutive_failures >= threshold
            and (self._consecutive_failures - threshold) % threshold == 0
        )
        if not should_alert or dry_run:
            return

        subject = f"【交大信息监控】连续 {self._consecutive_failures} 次抓取失败"
        body = (
            f"公告监控已连续 {self._consecutive_failures} 次抓取失败，"
            "期间不会有任何新公告通知，请检查网络或页面结构。\n\n"
            f"失败的页面（{len(result.failed_urls)}/{result.pages_total}）：\n"
            + "\n".join(f"  - {u}" for u in result.failed_urls)
            + "\n\n本次成功抓取到的公告数："
            f"{len(result.items)}\n"
        )
        self._notifier.send_alert(subject, body)

    @staticmethod
    def _log_new(items: list[Announcement]) -> None:
        logger.info("发现 %d 条新公告:", len(items))
        for a in items:
            logger.info("  [%s] %s (%s)", a.section, a.title, a.date)
