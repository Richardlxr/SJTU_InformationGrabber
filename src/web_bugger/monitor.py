"""
监控器 - 协调爬虫、存储、通知三大组件的核心编排类
"""

from __future__ import annotations

import logging
import time

from web_bugger.config import AppConfig
from web_bugger.models import Announcement
from web_bugger.notifier import Notifier
from web_bugger.scraper import Scraper
from web_bugger.storage import Storage

logger = logging.getLogger(__name__)


class Monitor:
    """
    公告监控器 —— 单次检查 / 初始化 / 守护运行的统一入口。

    Usage::

        config = AppConfig.from_env()
        monitor = Monitor(config)
        monitor.init()            # 首次初始化
        monitor.check_once()      # 单次检查
        monitor.run()             # 持续守护
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._scraper = Scraper(config.scraper)
        self._storage = Storage(config.seen_file)
        self._notifier = Notifier(config.smtp)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def init(self) -> None:
        """首次初始化：抓取当前所有公告并标记为已读，不发送邮件。"""
        announcements = self._scraper.fetch()
        if not announcements:
            logger.warning("未抓取到任何公告，初始化中止")
            return
        self._storage.mark_seen(announcements)
        logger.info("初始化完成：将当前 %d 条公告全部标记为已读", len(announcements))

    def check_once(self, *, dry_run: bool = False) -> int:
        """
        执行一次检查。

        Args:
            dry_run: 为 True 时不发送邮件，仅打印

        Returns:
            新公告数量
        """
        announcements = self._scraper.fetch()
        if not announcements:
            logger.warning("未抓取到任何公告，可能是网络问题或页面结构变化")
            return 0

        new = self._storage.filter_new(announcements)
        if not new:
            logger.info("没有新公告")
            return 0

        self._log_new(new)

        if dry_run:
            logger.info("Dry-run 模式，跳过邮件发送")
        else:
            if not self._notifier.send(new):
                logger.error("邮件发送失败，不标记已读，下次重试")
                return len(new)

        self._storage.mark_seen(new)
        return len(new)

    def run(self, *, dry_run: bool = False) -> None:
        """持续运行守护，定时检查并通知。"""
        interval = self._config.check_interval
        logger.info("启动守护模式，每 %d 秒检查一次（Ctrl+C 停止）", interval)

        while True:
            try:
                self.check_once(dry_run=dry_run)
            except Exception:
                logger.exception("检查过程中出错")
            logger.info("等待 %d 秒后再次检查...", interval)
            time.sleep(interval)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    @staticmethod
    def _log_new(items: list[Announcement]) -> None:
        logger.info("发现 %d 条新公告:", len(items))
        for a in items:
            logger.info("  [%s] %s (%s)", a.section, a.title, a.date)
