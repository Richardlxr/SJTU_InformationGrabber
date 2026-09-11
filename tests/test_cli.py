"""
单元测试 - CLI（参数互斥 / 退出码 / dry-run 透传）
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import web_bugger.cli as cli
import web_bugger.config as config_module
from web_bugger.config import AppConfig, ConfigError, ScraperConfig


class FakeMonitor:
    def __init__(self, *, init_ok: bool = True, count: int = 0, config: AppConfig) -> None:
        self.init_ok = init_ok
        self.count = count
        self.config = config
        self.init_called = False
        self.check_calls: list[bool] = []
        self.run_calls: list[bool] = []
        self.closed = False

    def init(self) -> bool:
        self.init_called = True
        return self.init_ok

    def check_once(self, *, dry_run: bool = False) -> int:
        self.check_calls.append(dry_run)
        return self.count

    def run(self, *, dry_run: bool = False) -> None:
        self.run_calls.append(dry_run)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[..., FakeMonitor]:
    """把 cli 的 AppConfig / Monitor 换成本地假对象，返回一个 install() 工厂"""
    config = AppConfig(scraper=ScraperConfig(target_urls=[]), data_dir=tmp_path)
    monkeypatch.setattr(
        config_module.AppConfig, "from_env", staticmethod(lambda env_file=None: config)
    )

    def install(*, init_ok: bool = True, count: int = 0) -> FakeMonitor:
        monitor = FakeMonitor(init_ok=init_ok, count=count, config=config)
        monkeypatch.setattr(cli, "Monitor", lambda _config: monitor)
        return monitor

    return install


class TestParser:
    def test_init_and_once_are_mutually_exclusive(self) -> None:
        parser = cli.build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--init", "--once"])
        assert exc.value.code == 2

    def test_dry_run_allowed_with_once(self) -> None:
        args = cli.build_parser().parse_args(["--once", "--dry-run"])
        assert args.once is True
        assert args.dry_run is True

    def test_version(self) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--version"])
        assert exc.value.code == 0


class TestMain:
    def test_init_success_returns_zero(self, harness: Callable[..., FakeMonitor]) -> None:
        monitor = harness(init_ok=True)
        assert cli.main(["--init"]) == 0
        assert monitor.init_called is True
        assert monitor.closed is True

    def test_init_failure_returns_nonzero(self, harness: Callable[..., FakeMonitor]) -> None:
        harness(init_ok=False)
        assert cli.main(["--init"]) == 1

    def test_once_passes_dry_run(self, harness: Callable[..., FakeMonitor]) -> None:
        monitor = harness(count=3)
        assert cli.main(["--once", "--dry-run"]) == 0
        assert monitor.check_calls == [True]
        assert monitor.run_calls == []

    def test_once_without_dry_run(self, harness: Callable[..., FakeMonitor]) -> None:
        monitor = harness(count=0)
        assert cli.main(["--once"]) == 0
        assert monitor.check_calls == [False]

    def test_default_enters_daemon(self, harness: Callable[..., FakeMonitor]) -> None:
        monitor = harness()
        assert cli.main([]) == 0
        assert monitor.run_calls == [False]
        assert monitor.closed is True

    def test_config_error_returns_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(env_file: object = None) -> AppConfig:
            raise ConfigError("SMTP_PORT 必须是整数")

        monkeypatch.setattr(config_module.AppConfig, "from_env", staticmethod(boom))
        assert cli.main(["--once"]) == 2

    def test_close_called_even_on_exception(
        self, harness: Callable[..., FakeMonitor], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monitor = harness()

        def explode(*, dry_run: bool = False) -> int:
            raise RuntimeError("boom")

        monkeypatch.setattr(monitor, "check_once", explode)
        with pytest.raises(RuntimeError):
            cli.main(["--once"])
        assert monitor.closed is True
