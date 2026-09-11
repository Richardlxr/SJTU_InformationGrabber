"""
单元测试 - storage（原子写入 / 损坏文件隔离）
"""

import json
import os
from pathlib import Path

import pytest

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

    def test_mark_seen_is_idempotent(self, tmp_path: Path) -> None:
        s = Storage(tmp_path / "seen.json")
        s.mark_seen([_make("https://a.com/1")])
        s.mark_seen([_make("https://a.com/1")])
        assert s.seen_urls == {"https://a.com/1"}


class TestCorruptFile:
    def test_corrupt_file_quarantined(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json!!!", encoding="utf-8")
        s = Storage(path)
        assert s.seen_urls == frozenset()
        assert (tmp_path / "bad.json.corrupt").is_file(), "损坏文件应被备份而非静默覆盖"
        assert not path.exists()

    @pytest.mark.parametrize("content", ["null", "{}", '{"a": 1}', '"str"', "42"])
    def test_valid_json_with_wrong_shape_is_handled(
        self, tmp_path: Path, content: str
    ) -> None:
        """合法 JSON 但顶层不是数组时不能抛 TypeError 崩溃启动"""
        path = tmp_path / "weird.json"
        path.write_text(content, encoding="utf-8")
        s = Storage(path)
        assert s.seen_urls == frozenset()
        assert (tmp_path / "weird.json.corrupt").is_file()

    def test_list_of_non_strings_is_filtered(self, tmp_path: Path) -> None:
        """数组内混入非字符串条目时只过滤，不当作损坏文件"""
        path = tmp_path / "weird.json"
        path.write_text('[{"url": "x"}, 1, null]', encoding="utf-8")
        s = Storage(path)
        assert s.seen_urls == frozenset()
        assert not (tmp_path / "weird.json.corrupt").exists()

    def test_blank_file_is_treated_as_empty_without_quarantine(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "blank.json"
        path.write_text("", encoding="utf-8")
        s = Storage(path)
        assert s.seen_urls == frozenset()
        assert not (tmp_path / "blank.json.corrupt").exists()

    def test_non_string_entries_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.json"
        path.write_text(
            json.dumps(["https://a.com/1", 42, None, "", {"x": 1}]), encoding="utf-8"
        )
        s = Storage(path)
        assert s.seen_urls == {"https://a.com/1"}


class TestAtomicWrite:
    def test_no_tmp_files_left_behind(self, tmp_path: Path) -> None:
        s = Storage(tmp_path / "seen.json")
        s.mark_seen([_make("https://a.com/1")])
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_failed_replace_keeps_previous_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """原子写失败时旧文件必须完好，且不留下临时文件"""
        path = tmp_path / "seen.json"
        s = Storage(path)
        s.mark_seen([_make("https://a.com/1")])
        before = path.read_text("utf-8")

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        s.mark_seen([_make("https://a.com/2")])  # 不应抛异常

        assert path.read_text("utf-8") == before, "写失败不应破坏旧文件"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == [], "临时文件应被清理"

    def test_atomic_write_uses_replace(self, tmp_path: Path) -> None:
        path = tmp_path / "seen.json"
        s = Storage(path)
        s.mark_seen([_make("https://a.com/1")])
        # 文件始终是完整合法的 JSON（不存在写一半的中间态）
        assert json.loads(path.read_text("utf-8")) == ["https://a.com/1"]

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        s = Storage(tmp_path / "nested" / "deep" / "seen.json")
        s.mark_seen([_make("https://a.com/1")])
        assert (tmp_path / "nested" / "deep" / "seen.json").is_file()
