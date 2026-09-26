"""
单元测试 - alerts（按页面的失败跟踪 / 告警节流 / 状态持久化）
"""

from __future__ import annotations

import json
from pathlib import Path

from web_bugger.alerts import ALERT_RETRY_BACKOFF, FailureTracker

DAY = 86400


def _tracker(path: Path | None = None, *, threshold: int = 2) -> FailureTracker:
    return FailureTracker(path, threshold=threshold, interval=DAY)


class TestObserve:
    def test_streaks_count_per_page(self) -> None:
        t = _tracker()
        t.observe(["a", "b"])
        t.observe(["a"])
        assert t.streak("a") == 2
        assert t.streak("b") == 0

    def test_returns_recovered_pages(self) -> None:
        t = _tracker()
        t.observe(["a", "b"])
        t.observe(["a", "b"])
        assert t.observe(["a"]) == {"b": 2}

    def test_failing_requires_threshold(self) -> None:
        t = _tracker(threshold=3)
        t.observe(["a", "b"])
        t.observe(["a"])
        assert t.failing() == []
        t.observe(["a"])
        assert t.failing() == ["a"]


class TestAlertDue:
    def test_due_once_per_interval(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        assert t.alert_due(0) is True
        t.mark_alerted(t.failing(), 0)
        assert t.alert_due(DAY - 1) is False
        assert t.alert_due(DAY) is True

    def test_not_due_without_failing_pages(self) -> None:
        t = _tracker(threshold=1)
        t.observe([])
        assert t.alert_due(0) is False

    def test_clock_rollback_counts_as_expired(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        t.mark_alerted(["a"], 1000)
        assert t.alert_due(500) is True, "时钟回拨时宁可多提醒一次"

    def test_send_failure_backs_off(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        t.mark_send_failed(0)
        assert t.alert_due(ALERT_RETRY_BACKOFF - 1) is False
        assert t.alert_due(ALERT_RETRY_BACKOFF) is True

    def test_backoff_survives_clock_rollback(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        t.mark_send_failed(10 * DAY)
        assert t.alert_due(0) is True, "时钟大幅回拨时不应被退避卡住"

    def test_is_new(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        assert t.is_new("a") is True
        t.mark_alerted(["a"], 0)
        assert t.is_new("a") is False

    def test_expired_records_of_recovered_pages_are_pruned(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        t.mark_alerted(["a"], 0)
        t.observe(["b"])  # a 恢复
        t.mark_alerted(["b"], DAY)  # a 的记录已过期 -> 清理
        assert t.is_new("a") is True
        assert t.is_new("b") is False

    def test_recent_records_of_recovered_pages_are_kept(self) -> None:
        t = _tracker(threshold=1)
        t.observe(["a"])
        t.mark_alerted(["a"], 0)
        t.observe(["b"])  # a 恢复
        t.mark_alerted(["b"], 100)
        assert t.is_new("a") is False, "a 未满间隔，恢复后再挂也不应立即重复提醒"


class TestPersistence:
    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "alert_state.json"
        t = _tracker(path, threshold=1)
        t.observe(["a"])
        t.mark_alerted(["a"], 123.0)
        t.mark_send_failed(200.0)
        t.save()

        t2 = _tracker(path, threshold=1)
        assert t2.streak("a") == 1
        assert t2.is_new("a") is False
        assert t2.alert_due(200.0 + ALERT_RETRY_BACKOFF - 1) is False

    def test_no_file_written_when_nothing_changes(self, tmp_path: Path) -> None:
        path = tmp_path / "alert_state.json"
        t = _tracker(path)
        t.observe([])
        t.save()
        assert not path.exists()

    def test_skips_rewrite_when_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "alert_state.json"
        t = _tracker(path)
        t.observe(["a"])
        t.save()
        path.write_text("sentinel", encoding="utf-8")
        t.save()
        assert path.read_text(encoding="utf-8") == "sentinel"

    def test_none_path_is_memory_only(self) -> None:
        t = _tracker(None)
        t.observe(["a"])
        t.save()  # 不应抛异常

    def test_corrupt_file_starts_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "alert_state.json"
        path.write_text("{not json", encoding="utf-8")
        t = _tracker(path)
        assert t.streak("a") == 0
        t.observe(["a"])
        t.save()
        assert json.loads(path.read_text(encoding="utf-8"))["streaks"] == {"a": 1}

    def test_ignores_malformed_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "alert_state.json"
        path.write_text(
            json.dumps(
                {
                    "streaks": {"a": 2, "b": "x", "c": True, "d": 0},
                    "alerted_at": {"a": 1.5, "b": None, "c": False},
                    "retry_after": "soon",
                }
            ),
            encoding="utf-8",
        )
        t = _tracker(path, threshold=1)
        assert t.failing() == ["a"]
        assert t.is_new("a") is False
        assert t.is_new("b") is True
        assert t.is_new("c") is True
        assert t.alert_due(1.5 + DAY) is True

    def test_non_dict_file_starts_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "alert_state.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert _tracker(path).failing() == []
