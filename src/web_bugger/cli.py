"""
CLI 入口 - 命令行解析与日志初始化
"""

from __future__ import annotations

import argparse
import logging
import sys

from web_bugger.config import AppConfig
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
        description="上海交通大学教务处公告监控 & 邮件通知工具",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="首次运行：抓取当前所有公告并标记为已读，不发送邮件",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只检查一次就退出（不进入持续监控模式）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行：只打印新公告，不发送邮件",
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
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    _setup_logging(verbose=args.verbose)
    logger = logging.getLogger("web_bugger")

    config = AppConfig.from_env(env_file=args.env_file)
    monitor = Monitor(config)

    if args.init:
        monitor.init()
        logger.info("初始化完成！后续运行将只通知新公告。")
        sys.exit(0)

    if args.once:
        count = monitor.check_once(dry_run=args.dry_run)
        logger.info("单次检查完成，发现 %d 条新公告", count)
        sys.exit(0)

    # 默认：持续守护
    monitor.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
