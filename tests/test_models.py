"""
单元测试 - models
"""

from web_bugger.models import Announcement


class TestAnnouncement:
    def test_equality_by_url(self) -> None:
        a1 = Announcement(
            title="A", url="https://x.com/1", date="2026-01-01", section="S"
        )
        a2 = Announcement(
            title="B", url="https://x.com/1", date="2026-02-02", section="T"
        )
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
            title="T", url="https://x.com/1", date="2026-03-01", section="S"
        )
        d = a.to_dict()
        assert Announcement.from_dict(d) == a

    def test_frozen(self) -> None:
        a = Announcement(title="T", url="https://x.com/1", date="", section="")
        try:
            a.title = "new"  # type: ignore[misc]
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass
