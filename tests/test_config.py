"""
单元测试 - config（环境变量解析与校验）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web_bugger.config import AppConfig, ConfigError, SmtpConfig

_ENV_KEYS = [
    "SMTP_SERVER",
    "SMTP_PORT",
    "SMTP_USE_SSL",
    "SMTP_TIMEOUT",
    "SENDER_EMAIL",
    "SENDER_PASSWORD",
    "RECEIVER_EMAIL",
    "TARGET_URLS",
    "BASE_URL",
    "REQUEST_TIMEOUT",
    "MAX_RETRIES",
    "MAX_WORKERS",
    "MAX_PAGES",
    "CHECK_INTERVAL",
    "FAILURE_ALERT_THRESHOLD",
    "DATA_DIR",
    "XDG_DATA_HOME",
]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免 .env / 真实环境变量在测试之间泄漏"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "test.env"
    path.write_text(content, encoding="utf-8")
    return path


class TestSmtpConfig:
    def test_is_configured(self) -> None:
        assert SmtpConfig().is_configured is False
        cfg = SmtpConfig(sender_email="a@qq.com", sender_password="x", receiver_email="b@qq.com")
        assert cfg.is_configured is True

    def test_recipients_single(self) -> None:
        assert SmtpConfig(receiver_email="a@qq.com").recipients == ["a@qq.com"]

    def test_recipients_multiple_with_separators(self) -> None:
        cfg = SmtpConfig(receiver_email=" a@qq.com, b@qq.com ;c@qq.com ,, ")
        assert cfg.recipients == ["a@qq.com", "b@qq.com", "c@qq.com"]
        assert cfg.is_configured is False  # 缺 sender/password

    def test_recipients_empty(self) -> None:
        assert SmtpConfig(receiver_email="  ,  ").recipients == []


class TestFromEnv:
    def test_reads_values_from_env_file(self, tmp_path: Path) -> None:
        env = _write_env(
            tmp_path,
            "\n".join(
                [
                    "SMTP_SERVER=smtp.163.com",
                    "SMTP_PORT=587",
                    "SMTP_USE_SSL=false",
                    "SENDER_EMAIL=sender@163.com",
                    "SENDER_PASSWORD=secret",
                    "RECEIVER_EMAIL=me@163.com",
                    "CHECK_INTERVAL=600",
                    "FAILURE_ALERT_THRESHOLD=5",
                    "MAX_RETRIES=1",
                    "MAX_WORKERS=2",
                    "MAX_PAGES=7",
                    "REQUEST_TIMEOUT=30",
                    "DATA_DIR=" + str(tmp_path / "data"),
                ]
            ),
        )
        cfg = AppConfig.from_env(env)

        assert cfg.smtp.server == "smtp.163.com"
        assert cfg.smtp.port == 587
        assert cfg.smtp.use_ssl is False
        assert cfg.smtp.receiver_email == "me@163.com"
        assert cfg.check_interval == 600
        assert cfg.failure_alert_threshold == 5
        assert cfg.scraper.max_retries == 1
        assert cfg.scraper.max_workers == 2
        assert cfg.scraper.max_pages == 7
        assert cfg.scraper.request_timeout == 30
        assert cfg.data_dir == tmp_path / "data"
        assert cfg.seen_file == tmp_path / "data" / "seen_announcements.json"

    def test_target_urls_are_split_and_stripped(self, tmp_path: Path) -> None:
        env = _write_env(
            tmp_path,
            "TARGET_URLS=https://a.com/1.htm, https://b.com/2.htm ,\n",
        )
        cfg = AppConfig.from_env(env)
        assert cfg.scraper.target_urls == ["https://a.com/1.htm", "https://b.com/2.htm"]

    def test_empty_target_urls_falls_back_to_defaults(self, tmp_path: Path) -> None:
        env = _write_env(tmp_path, "TARGET_URLS=  ,  ,")
        cfg = AppConfig.from_env(env)
        assert len(cfg.scraper.target_urls) == 6

    def test_defaults_when_env_file_is_almost_empty(self, tmp_path: Path) -> None:
        cfg = AppConfig.from_env(_write_env(tmp_path, "# nothing here\n"))
        assert cfg.smtp.server == "smtp.qq.com"
        assert cfg.smtp.port == 465
        assert cfg.smtp.use_ssl is True
        assert cfg.check_interval == 300

    def test_missing_env_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="不存在"):
            AppConfig.from_env(tmp_path / "nope.env")

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ("SMTP_PORT=notanumber", "SMTP_PORT"),
            ("SMTP_PORT=0", "SMTP_PORT"),
            ("SMTP_PORT=70000", "SMTP_PORT"),
            ("CHECK_INTERVAL=abc", "CHECK_INTERVAL"),
            ("CHECK_INTERVAL=1", "CHECK_INTERVAL"),
            ("MAX_WORKERS=0", "MAX_WORKERS"),
            ("FAILURE_ALERT_THRESHOLD=0", "FAILURE_ALERT_THRESHOLD"),
            ("SMTP_TIMEOUT=-1", "SMTP_TIMEOUT"),
        ],
    )
    def test_invalid_values_raise_config_error(
        self, tmp_path: Path, content: str, match: str
    ) -> None:
        with pytest.raises(ConfigError, match=match):
            AppConfig.from_env(_write_env(tmp_path, content))

    def test_bool_parsing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for raw, expected in [
            ("true", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("no", False),
        ]:
            # load_dotenv 默认不覆盖已存在的环境变量，逐轮清理才能真实生效
            monkeypatch.delenv("SMTP_USE_SSL", raising=False)
            env = _write_env(tmp_path, f"SMTP_USE_SSL={raw}\n")
            assert AppConfig.from_env(env).smtp.use_ssl is expected, raw


class TestDataDir:
    def test_xdg_fallback_outside_checkout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import web_bugger.config as config_module

        fake_config = str(tmp_path / "lib" / "web_bugger" / "config.py")
        monkeypatch.setattr(config_module, "__file__", fake_config)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        cfg = AppConfig.from_env(_write_env(tmp_path, "\n"))
        assert cfg.data_dir == tmp_path / "xdg" / "web-bugger"

    def test_home_fallback_when_no_xdg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import web_bugger.config as config_module

        fake_config = str(tmp_path / "lib" / "web_bugger" / "config.py")
        monkeypatch.setattr(config_module, "__file__", fake_config)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        cfg = AppConfig.from_env(_write_env(tmp_path, "\n"))
        assert cfg.data_dir == tmp_path / "home" / ".local" / "share" / "web-bugger"
