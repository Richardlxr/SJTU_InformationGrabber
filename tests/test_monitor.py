"""
单元测试 - monitor（含 dry-run 不吞通知等回归测试）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from web_bugger.config import AppConfig, ScraperConfig
from web_bugger.models import Announcement
from web_bugger.monitor import Monitor
from web_bugger.scraper import FetchResult
from web_bugger.storage import Storage

A1 = Announcement(title="公告一", url="https://x.com/1", date="2026-01-01", section="S")
A2 = Announcement(title="公告二", url="https://x.com/2", date="2026-01-02", section="S")


class FakeScraper:
    """按调用顺序返回预设结果，之后重复最后一个结果"""

    def __init__(self, *results: FetchResult) -> None:
        self._results = list(results) or [FetchResult()]
        self.calls = 0
        self.closed = False

    def fetch_with_status(self) -> FetchResult:
        index = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[index]

    def close(self) -> None:
        self.closed = True


class FakeNotifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[list[Announcement]] = []
        self.alerts: list[tuple[str, str]] = []

    def send(self, announcements: list[Announcement]) -> bool:
        self.sent.append(list(announcements))
        return self.ok

    def send_alert(self, subject: str, text: str) -> bool:
        self.alerts.append((subject, text))
        return self.ok


def _config(tmp_path: Path, *, failure_alert_threshold: int = 3) -> AppConfig:
    return AppConfig(
        scraper=ScraperConfig(target_urls=["https://x.com/page"]),
        data_dir=tmp_path,
        failure_alert_threshold=failure_alert_threshold,
    )


def _monitor(
    tmp_path: Path,
    scraper: FakeScraper,
    notifier: FakeNotifier | None = None,
    storage: Storage | None = None,
    *,
    failure_alert_threshold: int = 3,
) -> Monitor:
    return Monitor(
        _config(tmp_path, failure_alert_threshold=failure_alert_threshold),
        scraper=scraper,
        storage=storage or Storage(tmp_path / "seen_announcements.json"),
        notifier=notifier or FakeNotifier(),
    )


class TestDryRun:
    """dry-run 必须完全没有副作用（回归测试）"""

    def test_dry_run_does_not_consume_announcements(self, tmp_path: Path) -> None:
        scraper = FakeScraper(FetchResult(items=[A1], pages_total=1))
        notifier = FakeNotifier()
        monitor = _monitor(tmp_path, scraper, notifier)

        assert monitor.check_once(dry_run=True) == 1
        assert notifier.sent == [], "dry-run 不应该发邮件"
        assert not (tmp_path / "seen_announcements.json").exists(), (
            "dry-run 不应该写入已读状态"
        )

        # 关键：dry-run 之后真实运行仍应发现同一条公告
        assert monitor.check_once() == 1
        assert len(notifier.sent) == 1
        assert Storage(tmp_path / "seen_announcements.json").seen_urls == {A1.url}

    def test_dry_run_reports_count(self, tmp_path: Path) -> None:
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[A1, A2], pages_total=1)))
        assert monitor.check_once(dry_run=True) == 2


class TestCheckOnce:
    def test_marks_seen_only_after_successful_send(self, tmp_path: Path) -> None:
        notifier = FakeNotifier(ok=True)
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[A1], pages_total=1)), notifier)

        assert monitor.check_once() == 1
        assert Storage(tmp_path / "seen_announcements.json").seen_urls == {A1.url}
        # 第二次运行不应重复通知
        assert monitor.check_once() == 0
        assert len(notifier.sent) == 1

    def test_send_failure_keeps_announcement_unseen(self, tmp_path: Path) -> None:
        notifier = FakeNotifier(ok=False)
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[A1], pages_total=1)), notifier)

        assert monitor.check_once() == 1
        assert Storage(tmp_path / "seen_announcements.json").seen_urls == frozenset()
        # 下次仍然重试
        assert monitor.check_once() == 1
        assert len(notifier.sent) == 2

    def test_no_announcements_returns_zero(self, tmp_path: Path) -> None:
        monitor = _monitor(
            tmp_path,
            FakeScraper(FetchResult(items=[], pages_total=1, failed_urls=("u",))),
        )
        assert monitor.check_once() == 0


class TestInit:
    def test_init_returns_true_and_marks_seen(self, tmp_path: Path) -> None:
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[A1, A2], pages_total=1)))
        assert monitor.init() is True
        assert Storage(tmp_path / "seen_announcements.json").seen_urls == {A1.url, A2.url}

    def test_init_returns_false_without_announcements(self, tmp_path: Path) -> None:
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[], pages_total=1)))
        assert monitor.init() is False
        assert not (tmp_path / "seen_announcements.json").exists()

    def test_init_reports_partial_failure(self, tmp_path: Path) -> None:
        monitor = _monitor(
            tmp_path,
            FakeScraper(FetchResult(items=[A1], pages_total=2, failed_urls=("bad",))),
        )
        assert monitor.init() is True


class TestFailureAlert:
    def _failed(self) -> FetchResult:
        return FetchResult(items=[], pages_total=2, failed_urls=("a", "b"))

    def test_alerts_only_at_threshold(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        monitor = _monitor(
            tmp_path, FakeScraper(self._failed()), notifier, failure_alert_threshold=3
        )

        monitor.check_once()
        monitor.check_once()
        assert notifier.alerts == [], "未到阈值不应发告警"

        monitor.check_once()
        assert len(notifier.alerts) == 1, "第 3 次失败应发告警"
        assert "抓取失败" in notifier.alerts[0][0]

    def test_alert_repeats_every_threshold(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        monitor = _monitor(
            tmp_path, FakeScraper(self._failed()), notifier, failure_alert_threshold=2
        )
        for _ in range(4):
            monitor.check_once()
        assert len(notifier.alerts) == 2

    def test_success_resets_counter(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        scraper = FakeScraper(
            FetchResult(items=[], pages_total=1, failed_urls=("a",)),
            FetchResult(items=[A1], pages_total=1),
            FetchResult(items=[], pages_total=1, failed_urls=("a",)),
            FetchResult(items=[], pages_total=1, failed_urls=("a",)),
        )
        monitor = _monitor(tmp_path, scraper, notifier, failure_alert_threshold=2)

        monitor.check_once()  # 失败 1
        monitor.check_once()  # 成功 -> 归零
        monitor.check_once()  # 失败 1
        assert notifier.alerts == []
        monitor.check_once()  # 失败 2 -> 告警
        assert len(notifier.alerts) == 1

    def test_no_alert_in_dry_run(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        monitor = _monitor(
            tmp_path, FakeScraper(self._failed()), notifier, failure_alert_threshold=1
        )
        monitor.check_once(dry_run=True)
        assert notifier.alerts == []


class TestRunLoop:
    def test_run_calls_check_once_then_propagates_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[A1], pages_total=1)))
        calls: list[bool] = []

        def fake_check_once(*, dry_run: bool = False) -> int:
            calls.append(dry_run)
            raise KeyboardInterrupt

        monkeypatch.setattr(monitor, "check_once", fake_check_once)
        monkeypatch.setattr("web_bugger.monitor.time.sleep", lambda _s: None)

        with pytest.raises(KeyboardInterrupt):
            monitor.run(dry_run=True)
        assert calls == [True]

    def test_run_survives_check_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monitor = _monitor(tmp_path, FakeScraper(FetchResult(items=[A1], pages_total=1)))
        calls: list[int] = []

        def fake_check_once(*, dry_run: bool = False) -> int:
            calls.append(1)
            if len(calls) >= 2:
                raise SystemExit(0)
            raise RuntimeError("boom")

        monkeypatch.setattr(monitor, "check_once", fake_check_once)
        monkeypatch.setattr("web_bugger.monitor.time.sleep", lambda _s: None)

        with pytest.raises(SystemExit):
            monitor.run()
        assert len(calls) == 2, "RuntimeError 应被吞掉并继续循环"


class TestClose:
    def test_close_closes_scraper(self, tmp_path: Path) -> None:
        scraper = FakeScraper()
        monitor = _monitor(tmp_path, scraper)
        monitor.close()
        assert scraper.closed is True
        monitor.close()  # 可重复调用

    def test_close_tolerates_scraper_without_close(self, tmp_path: Path) -> None:
        class NoClose:
            def fetch_with_status(self) -> FetchResult:
                return FetchResult()

        monitor = Monitor(_config(tmp_path), scraper=NoClose())
        monitor.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
