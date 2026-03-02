"""
爬虫模块 - 从上海交通大学教务处网站抓取公告（支持多个页面、多种布局）
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from web_bugger.config import ScraperConfig
from web_bugger.models import Announcement

logger = logging.getLogger(__name__)


class Scraper:
    """教务处网站公告爬虫"""

    def __init__(self, config: ScraperConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers.update(config.headers)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def fetch(self) -> list[Announcement]:
        """
        依次抓取所有配置的目标页面，合并去重后返回。

        Returns:
            公告列表（未排序）
        """
        all_items: list[Announcement] = []
        seen_urls: set[str] = set()

        for url in self._config.target_urls:
            html = self._download(url)
            if html is None:
                continue
            items = self._parse(html, url)
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)

        logger.info(
            "共抓取到 %d 条公告（来自 %d 个页面）",
            len(all_items),
            len(self._config.target_urls),
        )
        return all_items

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    def _download(self, url: str) -> str | None:
        """下载目标页面 HTML"""
        try:
            resp = self._session.get(url, timeout=self._config.request_timeout)
            resp.encoding = "utf-8"
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.error("请求页面失败 (%s): %s", url, e)
            return None

    # ------------------------------------------------------------------
    # parse — 自动检测页面布局并分派
    # ------------------------------------------------------------------

    def _parse(self, html: str, page_url: str) -> list[Announcement]:
        """根据页面结构自动选择解析策略"""
        soup = BeautifulSoup(html, "html.parser")

        # 布局 A: xwtg.htm 板块式（多个 div.w50l / div.w50r，每个含 Newslist1）
        sections = soup.select("div.w50l, div.w50r")
        if sections:
            return self._parse_xwtg(soup, sections)

        # 布局 B: mxxsdtz.htm 列表式（单个 div.Newslist > ul > li.clearfix）
        newslist = soup.select_one("div.Newslist")
        if newslist:
            return self._parse_mxxsdtz(soup, newslist, page_url)

        logger.warning("未识别的页面布局: %s", page_url)
        return []

    # ------------------------------------------------------------------
    # 布局 A — xwtg.htm 板块式
    # ------------------------------------------------------------------

    def _parse_xwtg(
        self, soup: BeautifulSoup, sections: list[Tag]
    ) -> list[Announcement]:
        results: list[Announcement] = []
        for section_div in sections:
            section_name = self._extract_section_name(section_div)
            items: list[Tag] = section_div.select("div.Newslist1 ul li")
            for item in items:
                a = self._parse_xwtg_item(item, section_name)
                if a is not None:
                    results.append(a)
        return results

    @staticmethod
    def _extract_section_name(section_div: Tag) -> str:
        tag = section_div.select_one("div.nytit2 h2")
        return tag.get_text(strip=True) if tag else "未知板块"

    def _parse_xwtg_item(self, item: Tag, section: str) -> Announcement | None:
        link_tag = item.select_one("a")
        if link_tag is None:
            return None

        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")

        date_span = item.select_one("span")
        date = date_span.get_text(strip=True) if date_span else ""

        url = self._resolve_url(str(href))
        return Announcement(title=title, url=url, date=date, section=section)

    # ------------------------------------------------------------------
    # 布局 B — mxxsdtz.htm 列表式
    # ------------------------------------------------------------------

    def _parse_mxxsdtz(
        self, soup: BeautifulSoup, newslist: Tag, page_url: str
    ) -> list[Announcement]:
        """
        页面结构:
          <div class="Newslist"><ul>
            <li class="clearfix">
              <div class="sj"><h2>日</h2><p>年.月</p></div>
              <div class="wz"><a href="..."><h2>标题</h2></a><p>摘要</p></div>
            </li>
          </ul></div>
        """
        # 从页面标题推断板块名
        title_tag = soup.select_one("div.nytit1")
        section_name = title_tag.get_text(strip=True) if title_tag else "面向学生的通知"

        results: list[Announcement] = []
        items: list[Tag] = newslist.select("ul > li.clearfix")
        for item in items:
            a = self._parse_mxxsdtz_item(item, section_name)
            if a is not None:
                results.append(a)
        return results

    def _parse_mxxsdtz_item(self, item: Tag, section: str) -> Announcement | None:
        wz = item.select_one("div.wz")
        if wz is None:
            return None
        link_tag = wz.select_one("a")
        if link_tag is None:
            return None

        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")

        # 日期组合: <div class="sj"><h2>02</h2><p>2026.03</p></div> → 2026-03-02
        date = self._extract_mxxsdtz_date(item)

        url = self._resolve_url(str(href))
        return Announcement(title=title, url=url, date=date, section=section)

    @staticmethod
    def _extract_mxxsdtz_date(item: Tag) -> str:
        sj = item.select_one("div.sj")
        if sj is None:
            return ""
        day_tag = sj.select_one("h2")
        ym_tag = sj.select_one("p")
        if day_tag is None or ym_tag is None:
            return ""
        day = day_tag.get_text(strip=True).zfill(2)
        ym = ym_tag.get_text(strip=True)  # e.g. "2026.03"
        # 转换为 "2026-03-02"
        match = re.match(r"(\d{4})\.(\d{2})", ym)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{day}"
        return f"{ym}-{day}"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _resolve_url(self, href: str) -> str:
        """将相对链接解析为绝对链接"""
        if href and not href.startswith("http"):
            return urljoin(self._config.base_url, href)
        return href
