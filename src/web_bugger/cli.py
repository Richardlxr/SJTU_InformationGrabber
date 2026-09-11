"""
CLI 入口 - 命令行解析与日志初始化
"""

from __future__ import annotations

import argparse
import logging
import sys

from web_bugger import __version__
from web_bugger.config import AppConfig, ConfigError
from web_bugger.monitor import Monitor


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web-bugger",
        description="上海交通大学教务处/计算机学院公告监控 & 邮件通知工具",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--init",
        action="store_true",
        help="首次运行：抓取当前所有公告并标记为已读，不发送邮件",
    )
    mode.add_argument(
        "--once",
        action="store_true",
        help="只检查一次就退出（不进入持续监控模式）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行：只打印新公告，不发送邮件、不写入已读状态",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="输出调试级别日志",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="指定 .env 文件路径（默认自动搜索）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    _setup_logging(verbose=args.verbose)
    logger = logging.getLogger("web_bugger")

    try:
        config = AppConfig.from_env(env_file=args.env_file)
    except ConfigError as e:
        logger.error("配置错误: %s", e)
        return 2

    logger.debug("状态文件: %s", config.seen_file)
    monitor = Monitor(config)

    try:
        if args.init:
            if not monitor.init():
                logger.error("初始化失败：未抓取到任何公告，未标记任何已读")
                return 1
            logger.info("初始化完成！后续运行将只通知新公告。")
            return 0

        if args.once:
            count = monitor.check_once(dry_run=args.dry_run)
            logger.info("单次检查完成，发现 %d 条新公告", count)
            return 0

        # 默认：持续守护
        monitor.run(dry_run=args.dry_run)
        return 0
    finally:
        close = getattr(monitor, "close", None)
        if callable(close):
            close()


def _entrypoint() -> None:
    """console_scripts 入口（把返回值转成退出码）"""
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()
