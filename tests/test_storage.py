"""
单元测试 - storage
"""

import json
from pathlib import Path

from web_bugger.models import Announcement
from web_bugger.storage import Storage


def _make(url: str) -> Announcement:
    return Announcement(title="T", url=url, date="", section="")


class TestStorage:
    def test_empty_on_missing_file(self, tmp_path: Path) -> None:
        s = Storage(tmp_path / "none.json")
        assert s.seen_urls == frozenset()

    def test_mark_and_persist(self, tmp_path: Path) -> None:
        path = tmp_path / "seen.json"
        s = Storage(path)
        items = [_make("https://a.com/1"), _make("https://a.com/2")]
        s.mark_seen(items)

        # 文件写入成功
        data = json.loads(path.read_text("utf-8"))
        assert set(data) == {"https://a.com/1", "https://a.com/2"}

        # 重新加载
        s2 = Storage(path)
        assert s2.seen_urls == {"https://a.com/1", "https://a.com/2"}

    def test_filter_new(self, tmp_path: Path) -> None:
        path = tmp_path / "seen.json"
        s = Storage(path)
        s.mark_seen([_make("https://a.com/1")])

        items = [_make("https://a.com/1"), _make("https://a.com/2")]
        new = s.filter_new(items)
        assert len(new) == 1
        assert new[0].url == "https://a.com/2"

    def test_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json!!!", encoding="utf-8")
        s = Storage(path)
        assert s.seen_urls == frozenset()
