"""
配置管理 - 从环境变量和 .env 文件加载配置，使用 dataclass 统一管理
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(ValueError):
    """配置项非法（例如端口不是数字、间隔为负数）"""


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """读取整型环境变量，非法时抛出带变量名的 ConfigError。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as e:
        raise ConfigError(f"环境变量 {name} 必须是整数，当前值为 {raw!r}") from e
    if not minimum <= value <= maximum:
        raise ConfigError(f"环境变量 {name} 必须在 [{minimum}, {maximum}] 之间，当前值为 {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _split_addresses(value: str) -> list[str]:
    """把 "a@x.com, b@y.com; c@z.com" 拆成地址列表"""
    return [part.strip() for part in re.split(r"[,;]+", value) if part.strip()]


def _default_data_dir() -> Path:
    """
    状态文件存放目录。

    - 显式配置了 DATA_DIR 时优先使用；
    - 否则若本模块位于源码检出目录（同级存在 pyproject.toml）则放在项目根目录，
      保持与旧版本一致的行为、方便查看；
    - 否则（例如 pip 安装进 site-packages）退回 XDG 数据目录，
      避免写入只读/升级即丢失的位置。
    """
    override = os.getenv("DATA_DIR", "")
    if override.strip():
        return Path(override.strip()).expanduser()

    checkout_root = Path(__file__).resolve().parent.parent.parent
    if (checkout_root / "pyproject.toml").is_file():
        return checkout_root

    xdg = os.getenv("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "web-bugger"


@dataclass
class SmtpConfig:
    """SMTP 邮件服务器配置（支持 QQ / 163 / Gmail / Outlook 等任意 SMTP 服务）"""

    server: str = "smtp.qq.com"
    port: int = 465
    use_ssl: bool = True
    sender_email: str = ""
    sender_password: str = ""
    receiver_email: str = ""
    timeout: int = 20

    @property
    def recipients(self) -> list[str]:
        """收件人列表（支持逗号/分号分隔的多个地址）"""
        return _split_addresses(self.receiver_email)

    @property
    def is_configured(self) -> bool:
        """发件人邮箱、授权码、收件人是否均已配置"""
        return bool(self.sender_email and self.sender_password and self.recipients)


@dataclass
class ScraperConfig:
    """爬虫配置"""

    target_urls: list[str] = field(
        default_factory=lambda: [
            # 教务处「面向学生的通知」（选课 / 考试 / 竞赛等）
            "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm",
            # 计算机学院学生工作通知公告（党建德育 / 团学工作 / 学生事务 / 职业发展）
            "https://cs.sjtu.edu.cn/xsgz-tzgg-djdy.html",
            "https://cs.sjtu.edu.cn/xsgz-tzgg-txgz.html",
            "https://cs.sjtu.edu.cn/xsgz-tzgg-xssw.html",
            "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html",
            # 致远学院（讲座活动 / 通知公告，AJAX 接口）
            "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/events_list.php",
            "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/announcement_list.php",
        ]
    )
    base_url: str = "https://jwc.sjtu.edu.cn/"
    request_timeout: int = 15
    max_retries: int = 3
    max_workers: int = 4
    max_pages: int = 50
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )


@dataclass
class AppConfig:
    """应用总配置"""

    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    check_interval: int = 300
    failure_alert_threshold: int = 3
    failure_alert_interval: int = 86400
    data_dir: Path = field(default_factory=_default_data_dir)

    @property
    def seen_file(self) -> Path:
        """已读公告存储文件路径"""
        return self.data_dir / "seen_announcements.json"

    @property
    def alert_state_file(self) -> Path:
        """抓取失败告警状态文件路径"""
        return self.data_dir / "alert_state.json"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> AppConfig:
        """
        从环境变量（及可选的 .env 文件）加载配置。

        Args:
            env_file: .env 文件路径，为 None 时自动搜索当前目录及其父目录

        Returns:
            填充好的 AppConfig 实例

        Raises:
            ConfigError: 配置项非法（类型错误 / 超出范围）
        """
        if env_file:
            path = Path(env_file).expanduser()
            if not path.is_file():
                raise ConfigError(f"指定的 .env 文件不存在: {path}")
            load_dotenv(path)
        else:
            load_dotenv()

        smtp = SmtpConfig(
            server=_env_str("SMTP_SERVER", "smtp.qq.com"),
            port=_env_int("SMTP_PORT", 465, minimum=1, maximum=65535),
            use_ssl=_env_bool("SMTP_USE_SSL", True),
            sender_email=_env_str("SENDER_EMAIL", ""),
            sender_password=_env_str("SENDER_PASSWORD", ""),
            receiver_email=_env_str("RECEIVER_EMAIL", ""),
            timeout=_env_int("SMTP_TIMEOUT", 20, minimum=1, maximum=300),
        )

        default_urls = ",".join(ScraperConfig().target_urls)
        urls_str = _env_str("TARGET_URLS", default_urls)
        target_urls = [u.strip() for u in urls_str.split(",") if u.strip()]

        scraper = ScraperConfig(
            target_urls=target_urls or list(ScraperConfig().target_urls),
            base_url=_env_str("BASE_URL", "https://jwc.sjtu.edu.cn/"),
            request_timeout=_env_int("REQUEST_TIMEOUT", 15, minimum=1, maximum=300),
            max_retries=_env_int("MAX_RETRIES", 3, minimum=0, maximum=10),
            max_workers=_env_int("MAX_WORKERS", 4, minimum=1, maximum=16),
            max_pages=_env_int("MAX_PAGES", 50, minimum=1, maximum=1000),
        )

        return cls(
            smtp=smtp,
            scraper=scraper,
            check_interval=_env_int("CHECK_INTERVAL", 300, minimum=10, maximum=86400),
            failure_alert_threshold=_env_int("FAILURE_ALERT_THRESHOLD", 3, minimum=1, maximum=100),
            failure_alert_interval=_env_int(
                "FAILURE_ALERT_INTERVAL", 86400, minimum=60, maximum=30 * 86400
            ),
            data_dir=_default_data_dir(),
        )
