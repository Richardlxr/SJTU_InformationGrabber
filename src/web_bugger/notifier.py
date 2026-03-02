"""
邮件通知模块 - 通过 SMTP 发送新公告提醒邮件
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from web_bugger.config import SmtpConfig
from web_bugger.models import Announcement

logger = logging.getLogger(__name__)


class Notifier:
    """邮件通知器"""

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def send(self, announcements: list[Announcement]) -> bool:
        """
        发送邮件通知。

        Args:
            announcements: 新公告列表

        Returns:
            True 发送成功 / False 发送失败
        """
        if not announcements:
            return True

        if not self._config.is_configured:
            logger.error("发件人邮箱或授权码未配置，请检查 .env 文件")
            return False

        msg = self._build_message(announcements)
        return self._deliver(msg)

    # ------------------------------------------------------------------
    # message building
    # ------------------------------------------------------------------

    def _build_message(self, announcements: list[Announcement]) -> MIMEMultipart:
        count = len(announcements)
        subject = f"【交大教务处】有 {count} 条新公告"

        msg = MIMEMultipart("alternative")
        msg["From"] = self._config.sender_email
        msg["To"] = self._config.receiver_email
        msg["Subject"] = subject

        msg.attach(MIMEText(self._render_text(announcements), "plain", "utf-8"))
        msg.attach(MIMEText(self._render_html(announcements), "html", "utf-8"))
        return msg

    # ------------------------------------------------------------------
    # delivery
    # ------------------------------------------------------------------

    def _deliver(self, msg: MIMEMultipart) -> bool:
        cfg = self._config
        try:
            if cfg.use_ssl:
                server = smtplib.SMTP_SSL(cfg.server, cfg.port, timeout=15)
            else:
                server = smtplib.SMTP(cfg.server, cfg.port, timeout=15)
                server.starttls()

            server.login(cfg.sender_email, cfg.sender_password)
            server.sendmail(cfg.sender_email, cfg.receiver_email, msg.as_string())
            server.quit()
            logger.info("邮件发送成功")
            return True
        except smtplib.SMTPException as e:
            logger.error("邮件发送失败: %s", e)
            return False

    # ------------------------------------------------------------------
    # templates
    # ------------------------------------------------------------------

    @staticmethod
    def _render_html(announcements: list[Announcement]) -> str:
        rows = ""
        for a in announcements:
            rows += (
                '<tr><td style="padding:8px;border-bottom:1px solid #eee;">'
                f'<span style="color:#888;font-size:12px;">[{a.section}]</span><br>'
                f'<a href="{a.url}" style="color:#1a73e8;text-decoration:none;font-size:14px;">'
                f"{a.title}</a><br>"
                f'<span style="color:#999;font-size:12px;">{a.date}</span>'
                "</td></tr>"
            )

        return (
            "<html><body style=\"font-family:'Microsoft YaHei',Arial,sans-serif;padding:20px;\">"
            '<h2 style="color:#333;">📢 上海交通大学教务处 - 新公告通知</h2>'
            f'<p style="color:#666;">检测到以下 <strong>{len(announcements)}</strong> 条新公告：</p>'
            f'<table style="width:100%;border-collapse:collapse;">{rows}</table>'
            '<hr style="margin-top:20px;border:none;border-top:1px solid #ddd;">'
            '<p style="color:#999;font-size:12px;">'
            '来源: <a href="https://jwc.sjtu.edu.cn/xwtg.htm">https://jwc.sjtu.edu.cn/xwtg.htm</a>'
            "</p></body></html>"
        )

    @staticmethod
    def _render_text(announcements: list[Announcement]) -> str:
        lines = ["上海交通大学教务处 - 新公告通知", "=" * 40, ""]
        for a in announcements:
            lines.append(f"[{a.section}] {a.title}")
            lines.append(f"  日期: {a.date}")
            lines.append(f"  链接: {a.url}")
            lines.append("")
        lines.append("来源: https://jwc.sjtu.edu.cn/xwtg.htm")
        return "\n".join(lines)
