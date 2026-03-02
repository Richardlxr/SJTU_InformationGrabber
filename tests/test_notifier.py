"""
单元测试 - notifier 模板渲染
"""

from web_bugger.models import Announcement
from web_bugger.notifier import Notifier


class TestNotifier:
    def test_render_text(self) -> None:
        items = [
            Announcement(
                title="公告A",
                url="https://a.com/1",
                date="2026-03-01",
                section="质控办",
            ),
        ]
        text = Notifier._render_text(items)
        assert "公告A" in text
        assert "质控办" in text
        assert "https://a.com/1" in text

    def test_render_html(self) -> None:
        items = [
            Announcement(
                title="公告B",
                url="https://b.com/2",
                date="2026-03-02",
                section="新闻中心",
            ),
        ]
        html = Notifier._render_html(items)
        assert "公告B" in html
        assert "https://b.com/2" in html
        assert "<html>" in html
