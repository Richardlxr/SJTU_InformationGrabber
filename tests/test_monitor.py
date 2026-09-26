"""
单元测试 - monitor（含 dry-run 不吞通知等回归测试）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from web_bugger.alerts import ALERT_RETRY_BACKOFF
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


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _config(
    tmp_path: Path, *, failure_alert_threshold: int = 3, failure_alert_interval: int = 86400
) -> AppConfig:
    return AppConfig(
        scraper=ScraperConfig(target_urls=["https://x.com/page"]),
        data_dir=tmp_path,
        failure_alert_threshold=failure_alert_threshold,
        failure_alert_interval=failure_alert_interval,
    )


def _monitor(
    tmp_path: Path,
    scraper: FakeScraper,
    notifier: FakeNotifier | None = None,
    storage: Storage | None = None,
    *,
    failure_alert_threshold: int = 3,
    failure_alert_interval: int = 86400,
    clock: FakeClock | None = None,
) -> Monitor:
    return Monitor(
        _config(
            tmp_path,
            failure_alert_threshold=failure_alert_threshold,
            failure_alert_interval=failure_alert_interval,
        ),
        scraper=scraper,
        storage=storage or Storage(tmp_path / "seen_announcements.json"),
        notifier=notifier or FakeNotifier(),
        clock=clock or FakeClock(),
    )


class TestDryRun:
    """dry-run 必须完全没有副作用（回归测试）"""

    def test_dry_run_does_not_consume_announcements(self, tmp_path: Path) -> None:
        scraper = FakeScraper(FetchResult(items=[A1], pages_total=1))
        notifier = FakeNotifier()
        monitor = _monitor(tmp_path, scraper, notifier)

        assert monitor.check_once(dry_run=True) == 1
        assert notifier.sent == [], "dry-run 不应该发邮件"
        assert not (tmp_path / "seen_announcements.json").exists(), "dry-run 不应该写入已读状态"

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

    def test_alert_repeats_once_per_configured_interval(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        clock = FakeClock()
        monitor = _monitor(
            tmp_path,
            FakeScraper(self._failed()),
            notifier,
            failure_alert_threshold=1,
            failure_alert_interval=7200,
            clock=clock,
        )
        monitor.check_once()
        assert len(notifier.alerts) == 1

        clock.now = 7199
        monitor.check_once()
        assert len(notifier.alerts) == 1, "间隔未满不应重复告警"

        clock.now = 7200
        monitor.check_once()
        assert len(notifier.alerts) == 2, "满一个间隔后应再提醒一次"
        assert "每 2 小时" in notifier.alerts[1][1]

    def test_new_failing_page_alerts_immediately(self, tmp_path: Path) -> None:
        """回归：旧页面持续报错期间，新出问题的页面不能被 1 天的间隔压住"""
        notifier = FakeNotifier()
        clock = FakeClock()
        old = FetchResult(items=[A1], pages_total=3, failed_urls=("old",))
        both = FetchResult(items=[A1], pages_total=3, failed_urls=("old", "new"))
        monitor = _monitor(
            tmp_path, FakeScraper(old, old, both), notifier, failure_alert_threshold=2, clock=clock
        )

        monitor.check_once()
        monitor.check_once()  # old 达到阈值 -> 告警
        assert len(notifier.alerts) == 1

        for _ in range(3):  # new 连续失败
            clock.now += 300
            monitor.check_once()
        assert len(notifier.alerts) == 2, "new 达到阈值应立即告警"
        body = notifier.alerts[1][1]
        assert "new（连续失败 2 次）【新增】" in body
        assert "old（连续失败 4 次）\n" in body, "早已提醒过的页面不标【新增】"

    def test_transient_failure_does_not_alert(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        old = FetchResult(items=[A1], pages_total=3, failed_urls=("old",))
        blip = FetchResult(items=[A1], pages_total=3, failed_urls=("old", "new"))
        monitor = _monitor(
            tmp_path, FakeScraper(old, old, blip, old), notifier, failure_alert_threshold=2
        )
        for _ in range(4):
            monitor.check_once()
        assert len(notifier.alerts) == 1, "new 只失败一次，不应告警"

    def test_flapping_page_alerts_once_per_interval(self, tmp_path: Path) -> None:
        """回归：页面时好时坏（失败 3 次、恢复 1 次）也只能每个间隔提醒一次"""
        notifier = FakeNotifier()
        clock = FakeClock()
        fail = FetchResult(items=[A1], pages_total=2, failed_urls=("a",))
        ok = FetchResult(items=[A1], pages_total=2)
        rounds = ([fail] * 3 + [ok]) * 72  # 24 小时，每 5 分钟一轮
        monitor = _monitor(
            tmp_path, FakeScraper(*rounds), notifier, failure_alert_threshold=3, clock=clock
        )
        for _ in rounds:
            monitor.check_once()
            clock.now += 300
        assert len(notifier.alerts) == 1

    def test_failed_alert_send_backs_off(self, tmp_path: Path) -> None:
        notifier = FakeNotifier(ok=False)
        clock = FakeClock()
        monitor = _monitor(
            tmp_path, FakeScraper(self._failed()), notifier, failure_alert_threshold=1, clock=clock
        )
        monitor.check_once()
        clock.now = ALERT_RETRY_BACKOFF - 1
        monitor.check_once()
        assert len(notifier.alerts) == 1, "发送失败后退避期内不应重试"

        clock.now = ALERT_RETRY_BACKOFF
        notifier.ok = True
        monitor.check_once()
        assert len(notifier.alerts) == 2, "退避期满后应重试"
        clock.now += 300
        monitor.check_once()
        assert len(notifier.alerts) == 2, "发送成功后恢复按间隔提醒"

    def test_state_survives_restart(self, tmp_path: Path) -> None:
        """回归：重启（新 Monitor 实例）后不应重新计数、重复告警"""
        clock = FakeClock()
        first = FakeNotifier()
        monitor = _monitor(
            tmp_path, FakeScraper(self._failed()), first, failure_alert_threshold=2, clock=clock
        )
        monitor.check_once()
        monitor.check_once()
        assert len(first.alerts) == 1

        clock.now += 600
        second = FakeNotifier()
        restarted = _monitor(
            tmp_path, FakeScraper(self._failed()), second, failure_alert_threshold=2, clock=clock
        )
        restarted.check_once()
        assert second.alerts == [], "重启后仍在间隔内，不应再告警"

    def test_streak_persists_across_once_runs(self, tmp_path: Path) -> None:
        """--once 定时运行：每次都是新进程，连续失败次数也要累计"""
        notifier = FakeNotifier()
        for _ in range(3):
            _monitor(
                tmp_path, FakeScraper(self._failed()), notifier, failure_alert_threshold=3
            ).check_once()
        assert len(notifier.alerts) == 1

    def test_alert_text_partial_failure(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        partial = FetchResult(items=[A1], pages_total=3, failed_urls=("a",))
        monitor = _monitor(tmp_path, FakeScraper(partial), notifier, failure_alert_threshold=1)
        monitor.check_once()
        subject, body = notifier.alerts[0]
        assert subject == "【交大信息监控】1 个页面持续抓取失败"
        assert "其余页面的新公告仍会正常通知" in body
        assert "不会有任何新公告通知" not in body
        assert "每 1 天" in body

    def test_alert_text_all_failed(self, tmp_path: Path) -> None:
        notifier = FakeNotifier()
        monitor = _monitor(
            tmp_path, FakeScraper(self._failed()), notifier, failure_alert_threshold=1
        )
        monitor.check_once()
        assert "所有页面均抓取失败" in notifier.alerts[0][1]

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
        assert not (tmp_path / "alert_state.json").exists(), "dry-run 不应写告警状态"


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
