"""
单元测试 - models
"""

import pytest

from web_bugger.models import Announcement


class TestAnnouncement:
    def test_equality_by_url(self) -> None:
        a1 = Announcement(title="A", url="https://x.com/1", date="2026-01-01", section="S")
        a2 = Announcement(title="B", url="https://x.com/1", date="2026-02-02", section="T")
        assert a1 == a2

    def test_inequality(self) -> None:
        a1 = Announcement(title="A", url="https://x.com/1", date="", section="")
        a2 = Announcement(title="A", url="https://x.com/2", date="", section="")
        assert a1 != a2

    def test_hash_by_url(self) -> None:
        a1 = Announcement(title="A", url="https://x.com/1", date="", section="")
        a2 = Announcement(title="B", url="https://x.com/1", date="", section="")
        assert hash(a1) == hash(a2)
        assert len({a1, a2}) == 1

    def test_to_dict_roundtrip(self) -> None:
        a = Announcement(
            title="T",
            url="https://x.com/1",
            date="2026-03-01 12:00-13:30",
            section="S",
            source="致远学院",
            summary="摘要",
            location="110教室",
            speaker="某教授",
        )
        restored = Announcement.from_dict(a.to_dict())
        assert restored == a
        assert restored.to_dict() == a.to_dict(), "所有字段都应往返一致（== 只比较 URL）"

    def test_from_dict_accepts_old_format(self) -> None:
        a = Announcement.from_dict({"title": "T", "url": "https://x.com/1"})
        assert (a.date, a.section, a.source, a.summary, a.location, a.speaker) == ("",) * 6

    def test_is_event(self) -> None:
        notice = Announcement(title="T", url="https://x.com/1", date="", section="")
        assert notice.is_event is False
        assert Announcement(title="T", url="u", date="", section="", location="教室").is_event
        assert Announcement(title="T", url="u", date="", section="", speaker="某人").is_event

    def test_frozen(self) -> None:
        a = Announcement(title="T", url="https://x.com/1", date="", section="")
        with pytest.raises(AttributeError):
            a.title = "new"  # type: ignore[misc]
