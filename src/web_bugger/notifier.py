"""
邮件通知模块 - 通过 SMTP 发送新公告提醒邮件
"""

from __future__ import annotations

import contextlib
import html
import logging
import smtplib
import ssl
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlsplit

from web_bugger.config import SmtpConfig
from web_bugger.models import Announcement

logger = logging.getLogger(__name__)

# 各来源站点的主题色：(强调色, 浅底色)
_SOURCE_COLORS: dict[str, tuple[str, str]] = {
    "教务处": ("#b4232a", "#fdeeee"),
    "计算机学院": ("#1d4ed8", "#e9f0ff"),
    "致远学院": ("#0b3b7a", "#e7eef8"),
}
_DEFAULT_COLORS = ("#475569", "#eef2f6")
# 条目没有标注来源时按域名推断
_HOST_SOURCES = {
    "jwc.sjtu.edu.cn": "教务处",
    "cs.sjtu.edu.cn": "计算机学院",
    "zhiyuan.sjtu.edu.cn": "致远学院",
}
_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei','Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
_SUBJECT_TITLE_MAX = 40


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

    def _build_message(
        self, announcements: list[Announcement], now: datetime | None = None
    ) -> MIMEMultipart:
        now = now or datetime.now()
        msg = MIMEMultipart("alternative")
        msg["From"] = self._config.sender_email
        msg["To"] = ", ".join(self._config.recipients)
        msg["Subject"] = self._subject(announcements)

        msg.attach(MIMEText(self._render_text(announcements, now), "plain", "utf-8"))
        msg.attach(MIMEText(self._render_html(announcements, now), "html", "utf-8"))
        return msg

    @staticmethod
    def _subject(announcements: list[Announcement]) -> str:
        """如「【交大信息监控】3 条新公告：关于……的通知 等」，收件箱列表里就能看到首条标题"""
        ordered = [a for _, group in _group_by_source(announcements) for a in group]
        if not ordered:
            return "【交大信息监控】0 条新公告"
        first = _truncate(ordered[0].title, _SUBJECT_TITLE_MAX)
        more = " 等" if len(ordered) > 1 else ""
        return f"【交大信息监控】{len(ordered)} 条新公告：{first}{more}"

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
    def _render_html(announcements: list[Announcement], now: datetime | None = None) -> str:
        """
        邮件 HTML。邮件客户端对 CSS 支持有限，因此全部使用表格布局 + 内联样式，
        不依赖外部资源；所有来自网页的内容都经过转义。
        """
        now = now or datetime.now()
        esc = html.escape
        groups = _group_by_source(announcements)
        pills = "".join(
            '<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 10px;'
            "border-radius:999px;background-color:#25476f;color:#ffffff;font-size:13px;"
            f'line-height:1.6;">{esc(source)} <b>{len(items)}</b></span>'
            for source, items in groups
        )
        preheader = esc(_truncate("；".join(a.title for _, g in groups for a in g), 150))
        body = "".join(_render_group(source, items, now.date()) for source, items in groups)
        home_links = " · ".join(
            f'<a href="{esc(_home_url(items[0].url), quote=True)}" '
            f'style="color:#64748b;text-decoration:underline;">{esc(source)}</a>'
            for source, items in groups
        )
        return (
            '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="color-scheme" content="light">'
            '<meta name="supported-color-schemes" content="light">'
            "<title>交大信息监控</title></head>"
            '<body style="margin:0;padding:0;background-color:#eef1f6;">'
            '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
            f"{preheader}</div>"
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background-color:#eef1f6;"><tr><td align="center" style="padding:24px 12px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="max-width:640px;background-color:#ffffff;border-radius:14px;'
            f'overflow:hidden;font-family:{_FONT_STACK};">'
            # 头部：总数 + 各来源条数
            '<tr><td style="background-color:#0f2a4a;padding:28px 28px 22px;">'
            '<div style="font-size:13px;letter-spacing:2px;color:#9fb3d1;">交大信息监控</div>'
            '<div style="margin-top:8px;font-size:26px;line-height:1.3;font-weight:700;'
            f'color:#ffffff;">发现 {len(announcements)} 条新公告</div>'
            f'<div style="margin-top:14px;">{pills}</div>'
            '<div style="margin-top:8px;font-size:12px;color:#9fb3d1;">'
            f"{now:%Y-%m-%d %H:%M} 检查</div>"
            "</td></tr>"
            f"{body}"
            # 页脚
            '<tr><td style="padding:12px 28px 28px;">'
            '<div style="border-top:1px solid #eef1f5;padding-top:16px;font-size:12px;'
            'line-height:1.8;color:#94a3b8;">'
            "由 web-bugger 自动发送 · 摘要仅供预览，内容以各官网原文为准<br>"
            f"本次来源：{home_links or '无'}"
            "</div></td></tr>"
            "</table></td></tr></table></body></html>"
        )

    @staticmethod
    def _render_text(announcements: list[Announcement], now: datetime | None = None) -> str:
        """纯文本版本（不显示 HTML 的客户端 / 通知预览使用）"""
        now = now or datetime.now()
        groups = _group_by_source(announcements)
        overview = " · ".join(f"{source} {len(items)}" for source, items in groups)
        lines = [
            f"交大信息监控 · 发现 {len(announcements)} 条新公告",
            f"{now:%Y-%m-%d %H:%M} 检查" + (f" · {overview}" if overview else ""),
            "",
        ]
        for source, items in groups:
            lines += [f"【{source}】{len(items)} 条", "-" * 40]
            for a in items:
                badge = _event_badge(a, now.date())
                lines.append(f"• {a.title}" + (f"（{badge}）" if badge else ""))
                if a.section:
                    lines.append(f"  分类：{a.section}")
                if a.date:
                    lines.append(f"  {'时间' if a.is_event else '日期'}：{a.date}")
                if a.location:
                    lines.append(f"  地点：{a.location}")
                if a.speaker:
                    lines.append(f"  主讲：{a.speaker}")
                if a.summary:
                    lines.append(f"  摘要：{a.summary}")
                lines.append(f"  链接：{a.url}")
                lines.append("")
        lines.append("—— 由 web-bugger 自动发送，内容以各官网原文为准")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# template helpers
