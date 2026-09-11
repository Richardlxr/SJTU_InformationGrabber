"""
邮件通知模块 - 通过 SMTP 发送新公告提醒邮件
"""

from __future__ import annotations

import contextlib
import html
import logging
import smtplib
import ssl
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
        发送新公告通知邮件。

        Args:
            announcements: 新公告列表

        Returns:
            True 发送成功 / False 发送失败（失败时不会抛出异常）
        """
        if not announcements:
            return True

        if not self._config.is_configured:
            logger.error("发件人邮箱或授权码未配置，请检查 .env 文件")
            return False

        return self._deliver(self._build_message(announcements))

    def send_alert(self, subject: str, text: str) -> bool:
        """
        发送纯文本告警邮件（例如连续多次抓取失败）。

        Returns:
            True 发送成功 / False 发送失败
        """
        if not self._config.is_configured:
            logger.error("发件人邮箱或授权码未配置，无法发送告警邮件")
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = self._config.sender_email
        msg["To"] = ", ".join(self._config.recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain", "utf-8"))
        return self._deliver(msg)

    # ------------------------------------------------------------------
    # message building
    # ------------------------------------------------------------------

    def _build_message(self, announcements: list[Announcement]) -> MIMEMultipart:
        count = len(announcements)
        subject = f"【交大教务处】有 {count} 条新公告"

        msg = MIMEMultipart("alternative")
        msg["From"] = self._config.sender_email
        msg["To"] = ", ".join(self._config.recipients)
        msg["Subject"] = subject

        msg.attach(MIMEText(self._render_text(announcements), "plain", "utf-8"))
        msg.attach(MIMEText(self._render_html(announcements), "html", "utf-8"))
        return msg

    # ------------------------------------------------------------------
    # delivery
    # ------------------------------------------------------------------

    def _deliver(self, msg: MIMEMultipart) -> bool:
        """
        投递邮件。

        注意：除了 smtplib.SMTPException 之外，DNS 解析失败（gaierror）、
        连接被拒（ConnectionRefusedError）、超时（TimeoutError）、TLS 失败
        （ssl.SSLError）都继承自 OSError，因此必须一并捕获，否则
        `--once` 模式会直接崩溃，daemon 模式也会丢掉本轮通知。
        """
        cfg = self._config
        server: smtplib.SMTP | None = None
        try:
            if cfg.use_ssl:
                server = smtplib.SMTP_SSL(cfg.server, cfg.port, timeout=cfg.timeout)
            else:
                server = smtplib.SMTP(cfg.server, cfg.port, timeout=cfg.timeout)
                server.starttls(context=ssl.create_default_context())

            server.login(cfg.sender_email, cfg.sender_password)
            server.sendmail(cfg.sender_email, cfg.recipients, msg.as_string())
            logger.info("邮件发送成功 -> %s", ", ".join(cfg.recipients))
            return True
        except (smtplib.SMTPException, OSError) as e:
            logger.error("邮件发送失败: %s: %s", type(e).__name__, e)
            return False
        finally:
            if server is not None:
                with contextlib.suppress(smtplib.SMTPException, OSError):
                    server.quit()

    # ------------------------------------------------------------------
    # templates
    # ------------------------------------------------------------------

    @staticmethod
    def _render_html(announcements: list[Announcement]) -> str:
        esc = html.escape
        total = len(announcements)
        rows = ""
        for a in announcements:
            rows += (
                '<tr><td style="padding:8px;border-bottom:1px solid #eee;">'
                f'<span style="color:#888;font-size:12px;">[{esc(a.section)}]</span><br>'
                f'<a href="{esc(a.url, quote=True)}" '
                'style="color:#1a73e8;text-decoration:none;font-size:14px;">'
                f"{esc(a.title)}</a><br>"
                f'<span style="color:#999;font-size:12px;">{esc(a.date)}</span>'
                "</td></tr>"
            )

        return (
            "<html><body style=\"font-family:'Microsoft YaHei',Arial,sans-serif;padding:20px;\">"
            '<h2 style="color:#333;">📢 上海交通大学 - 新公告通知</h2>'
            f'<p style="color:#666;">检测到以下 <strong>{total}</strong> 条新公告：</p>'
            f'<table style="width:100%;border-collapse:collapse;">{rows}</table>'
            '<hr style="margin-top:20px;border:none;border-top:1px solid #ddd;">'
            '<p style="color:#999;font-size:12px;">'
            '来源: <a href="https://jwc.sjtu.edu.cn/xwtg.htm">'
            "https://jwc.sjtu.edu.cn/xwtg.htm</a></p>"
            "</body></html>"
        )

    @staticmethod
    def _render_text(announcements: list[Announcement]) -> str:
        lines = ["上海交通大学 - 新公告通知", "=" * 40, ""]
        for a in announcements:
            lines.append(f"[{a.section}] {a.title}")
            lines.append(f"  日期: {a.date}")
            lines.append(f"  链接: {a.url}")
            lines.append("")
        lines.append("来源: https://jwc.sjtu.edu.cn/xwtg.htm")
        return "\n".join(lines)
