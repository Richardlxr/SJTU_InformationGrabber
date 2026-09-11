"""
爬虫模块 - 从上海交通大学教务处/计算机学院网站抓取公告（支持多个页面、多种布局）

支持布局:
  A — jwc.sjtu.edu.cn 教务处板块式
  B — jwc.sjtu.edu.cn 面向学生通知列表式
  C — cs.sjtu.edu.cn  计算机学院学生工作 AJAX 分页列表式
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from web_bugger.config import ScraperConfig
from web_bugger.models import Announcement

logger = logging.getLogger(__name__)

# requests 在响应头缺少 charset 时会默认用 ISO-8859-1 解码，
# 下列取值都需要改用内容嗅探，否则中文会乱码。
_UNTRUSTED_ENCODINGS = {"", "iso-8859-1", "latin-1", "ascii", "us-ascii"}

_INVALID_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:")

# 布局 D: zhiyuan.sjtu.edu.cn 致远学院 AJAX 接口
_ZHIYUAN_API_BASE = "https://zhiyuan.sjtu.edu.cn/api/"
_ZHIYUAN_VIEW_BASE = "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/"
_ZHIYUAN_PAGE_SIZE = 50
# 这两个接口历史很深（通知 1141 条 / 活动 637 条），而新条目永远排在最前，
# 所以只取最近几页即可：既覆盖新公告，又不必每轮拉全量历史。
_ZHIYUAN_RECENT_PAGES = 2
# 活动分类 523 的详情不在本院站点，而在 ins.sjtu.edu.cn（与官网 JS 一致）
_ZHIYUAN_INS_CATEGORY = 523
_ZHIYUAN_INS_BASE = "https://ins.sjtu.edu.cn/seminars/"


@dataclass(frozen=True)
class FetchResult:
    """一次抓取的整体结果（用于区分「没有公告」和「抓取失败」）"""

    items: list[Announcement] = field(default_factory=list)
    pages_total: int = 0
    failed_urls: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failed_urls

    @property
    def all_failed(self) -> bool:
        return self.pages_total > 0 and len(self.failed_urls) == self.pages_total


class Scraper:
    """教务处/计算机学院网站公告爬虫"""

    # 布局 C: cs.sjtu.edu.cn 学生工作 AJAX 接口
    _CS_SJTU_AJAX_URL = "https://cs.sjtu.edu.cn/active/ajax_type_list.html"
    _CS_SJTU_CAT_SECTION: dict[str, str] = {
        "xsgz-tzgg-djdy": "党建德育",
        "xsgz-tzgg-txgz": "团学工作",
        "xsgz-tzgg-xssw": "学生事务",
        "xsgz-tzgg-zyfz": "职业发展",
    }

    def __init__(self, config: ScraperConfig) -> None:
        self._config = config
        self._session = self._build_session(config)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session(config: ScraperConfig) -> requests.Session:
        session = requests.Session()
        session.headers.update(config.headers)
        retry = Retry(
            total=config.max_retries,
            connect=config.max_retries,
            read=config.max_retries,
            status=config.max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry, pool_connections=config.max_workers, pool_maxsize=config.max_workers
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def close(self) -> None:
        """释放连接池"""
        self._session.close()

    def __enter__(self) -> Scraper:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def fetch(self) -> list[Announcement]:
        """
        依次抓取所有配置的目标页面，合并去重后返回。

        Returns:
            公告列表（未排序）；抓取失败的页面会被跳过
        """
        return self.fetch_with_status().items

    def fetch_with_status(self) -> FetchResult:
        """
        抓取所有目标页面，并返回失败页面信息。

        多个页面并发抓取；输出顺序按 `target_urls` 顺序稳定排列。
        """
        urls = list(self._config.target_urls)
        if not urls:
            logger.warning("未配置任何目标页面（TARGET_URLS 为空）")
            return FetchResult()

        per_url: dict[str, list[Announcement]] = {}
        failed: list[str] = []
        workers = max(1, min(self._config.max_workers, len(urls)))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_one, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    items = future.result()
                except Exception:
                    logger.exception("抓取页面时出现未预期错误: %s", url)
                    failed.append(url)
                    continue
                if items is None:
                    failed.append(url)
                else:
                    per_url[url] = items

        all_items: list[Announcement] = []
        seen_urls: set[str] = set()
        for url in urls:
            for item in per_url.get(url, []):
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)

        if failed:
            logger.warning("%d/%d 个页面抓取失败: %s", len(failed), len(urls), ", ".join(failed))
        logger.info(
            "共抓取到 %d 条公告（来自 %d 个页面，成功 %d 个）",
            len(all_items),
            len(urls),
            len(urls) - len(failed),
        )
        return FetchResult(items=all_items, pages_total=len(urls), failed_urls=tuple(failed))

    def _fetch_one(self, url: str) -> list[Announcement] | None:
        """下载并解析单个页面；失败返回 None"""
        html = self._download(url)
        if html is None:
            return None
        return self._parse(html, url)

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    def _download(self, url: str) -> str | None:
        """下载目标页面 HTML"""
        try:
            resp = self._session.get(url, timeout=self._config.request_timeout)
            resp.raise_for_status()
            return self._decode(resp)
        except requests.RequestException as e:
            logger.error("请求页面失败 (%s): %s", url, e)
            return None

    @staticmethod
    def _decode(resp: requests.Response) -> str:
        """
        按响应解码正文。

        SJTU 站点在 HTTP 头里通常不带 charset（requests 会退回 ISO-8859-1），
        因此对这类不可信默认值改用内容嗅探，避免中文标题变成乱码。
        """
        declared = (resp.encoding or "").strip().lower()
        if declared in _UNTRUSTED_ENCODINGS:
            detected = resp.apparent_encoding
            if detected:
                logger.debug(
                    "%s 响应头未声明 charset（encoding=%r），改用嗅探结果 %s",
                    resp.url,
                    resp.encoding,
                    detected,
                )
                text: str = resp.content.decode(detected, errors="replace")
                return text
        body: str = resp.text
        return body

    # ------------------------------------------------------------------
    # parse — 自动检测页面布局并分派
    # ------------------------------------------------------------------

    def _parse(self, html: str, page_url: str) -> list[Announcement]:
        """根据页面结构自动选择解析策略"""
        soup = BeautifulSoup(html, "html.parser")

        # 布局 C: cs.sjtu.edu.cn 计算机学院 AJAX 动态加载列表
        if soup.select_one("div#article_list") is not None:
            return self._parse_cs_sjtu(html, soup, page_url)

        # 布局 D: zhiyuan.sjtu.edu.cn 致远学院 AJAX 动态加载列表
        if soup.select_one("div.events-list") is not None:
            return self._parse_zhiyuan_events(page_url)
        if soup.select_one("div.announcement-list") is not None:
            return self._parse_zhiyuan_announcements(page_url)

        # 布局 A: xwtg.htm 板块式（多个 div.w50l / div.w50r，每个含 Newslist1）
        sections = soup.select("div.w50l, div.w50r")
        if sections:
            return self._parse_xwtg(soup, sections, page_url)

        # 布局 B: mxxsdtz.htm 列表式（单个 div.Newslist > ul > li.clearfix）
        newslist = soup.select_one("div.Newslist")
        if newslist:
            return self._parse_mxxsdtz(soup, newslist, page_url)

        logger.warning("未识别的页面布局: %s", page_url)
        return []

    # ------------------------------------------------------------------
    # 布局 C — cs.sjtu.edu.cn 计算机学院 AJAX 列表式
    # ------------------------------------------------------------------

    def _parse_cs_sjtu(self, html: str, soup: BeautifulSoup, page_url: str) -> list[Announcement]:
        """
        页面使用 AJAX POST 接口动态加载通知列表：
          POST https://cs.sjtu.edu.cn/active/ajax_type_list.html
          Params: page, cat_code, type, search, extend_id, template
        支持自动翻页直至取得全部公告。
        """
        m = re.search(r"cat_code\s*:\s*['\"]([^'\"]+)['\"]", html)
        if m is None:
            logger.warning("cs.sjtu 页面未找到 cat_code，跳过: %s", page_url)
            return []
        cat_code = m.group(1)

        section_name = self._CS_SJTU_CAT_SECTION.get(cat_code)
        if section_name is None:
            active_a = soup.select_one("div.swiper-slide a.on")
            section_name = active_a.get_text(strip=True) if active_a else cat_code

        logger.info("cs.sjtu 板块 [%s] cat_code=%s", section_name, cat_code)
        return self._fetch_all_cs_sjtu_pages(cat_code, section_name, page_url)

    def _fetch_all_cs_sjtu_pages(
        self, cat_code: str, section: str, referer: str
    ) -> list[Announcement]:
        """
        分页 POST AJAX 接口，取出所有条目。

        必须设置页数上限：若服务端忽略 page 参数（或返回错误的 count），
        `len(results) >= total` 永远不成立，会无限翻页把守护进程卡死。
        """
        results: list[Announcement] = []
        seen_urls: set[str] = set()
        max_pages = max(1, self._config.max_pages)
        page = 1

        while page <= max_pages:
            try:
                resp = self._session.post(
                    self._CS_SJTU_AJAX_URL,
                    data={
                        "page": page,
                        "cat_code": cat_code,
                        "type": "",
                        "search": "",
                        "extend_id": "0",
                        "template": "ajax_news_list1_search",
                    },
                    timeout=self._config.request_timeout,
                    headers={
                        "Referer": referer,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                logger.error("cs.sjtu AJAX 请求失败 (cat=%s, page=%d): %s", cat_code, page, e)
                break

            if not isinstance(data, dict):
                logger.error("cs.sjtu AJAX 返回结构异常 (cat=%s): %r", cat_code, data)
                break

            content_html = data.get("content", "")
            if not isinstance(content_html, str) or not content_html:
                break

            items = BeautifulSoup(content_html, "html.parser").find_all("li")
            if not items:
                break

            for item in items:
                ann = self._parse_cs_sjtu_item(item, section, referer)
                if ann is not None and ann.url not in seen_urls:
                    seen_urls.add(ann.url)
                    results.append(ann)

            try:
                total = int(data.get("count", 0))
            except (TypeError, ValueError):
                total = 0
            if len(results) >= total:
                break
            page += 1
        else:
            logger.warning(
                "cs.sjtu [%s] 达到最大翻页数 %d，可能仍有遗漏（count 与实际不一致？）",
                section,
                max_pages,
            )

        logger.debug("cs.sjtu [%s] 共抓取 %d 条（%d 页）", section, len(results), page)
        return results

    @classmethod
    def _parse_cs_sjtu_item(cls, item: Tag, section: str, base_url: str) -> Announcement | None:
        """
        HTML 结构:
          <li>
            <a href="https://cs.sjtu.edu.cn/...">
              <div class="time"><p>05</p><span>2025-11</span></div>
              <div class="tit line-2">标题</div>
            </a>
          </li>
        """
        link = item.select_one("a")
        if link is None:
            return None
        url = cls._normalize_href(str(link.get("href", "")), base_url)
        if url is None:
            return None

        tit_div = link.select_one("div.tit")
        title = tit_div.get_text(strip=True) if tit_div else link.get_text(strip=True)
        if not title:
            return None

        # 日期: <div class="time"><p>05</p><span>2025-11</span></div> → 2025-11-05
        date = ""
        time_div = link.select_one("div.time")
        if time_div:
            day_tag = time_div.select_one("p")
            ym_tag = time_div.select_one("span")
            day = day_tag.get_text(strip=True).zfill(2) if day_tag else "01"
            ym = ym_tag.get_text(strip=True) if ym_tag else ""
            date = f"{ym}-{day}" if ym else ""

        return Announcement(title=title, url=url, date=date, section=section)

    # ------------------------------------------------------------------
    # 布局 D — zhiyuan.sjtu.edu.cn 致远学院 AJAX 列表式
    # ------------------------------------------------------------------

    def _parse_zhiyuan_events(self, page_url: str) -> list[Announcement]:
        """讲座/活动：GET /api/get_event_by_category"""
        return self._fetch_zhiyuan_pages(
            endpoint="get_event_by_category",
            list_key="events",
            section="致远讲座活动",
            parse_item=self._zhiyuan_event_item,
            extra_params={"active": 0},
            referer=page_url,
        )

    def _parse_zhiyuan_announcements(self, page_url: str) -> list[Announcement]:
        """通知公告：GET /api/get_announcements_by_category"""
        return self._fetch_zhiyuan_pages(
            endpoint="get_announcements_by_category",
            list_key="announcements",
            section="致远通知公告",
            parse_item=self._zhiyuan_announcement_item,
            extra_params={},
            referer=page_url,
        )

    def _fetch_zhiyuan_pages(
        self,
        *,
        endpoint: str,
        list_key: str,
        section: str,
        parse_item: Callable[[dict[str, Any], str], Announcement | None],
        extra_params: dict[str, Any],
        referer: str,
    ) -> list[Announcement]:
        """分页读取致远学院 JSON 接口（只取最近若干页，见 _ZHIYUAN_RECENT_PAGES）"""
        results: list[Announcement] = []
        seen_urls: set[str] = set()
        max_pages = max(1, min(self._config.max_pages, _ZHIYUAN_RECENT_PAGES))

        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {
                "num": _ZHIYUAN_PAGE_SIZE,
                "category": -1,
                "page": page,
                **extra_params,
            }
            try:
                resp = self._session.get(
                    f"{_ZHIYUAN_API_BASE}{endpoint}",
                    params=params,
                    timeout=self._config.request_timeout,
                    headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"},
                )
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                logger.error("zhiyuan AJAX 请求失败 (%s, page=%d): %s", endpoint, page, e)
                break

            if not isinstance(data, dict) or data.get("status") != 200:
                logger.error("zhiyuan AJAX 返回异常 (%s, page=%d): %r", endpoint, page, data)
                break

            raw_items = data.get(list_key)
            if not isinstance(raw_items, list):
                break
            # 接口偶尔会混入 null 元素（如 announcements 数组）
            items = [it for it in raw_items if isinstance(it, dict)]
            if not items:
                break

            for it in items:
                ann = parse_item(it, section)
                if ann is not None and ann.url not in seen_urls:
                    seen_urls.add(ann.url)
                    results.append(ann)

            if len(items) < _ZHIYUAN_PAGE_SIZE:
                break  # 已是最后一页

        logger.debug("zhiyuan [%s] 共抓取 %d 条", section, len(results))
        return results

    @staticmethod
    def _zhiyuan_title(item: dict[str, Any]) -> str:
        return str(item.get("cn_title") or item.get("en_title") or "").strip()

    @staticmethod
    def _zhiyuan_date(raw: Any) -> str:
        """'2026-09-10 15:14:31' -> '2026-09-10'"""
        text = str(raw or "").strip()
        return text[:10] if len(text) >= 10 else text

    @classmethod
    def _zhiyuan_announcement_item(cls, item: dict[str, Any], section: str) -> Announcement | None:
        title = cls._zhiyuan_title(item)
        item_id = item.get("id")
        if not title or item_id is None:
            return None
        return Announcement(
            title=title,
            url=urljoin(_ZHIYUAN_VIEW_BASE, f"announcement_view.php?id={item_id}"),
            date=cls._zhiyuan_date(item.get("publish_time")),
            section=section,
        )

    @classmethod
    def _zhiyuan_event_item(cls, item: dict[str, Any], section: str) -> Announcement | None:
        title = cls._zhiyuan_title(item)
        event_id = item.get("announcement_id") or item.get("id")
        if not title or event_id is None:
            return None
        if item.get("announce_category_id") == _ZHIYUAN_INS_CATEGORY:
            url = f"{_ZHIYUAN_INS_BASE}{event_id}"
        else:
            url = urljoin(_ZHIYUAN_VIEW_BASE, f"event_view.php?id={event_id}")
        return Announcement(
            title=title,
            url=url,
            date=cls._zhiyuan_date(item.get("date")),
            section=section,
        )

    # ------------------------------------------------------------------
    # 布局 A — xwtg.htm 板块式
    # ------------------------------------------------------------------

    def _parse_xwtg(
        self, soup: BeautifulSoup, sections: list[Tag], page_url: str
    ) -> list[Announcement]:
        results: list[Announcement] = []
        for section_div in sections:
            section_name = self._extract_section_name(section_div)
            items: list[Tag] = section_div.select("div.Newslist1 ul li")
            for item in items:
                a = self._parse_xwtg_item(item, section_name, page_url)
                if a is not None:
                    results.append(a)
        return results

    @staticmethod
    def _extract_section_name(section_div: Tag) -> str:
        tag = section_div.select_one("div.nytit2 h2")
        return tag.get_text(strip=True) if tag else "未知板块"

    @classmethod
    def _parse_xwtg_item(cls, item: Tag, section: str, base_url: str) -> Announcement | None:
        link_tag = item.select_one("a")
        if link_tag is None:
            return None

        url = cls._normalize_href(str(link_tag.get("href", "")), base_url)
        if url is None:
            return None

        title = link_tag.get_text(strip=True)
        if not title:
            return None

        date_span = item.select_one("span")
        date = date_span.get_text(strip=True) if date_span else ""

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
            a = self._parse_mxxsdtz_item(item, section_name, page_url)
            if a is not None:
                results.append(a)
        return results

    @classmethod
    def _parse_mxxsdtz_item(cls, item: Tag, section: str, base_url: str) -> Announcement | None:
        wz = item.select_one("div.wz")
        if wz is None:
            return None
        link_tag = wz.select_one("a")
        if link_tag is None:
            return None

        url = cls._normalize_href(str(link_tag.get("href", "")), base_url)
        if url is None:
            return None

        title = link_tag.get_text(strip=True)
        if not title:
            return None

        date = cls._extract_mxxsdtz_date(item)
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
        match = re.match(r"(\d{4})\.(\d{1,2})", ym)
        if match:
            month = match.group(2).zfill(2)
            return f"{match.group(1)}-{month}-{day}"
        return f"{ym}-{day}"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_href(href: str, base_url: str) -> str | None:
        """
        校验并补全链接。

        Returns:
            绝对 URL；空链接、锚点、javascript:/mailto:/tel: 等无效链接返回 None
        """
        href = (href or "").strip()
        if not href or href.startswith(_INVALID_HREF_PREFIXES):
            return None
        if href.startswith("http"):
            return href
        return urljoin(base_url, href)

    def _resolve_url(self, href: str, base_url: str | None = None) -> str:
        """将相对链接解析为绝对链接（无效链接返回空串）"""
        return self._normalize_href(href, base_url or self._config.base_url) or ""