# ----------------------------------------------------------------------


def _source_of(item: Announcement) -> str:
    if item.source:
        return item.source
    host = urlsplit(item.url).hostname or ""
    return _HOST_SOURCES.get(host, host or "其他")


def _group_by_source(items: list[Announcement]) -> list[tuple[str, list[Announcement]]]:
    """按来源分组，保持各来源首次出现的顺序（即配置里页面的顺序）"""
    groups: dict[str, list[Announcement]] = {}
    for item in items:
        groups.setdefault(_source_of(item), []).append(item)
    return list(groups.items())


def _home_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else url


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _event_badge(item: Announcement, today: date) -> str:
    """活动在今天 / 明天举行时给出醒目标记"""
    if not item.is_event:
        return ""
    try:
        day = date.fromisoformat(item.date[:10])
    except ValueError:
        return ""
    return {0: "今天", 1: "明天"}.get((day - today).days, "")


def _render_group(source: str, items: list[Announcement], today: date) -> str:
    esc = html.escape
    accent, _ = _SOURCE_COLORS.get(source, _DEFAULT_COLORS)
    cards = "".join(_render_card(item, source, today) for item in items)
    return (
        '<tr><td style="padding:24px 28px 4px;">'
        f'<div style="border-left:4px solid {accent};padding:2px 0 2px 10px;font-size:16px;'
        f'font-weight:700;color:#0f172a;">{esc(source)}'
        '<span style="margin-left:6px;font-size:13px;font-weight:400;color:#94a3b8;">'
        f"{len(items)} 条</span></div>"
        "</td></tr>"
        f"{cards}"
    )


def _render_card(item: Announcement, source: str, today: date) -> str:
    esc = html.escape
    accent, tint = _SOURCE_COLORS.get(source, _DEFAULT_COLORS)
    url = esc(item.url, quote=True)

    chips = ""
    badge = _event_badge(item, today)
    if badge:
        chips += (
            '<span style="display:inline-block;margin-right:6px;padding:1px 8px;border-radius:6px;'
            "background-color:#fff1e6;color:#c2410c;font-size:12px;line-height:1.7;"
            f'font-weight:600;">{badge}</span>'
        )
    if item.section:
        chips += (
            '<span style="display:inline-block;padding:1px 8px;border-radius:6px;'
            f'background-color:{tint};color:{accent};font-size:12px;line-height:1.7;">'
            f"{esc(item.section)}</span>"
        )

    meta: list[str] = []
    if item.date:
        meta.append(("🕒 " if item.is_event else "📅 ") + esc(item.date))
    if item.location:
        meta.append("📍 " + esc(item.location))
    if item.speaker:
        meta.append("🎤 " + esc(item.speaker))
    meta_html = "".join(f"<div>{line}</div>" for line in meta)

    summary_html = (
        '<div style="margin-top:10px;padding:10px 12px;border-radius:8px;'
        'background-color:#f8fafc;font-size:14px;line-height:1.7;color:#334155;">'
        f"{esc(item.summary)}</div>"
        if item.summary
        else ""
    )
    return (
        '<tr><td style="padding:10px 28px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border:1px solid #e5e9f0;border-radius:12px;">'
        '<tr><td style="padding:16px 18px;">'
        f"<div>{chips}</div>"
        f'<a href="{url}" style="display:block;margin-top:8px;font-size:16px;line-height:1.5;'
        f'font-weight:600;color:#0f172a;text-decoration:none;">{esc(item.title)}</a>'
        '<div style="margin-top:6px;font-size:13px;line-height:1.8;color:#64748b;">'
        f"{meta_html}</div>"
        f"{summary_html}"
        f'<div style="margin-top:12px;"><a href="{url}" style="font-size:13px;font-weight:600;'
        f'color:{accent};text-decoration:none;">查看原文 →</a></div>'
        "</td></tr></table>"
        "</td></tr>"
    )
