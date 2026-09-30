"""
单元测试 - notifier（模板渲染 / 转义 / 投递异常处理）
"""

from __future__ import annotations

import smtplib
import socket
from datetime import datetime
from email import message_from_string
from email.parser import Parser
from typing import Any

import pytest

from web_bugger.config import SmtpConfig
from web_bugger.models import Announcement
from web_bugger.notifier import Notifier


def _items() -> list[Announcement]:
    return [
        Announcement(
            title="公告A",
            url="https://a.com/1",
            date="2026-03-01",
            section="质控办",
        )
    ]


def _configured(**overrides: Any) -> SmtpConfig:
    kwargs: dict[str, Any] = {
        "sender_email": "sender@qq.com",
        "sender_password": "authcode",
        "receiver_email": "me@qq.com",
    }
    kwargs.update(overrides)
    return SmtpConfig(**kwargs)


class FakeServer:
    """记录调用的假 SMTP 服务器"""

    def __init__(
        self,
        *args: object,
        login_error: Exception | None = None,
        send_error: Exception | None = None,
        **kwargs: object,
    ) -> None:
        self.login_error = login_error
        self.send_error = send_error
        self.quit_called = False
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None
        self.sendmail_args: tuple[str, list[str], str] | None = None

    def starttls(self, context: object = None) -> None:
        self.starttls_called = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)
        if self.login_error is not None:
            raise self.login_error

    def sendmail(self, sender: str, to: list[str], body: str) -> None:
        self.sendmail_args = (sender, to, body)
        if self.send_error is not None:
            raise self.send_error

    def quit(self) -> None:
        self.quit_called = True


# 每次真实发起 SMTP 连接时记录一笔（用于断言「没有建连」）
_connections: list[FakeServer] = []


@pytest.fixture(autouse=True)
def _reset_connections() -> None:
    _connections.clear()


def _install_server(
    monkeypatch: pytest.MonkeyPatch, *, ssl: bool = True, **server_kwargs: Any
) -> FakeServer:
    """把 smtplib.SMTP_SSL / SMTP 换成一个假服务器并返回它"""
    server = FakeServer(**server_kwargs)

    def factory(*args: object, **kwargs: object) -> FakeServer:
        _connections.append(server)
        return server

    monkeypatch.setattr(smtplib, "SMTP_SSL" if ssl else "SMTP", factory)
    return server


def _decoded_text(body: str) -> str:
    """取出邮件正文（自动做 base64/quoted-printable 解码）"""
    message = message_from_string(body)
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(part.get_content_charset() or "utf-8")
        return ""
    payload = message.get_payload(decode=True)
    return payload.decode("utf-8") if isinstance(payload, bytes) else ""


NOW = datetime(2026, 9, 29, 20, 30)


def _mixed() -> list[Announcement]:
    return [
        Announcement(
            title="致远阳光领袖奖学金评选通知",
            url="https://zhiyuan.sjtu.edu.cn/post/3121",
            date="2026-09-30",
            section="学生事务",
            source="致远学院",
            summary="为厚植学生家国情怀……",
        ),
        Announcement(
            title="大学物理（荣誉）选拔测试的通知",
            url="https://jwc.sjtu.edu.cn/info/1222/1.htm",
            date="2026-09-24",
            section="面向学生的通知",
            source="教务处",
        ),
        Announcement(
            title="ChalkTalk No.107",
            url="https://zhiyuan.sjtu.edu.cn/event/2639",
            date="2026-09-30 12:00-13:30",
            section="学术活动·ChalkTalk",
            source="致远学院",
            location="110教室",
            speaker="金梦，教授",
        ),
    ]


class TestTemplates:
    def test_render_text(self) -> None:
        text = Notifier._render_text(_items(), NOW)
        assert "公告A" in text
        assert "质控办" in text
        assert "https://a.com/1" in text
        assert "2026-03-01" in text

    def test_render_text_rich_fields_grouped(self) -> None:
        text = Notifier._render_text(_mixed(), NOW)
        assert text.index("【致远学院】2 条") < text.index("【教务处】1 条"), "按首次出现顺序分组"
        assert "摘要：为厚植学生家国情怀……" in text
        assert "时间：2026-09-30 12:00-13:30" in text
        assert "地点：110教室" in text
        assert "主讲：金梦，教授" in text
        assert "• ChalkTalk No.107（明天）" in text
        assert "致远学院 2 · 教务处 1" in text

    def test_render_html(self) -> None:
        html = Notifier._render_html(_items(), NOW)
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        assert "公告A" in html
        assert "https://a.com/1" in html
        assert "发现 1 条新公告" in html

    def test_render_html_rich_fields(self) -> None:
        html = Notifier._render_html(_mixed(), NOW)
        assert html.index(">致远学院<") < html.index(">教务处<"), "按来源分组"
        assert "为厚植学生家国情怀……" in html
        assert "110教室" in html and "金梦，教授" in html
        assert ">明天</span>" in html, "明天举行的活动要有醒目标记"
        assert "https://zhiyuan.sjtu.edu.cn/" in html, "页脚链接到来源站点首页"

    def test_render_html_escapes_untrusted_content(self) -> None:
        html = Notifier._render_html(
            [
                Announcement(
                    title='关于 <2026级> A&R "通知"',
                    url="https://a.com/1?a=1&b=2",
                    date="2026-01-01",
                    section="<b>S</b>",
                    source="<i>源</i>",
                    summary="<script>alert(1)</script>",
                    location="<u>地点</u>",
                    speaker="<img src=x>",
                )
            ],
            NOW,
        )
        assert "<2026级>" not in html, "标题中的 HTML 必须被转义"
        assert "&lt;2026级&gt;" in html
        assert "&amp;" in html
        assert "a=1&b=2" not in html, "URL 中的 & 必须被转义"
        for raw in ("<b>S</b>", "<i>源</i>", "<script>", "<u>地点</u>", "<img src=x>"):
            assert raw not in html

    def test_render_html_handles_empty_list(self) -> None:
        html = Notifier._render_html([], NOW)
        assert "发现 0 条新公告" in html

    def test_source_falls_back_to_host(self) -> None:
        item = Announcement(title="T", url="https://cs.sjtu.edu.cn/1.html", date="", section="")
        assert "【计算机学院】1 条" in Notifier._render_text([item], NOW)
        unknown = Announcement(title="T", url="https://x.edu.cn/1", date="", section="")
        assert "【x.edu.cn】1 条" in Notifier._render_text([unknown], NOW)

    @pytest.mark.parametrize(
        ("event_date", "badge"),
        [("2026-09-29 12:00", "今天"), ("2026-09-30", "明天"), ("2026-10-01", ""), ("待定", "")],
    )
    def test_event_badge(self, event_date: str, badge: str) -> None:
        event = Announcement(
            title="活动", url="https://z/1", date=event_date, section="", location="教室"
        )
        text = Notifier._render_text([event], NOW)
        assert (f"• 活动（{badge}）" in text) if badge else ("• 活动\n" in text)

    def test_announcement_never_gets_badge(self) -> None:
        notice = Announcement(title="通知", url="https://z/1", date="2026-09-29", section="")
        assert "• 通知\n" in Notifier._render_text([notice], NOW)


