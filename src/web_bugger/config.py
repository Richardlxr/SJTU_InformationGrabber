"""
配置管理 - 从环境变量和 .env 文件加载配置，使用 dataclass 统一管理
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class SmtpConfig:
    """SMTP 邮件服务器配置（支持 QQ / 163 / Gmail / Outlook 等任意 SMTP 服务）"""

    server: str = "smtp.qq.com"
    port: int = 465
    use_ssl: bool = True
    sender_email: str = ""
    sender_password: str = ""
    receiver_email: str = ""

    @property
    def is_configured(self) -> bool:
        """发件人邮箱、授权码、收件人是否均已配置"""
        return bool(self.sender_email and self.sender_password and self.receiver_email)


@dataclass
class ScraperConfig:
    """爬虫配置"""

    target_urls: list[str] = field(
        default_factory=lambda: [
            "https://jwc.sjtu.edu.cn/xwtg.htm",
            "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm",
        ]
    )
    base_url: str = "https://jwc.sjtu.edu.cn/"
    request_timeout: int = 15
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
    data_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent
    )

    @property
    def seen_file(self) -> Path:
        """已读公告存储文件路径"""
        return self.data_dir / "seen_announcements.json"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> AppConfig:
        """
        从环境变量（及可选的 .env 文件）加载配置。

        Args:
            env_file: .env 文件路径，为 None 时自动搜索项目根目录

        Returns:
            填充好的 AppConfig 实例
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        smtp = SmtpConfig(
            server=os.getenv("SMTP_SERVER", "smtp.qq.com"),
            port=int(os.getenv("SMTP_PORT", "465")),
            use_ssl=os.getenv("SMTP_USE_SSL", "true").lower() == "true",
            sender_email=os.getenv("SENDER_EMAIL", ""),
            sender_password=os.getenv("SENDER_PASSWORD", ""),
            receiver_email=os.getenv("RECEIVER_EMAIL", ""),
        )

        # 支持逗号分隔的多个 URL
        urls_str = os.getenv(
            "TARGET_URLS",
            "https://jwc.sjtu.edu.cn/xwtg.htm,https://jwc.sjtu.edu.cn/index/mxxsdtz.htm",
        )
        target_urls = [u.strip() for u in urls_str.split(",") if u.strip()]

        scraper = ScraperConfig(
            target_urls=target_urls,
            base_url=os.getenv("BASE_URL", "https://jwc.sjtu.edu.cn/"),
        )

        check_interval = int(os.getenv("CHECK_INTERVAL", "300"))

        data_dir_str = os.getenv("DATA_DIR", "")
        if data_dir_str:
            data_dir = Path(data_dir_str)
        else:
            data_dir = Path(__file__).resolve().parent.parent.parent

        return cls(
            smtp=smtp,
            scraper=scraper,
            check_interval=check_interval,
            data_dir=data_dir,
        )
