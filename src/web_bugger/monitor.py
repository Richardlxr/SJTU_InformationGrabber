"""
监控器 - 协调爬虫、存储、通知三大组件的核心编排类
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from web_bugger.alerts import FailureTracker
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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._scraper: ScraperLike = scraper or Scraper(config.scraper)
        self._storage: StorageLike = storage or Storage(config.seen_file)
        self._notifier: NotifierLike = notifier or Notifier(config.smtp)
        # 告警状态会持久化，跨进程比较时间，所以用墙上时钟而非 monotonic
        self._clock = clock
        self._failures = FailureTracker(
            config.alert_state_file,
            threshold=config.failure_alert_threshold,
            interval=config.failure_alert_interval,
        )

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

        new = self._enrich(new)
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
        """按页面记录连续抓取失败，需要时发送告警邮件（规则见 web_bugger.alerts）"""
        tracker = self._failures
        for url, count in tracker.observe(result.failed_urls).items():
            logger.info("页面已恢复正常: %s（此前连续失败 %d 次）", url, count)
        if result.failed_urls:
            logger.warning(
                "连续失败次数: %s",
                ", ".join(f"{u} ×{tracker.streak(u)}" for u in result.failed_urls),
            )
        if dry_run:
            return  # dry-run 不发告警、也不写状态文件

        now = self._clock()
        if tracker.alert_due(now):
            failing = tracker.failing()
            if self._notifier.send_alert(*self._build_alert(result, failing)):
                tracker.mark_alerted(failing, now)
            else:
                tracker.mark_send_failed(now)
        tracker.save()

    def _build_alert(self, result: FetchResult, failing: list[str]) -> tuple[str, str]:
        """构造告警邮件的（主题, 正文）"""
        tracker = self._failures
        lines = [
            f"  - {u}（连续失败 {tracker.streak(u)} 次）"
            + ("【新增】" if tracker.is_new(u) else "")
            for u in failing
        ]
        if result.all_failed:
            impact = "所有页面均抓取失败，期间不会有任何新公告通知，请检查服务器网络。"
        else:
            impact = (
                "其余页面的新公告仍会正常通知；上述页面恢复后，"
                "其间发布且仍在列表中的公告会补发通知。"
            )
        subject = f"【交大信息监控】{len(failing)} 个页面持续抓取失败"
        body = (
            f"以下页面持续抓取失败（{len(failing)}/{result.pages_total}），"
            "请检查网络或页面结构：\n"
            + "\n".join(lines)
            + f"\n\n{impact}\n本次成功抓取到的公告数：{len(result.items)}\n\n"
            f"同一页面持续失败时，每 {_humanize_seconds(self._config.failure_alert_interval)} "
            "最多提醒一次；新出问题的页面会立即提醒；页面恢复后不会另发邮件。\n"
        )
        return subject, body

    def _enrich(self, items: list[Announcement]) -> list[Announcement]:
        """发送前补全摘要等详情；出任何问题都退回原始条目，绝不影响通知"""
        enrich = getattr(self._scraper, "enrich", None)
        if not callable(enrich):
            return items
        try:
            enriched = enrich(items)
        except Exception:
            logger.exception("补全公告详情时出错，将发送不含摘要的邮件")
            return items
        return list(enriched)

    @staticmethod
    def _log_new(items: list[Announcement]) -> None:
        logger.info("发现 %d 条新公告:", len(items))
        for a in items:
            label = "·".join(part for part in (a.source, a.section) if part)
            logger.info("  [%s] %s (%s)", label, a.title, a.date)


def _humanize_seconds(seconds: int) -> str:
    """86400 -> '1 天'，7200 -> '2 小时'，90 -> '90 秒'"""
    for unit, name in ((86400, "天"), (3600, "小时"), (60, "分钟")):
        if seconds >= unit and seconds % unit == 0:
            return f"{seconds // unit} {name}"
    return f"{seconds} 秒"