class TestBuildMessage:
    def test_headers_and_multipart(self) -> None:
        msg = Notifier(_configured())._build_message(_items())
        assert msg["From"] == "sender@qq.com"
        assert msg["To"] == "me@qq.com"
        assert "1 条新公告" in str(msg["Subject"])

    def test_subject_contains_first_title(self) -> None:
        assert Notifier._subject(_items()) == "【交大信息监控】1 条新公告：公告A"
        subject = Notifier._subject(_mixed())
        assert subject == "【交大信息监控】3 条新公告：致远阳光领袖奖学金评选通知 等"

    def test_subject_truncates_long_title(self) -> None:
        item = Announcement(title="长" * 80, url="https://a.com/1", date="", section="")
        subject = Notifier._subject([item])
        assert subject.endswith("…")
        assert len(subject) < 80

    def test_subject_is_rfc2047_encoded(self) -> None:
        raw = Notifier(_configured())._build_message(_items()).as_string()
        subject = raw.split("Subject: ")[1].split("\n")[0]
        assert subject.startswith("=?utf-8?"), "中文主题必须做 RFC2047 编码"

    def test_multiple_recipients(self) -> None:
        msg = Notifier(_configured(receiver_email="a@qq.com, b@qq.com"))._build_message(_items())
        assert msg["To"] == "a@qq.com, b@qq.com"
        assert Parser().parsestr(msg.as_string())["To"] == "a@qq.com, b@qq.com"

    def test_alert_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_server(monkeypatch)
        assert Notifier(_configured()).send_alert("告警主题", "正文内容") is True
        assert server.sendmail_args is not None
        assert "正文内容" in _decoded_text(server.sendmail_args[2])


class TestSendGuards:
    def test_empty_list_is_success_without_smtp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_server(monkeypatch)
        assert Notifier(_configured()).send([]) is True
        assert _connections == [], "空列表不应建立连接"

    def test_unconfigured_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_server(monkeypatch)
        assert Notifier(SmtpConfig()).send(_items()) is False
        assert Notifier(SmtpConfig()).send_alert("s", "t") is False
        assert _connections == [], "未配置时不应建立连接"

    def test_unconfigured_receiver_only(self) -> None:
        cfg = SmtpConfig(sender_email="a@qq.com", sender_password="x")
        assert Notifier(cfg).send(_items()) is False


class TestDeliver:
    def test_success_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_server(monkeypatch)
        assert Notifier(_configured()).send(_items()) is True
        assert _connections == [server]
        assert server.login_args == ("sender@qq.com", "authcode")
        assert server.sendmail_args is not None
        sender, to, _body = server.sendmail_args
        assert sender == "sender@qq.com"
        assert to == ["me@qq.com"]
        assert server.quit_called is True

    def test_multi_recipient_sendmail_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_server(monkeypatch)
        Notifier(_configured(receiver_email="a@qq.com;b@qq.com")).send(_items())
        assert server.sendmail_args is not None
        assert server.sendmail_args[1] == ["a@qq.com", "b@qq.com"]

    @pytest.mark.parametrize(
        "error",
        [
            smtplib.SMTPAuthenticationError(535, b"auth failed"),
            smtplib.SMTPException("smtp boom"),
            ConnectionRefusedError(61, "refused"),
            socket.gaierror(8, "nodename nor servname provided"),
            TimeoutError("timed out"),
        ],
    )
    def test_errors_return_false_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        """网络类异常（OSError 家族）也必须被吞掉，否则 --once 会崩溃"""
        _install_server(monkeypatch, login_error=error)
        assert Notifier(_configured()).send(_items()) is False

    def test_sendmail_error_returns_false_and_quits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_server(monkeypatch, send_error=smtplib.SMTPServerDisconnected("gone"))
        assert Notifier(_configured()).send(_items()) is False
        assert server.quit_called is True

    def test_uses_starttls_when_ssl_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _install_server(monkeypatch, ssl=False)
        cfg = _configured(use_ssl=False, port=587)
        assert Notifier(cfg).send(_items()) is True
        assert server.starttls_called is True
