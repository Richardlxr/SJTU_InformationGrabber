"""
爬虫模块 - 从上海交通大学教务处/计算机学院/致远学院网站抓取公告（支持多个页面、多种布局）

支持布局:
  A — jwc.sjtu.edu.cn 教务处板块式
  B — jwc.sjtu.edu.cn 面向学生通知列表式
  C — cs.sjtu.edu.cn  计算机学院学生工作 AJAX 分页列表式
  D — zhiyuan.sjtu.edu.cn 致远学院通知动态 / 学术活动（服务端渲染，?page=N 翻页）

无法识别的页面结构按抓取失败处理，以便触发告警，而不是静默返回空列表。
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from types import TracebackType
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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

# 布局 D: zhiyuan.sjtu.edu.cn 致远学院（2026-09 改版后为服务端渲染列表，每页 12 条）
# 两个列表历史都很深（通知 1100+ 条 / 活动 400+ 条），新条目排在最前；但活动列表偶尔
# 会把补录的旧活动插到前面，所以多取几页，既覆盖新条目，又不必每轮拉全量历史。
_ZHIYUAN_RECENT_PAGES = 3
_ZHIYUAN_ANNOUNCEMENT_SECTION = "通知动态"
_ZHIYUAN_EVENT_SECTION = "学术活动"
# laypage.render({ ... }) 的选项部分（截到第一个花括号为止，jump 回调的函数体不在内）
_LAYPAGE_OPTIONS_RE = re.compile(r"laypage\.render\(\s*\{([^{}]*)")
# 活动时间，如 "2026-09-30 12:00"（列表卡片）或 "2026-09-30  12:00-13:30"（详情页）
_ZHIYUAN_EVENT_TIME_RE = re.compile(
    r"(\d{4}-\d{1,2}-\d{1,2})(?:\s+(\d{1,2}:\d{2})(?:\s*-\s*(\d{1,2}:\d{2}))?)?"
)

# 各布局对应的来源站点（邮件按来源分组）
_SOURCE_JWC = "教务处"
_SOURCE_CS = "计算机学院"
_SOURCE_ZHIYUAN = "致远学院"

# 发送前补全详情：最多抓取的详情页数（首次加入新板块时可能一次几十条）与单页超时
_DETAIL_FETCH_LIMIT = 20
_DETAIL_TIMEOUT = 10
_SUMMARY_MAX_CHARS = 120
# 详情页正文容器：致远学院 / 计算机学院 / 教务处
_DETAIL_BODY_SELECTOR = ".article-container .mce-content-body, div.txt, div.v_news_content"
# 活动详情正文开头的「主讲嘉宾：xxx」（其后紧跟下一个字段标签，或正文结束）
_EVENT_SPEAKER_RE = re.compile(
    r"\s*(?:主讲嘉宾|主讲人|报告人)\s*[:：]?\s*(.{2,60}?)\s*"
    r"(?=讲座时间|报告时间|活动时间|讲座地点|报告地点|活动地点|主讲内容|报告摘要|内容简介|嘉宾介绍|$)"
)
# 活动摘要从这些标签之后开始
_EVENT_ABSTRACT_RE = re.compile(r"(?:报告摘要|主讲内容|内容简介|活动简介|讲座简介)\s*[:：]?\s*")
_LEADING_SPEAKER_LABEL_RE = re.compile(r"^(?:主讲嘉宾|主讲人|报告人)\s*[:：]?\s*")
# 两个中文字符（含全角标点）之间的空白：网页排版产生的，不是内容本身
_CJK_GAP_RE = re.compile(
    r"(?<=[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef])\s+(?=[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef])"
)


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

    def enrich(self, announcements: list[Announcement]) -> list[Announcement]:
        """
        发送前为新公告补全详情：正文摘要，以及活动的主讲人和精确时间段。

        只处理还没有摘要的条目（最多 _DETAIL_FETCH_LIMIT 条，并发抓取详情页）；
        任何失败都只是少了这些信息，对应条目原样返回，不影响通知。
        """
        todo = [a for a in announcements if not a.summary][:_DETAIL_FETCH_LIMIT]
        if not todo:
            return announcements
        workers = max(1, min(self._config.max_workers, len(todo)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            enriched = dict(
                zip((a.url for a in todo), pool.map(self._enrich_one, todo), strict=True)
            )
        return [enriched.get(a.url, a) for a in announcements]

    def _enrich_one(self, item: Announcement) -> Announcement:
        try:
            resp = self._session.get(
                item.url, timeout=min(self._config.request_timeout, _DETAIL_TIMEOUT)
            )
            resp.raise_for_status()
            return self._parse_detail(item, self._decode(resp))
        except requests.RequestException as e:
            logger.warning("获取详情页失败，邮件中将不含摘要 (%s): %s", item.url, e)
        except Exception:
            logger.exception("解析详情页出错 (%s)", item.url)
        return item

    @classmethod
    def _parse_detail(cls, item: Announcement, html: str) -> Announcement:
        """从详情页提取摘要；活动另取主讲人和精确时间段"""
        soup = BeautifulSoup(html, "html.parser")
        body = soup.select_one(_DETAIL_BODY_SELECTOR)
        text = _element_text(body) if body is not None else cls._meta_description(soup)
        changes: dict[str, str] = {}

        if item.is_event:
            speaker = _EVENT_SPEAKER_RE.match(text)
            if speaker is not None and not item.speaker:
                changes["speaker"] = speaker.group(1).rstrip("，,；; ")
            abstract = _EVENT_ABSTRACT_RE.search(text)
            if abstract is not None:
                text = text[abstract.end() :]
            elif speaker is not None:
                text = text[speaker.end() :]
            else:
                text = _LEADING_SPEAKER_LABEL_RE.sub("", text)
            # 详情页有完整时间段（列表卡片只有开始时间）
            time_tag = soup.select_one(".article-otherBase span")
            if time_tag is not None:
                detailed = _tidy_event_time(time_tag.get_text(" ", strip=True))
                if detailed and detailed[:10] == item.date[:10]:
                    changes["date"] = detailed

        summary = _summarize(text, item.title)
        if summary:
            changes["summary"] = summary
        return replace(item, **changes) if changes else item

    @staticmethod
    def _meta_description(soup: BeautifulSoup) -> str:
        """<meta name="description">；与页面标题重复（站点通用描述）或太短时视为无效"""
        tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        content = " ".join(str(tag.get("content", "")).split()) if isinstance(tag, Tag) else ""
        page_title = soup.title.get_text(strip=True) if soup.title else ""
        if len(content) < 20 or content in page_title:
            return ""
        return content

    def _fetch_one(self, url: str) -> list[Announcement] | None:
        """下载并解析单个页面；下载失败或页面结构无法识别时返回 None"""
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

    def _parse(self, html: str, page_url: str) -> list[Announcement] | None:
        """
        根据页面结构自动选择解析策略。

        Returns:
            公告列表；页面结构无法识别时返回 None（按抓取失败处理，以便触发告警）
        """
        soup = BeautifulSoup(html, "html.parser")

        # 布局 C: cs.sjtu.edu.cn 计算机学院 AJAX 动态加载列表
        if soup.select_one("div#article_list") is not None:
            return self._parse_cs_sjtu(html, soup, page_url)

        # 布局 D: zhiyuan.sjtu.edu.cn 致远学院服务端渲染列表
        if soup.select_one("div.announcement-list") is not None:
            return self._parse_zhiyuan(html, soup, page_url, self._parse_zhiyuan_announcements)
        if soup.select_one("div.event-list") is not None:
            return self._parse_zhiyuan(html, soup, page_url, self._parse_zhiyuan_events)

        # 布局 A: xwtg.htm 板块式（多个 div.w50l / div.w50r，每个含 Newslist1）
        sections = soup.select("div.w50l, div.w50r")
        if sections:
            return self._parse_xwtg(soup, sections, page_url)

        # 布局 B: mxxsdtz.htm 列表式（单个 div.Newslist > ul > li.clearfix）
        newslist = soup.select_one("div.Newslist")
        if newslist:
            return self._parse_mxxsdtz(soup, newslist, page_url)

        logger.error("未识别的页面布局（页面可能已改版）: %s", page_url)
        return None

    # ------------------------------------------------------------------
    # 布局 C — cs.sjtu.edu.cn 计算机学院 AJAX 列表式
    # ------------------------------------------------------------------

    def _parse_cs_sjtu(
        self, html: str, soup: BeautifulSoup, page_url: str
    ) -> list[Announcement] | None:
        """
        页面使用 AJAX POST 接口动态加载通知列表：
          POST https://cs.sjtu.edu.cn/active/ajax_type_list.html
          Params: page, cat_code, type, search, extend_id, template
        支持自动翻页直至取得全部公告。

        Returns:
            公告列表；页面里找不到 cat_code（页面结构变了）或接口失败时返回 None
        """
        m = re.search(r"cat_code\s*:\s*['\"]([^'\"]+)['\"]", html)
        if m is None:
            logger.error("cs.sjtu 页面未找到 cat_code，页面结构可能已变化: %s", page_url)
            return None
        cat_code = m.group(1)

        section_name = self._CS_SJTU_CAT_SECTION.get(cat_code)
        if section_name is None:
            active_a = soup.select_one("div.swiper-slide a.on")
            section_name = active_a.get_text(strip=True) if active_a else cat_code

        logger.info("cs.sjtu 板块 [%s] cat_code=%s", section_name, cat_code)
        return self._fetch_all_cs_sjtu_pages(cat_code, section_name, page_url)

    def _fetch_all_cs_sjtu_pages(
        self, cat_code: str, section: str, referer: str
    ) -> list[Announcement] | None:
        """
        分页 POST AJAX 接口，取出所有条目。

        第 1 页请求失败、返回结构异常，或接口声称有内容（count > 0）却解析不出任何条目时，
        返回 None 按抓取失败处理；后续页面失败只记警告，保留已抓到的结果。

        必须设置页数上限：若服务端忽略 page 参数（或返回错误的 count），
        `len(results) >= total` 永远不成立，会无限翻页把守护进程卡死。
        """
        results: list[Announcement] = []
        seen_urls: set[str] = set()
        max_pages = max(1, self._config.max_pages)
        page = 1

        while page <= max_pages:
            data = self._post_cs_sjtu_page(cat_code, page, referer)
            if data is None:
                if page == 1:
                    return None
                logger.warning(
                    "cs.sjtu [%s] 第 %d 页抓取失败，本轮只使用前 %d 页", section, page, page - 1
                )
                break

            try:
                total = int(data.get("count", 0))
            except (TypeError, ValueError):
                total = 0
            content_html = data.get("content", "")
            items = (
                BeautifulSoup(content_html, "html.parser").find_all("li")
                if isinstance(content_html, str) and content_html
                else []
            )

            for item in items:
                ann = self._parse_cs_sjtu_item(item, section, referer)
                if ann is not None and ann.url not in seen_urls:
                    seen_urls.add(ann.url)
                    results.append(ann)

            if page == 1 and not results and total > 0:
                logger.error(
                    "cs.sjtu [%s] 接口声称有 %d 条却解析不出任何条目，接口或页面结构可能已变化",
                    section,
                    total,
                )
                return None
            if not items or len(results) >= total:
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

    def _post_cs_sjtu_page(self, cat_code: str, page: int, referer: str) -> dict[str, Any] | None:
        """请求 AJAX 接口的某一页；请求失败或返回结构异常时返回 None"""
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
            return None
        if not isinstance(data, dict):
            logger.error("cs.sjtu AJAX 返回结构异常 (cat=%s, page=%d): %r", cat_code, page, data)
            return None
        return data

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

        return Announcement(title=title, url=url, date=date, section=section, source=_SOURCE_CS)

    # ------------------------------------------------------------------
    # 布局 D — zhiyuan.sjtu.edu.cn 致远学院服务端渲染列表
    # ------------------------------------------------------------------

    def _parse_zhiyuan(
        self,
        html: str,
        soup: BeautifulSoup,
        page_url: str,
        parse_page: Callable[[BeautifulSoup, str], list[Announcement]],
    ) -> list[Announcement] | None:
        """
        目标页面本身就是第 1 页，其后按 ?page=N 翻页（只取最近 _ZHIYUAN_RECENT_PAGES 页）。

        第 1 页声称有内容（分页组件显示总数 > 0）却一条都解析不出时，说明页面结构变了，
        返回 None 按抓取失败处理；后续页面失败只影响「补漏」，保留已抓到的结果。
        """
        results = parse_page(soup, page_url)
        pagination = self._laypage(html)
        if not results:
            if pagination is not None and pagination[0] > 0:
                logger.error(
                    "致远学院列表有 %d 条却解析不出任何条目，页面结构可能已变化: %s",
                    pagination[0],
                    page_url,
                )
                return None
            return []

        # 条目数不超过一页时页面上没有分页组件
        total_pages = math.ceil(pagination[0] / pagination[1]) if pagination else 1
        last_page = min(total_pages, max(1, min(self._config.max_pages, _ZHIYUAN_RECENT_PAGES)))
        seen_urls = {a.url for a in results}

        for page in range(2, last_page + 1):
            page_html = self._download(_with_query_param(page_url, "page", str(page)))
            if page_html is None:
                logger.warning(
                    "致远学院第 %d 页抓取失败，本轮只使用前 %d 页: %s", page, page - 1, page_url
                )
                break
            new = [
                a
                for a in parse_page(BeautifulSoup(page_html, "html.parser"), page_url)
                if a.url not in seen_urls
            ]
            if not new:
                break  # 已无更多条目（或服务端忽略了 page 参数）
            seen_urls.update(a.url for a in new)
            results.extend(new)

        logger.debug("zhiyuan 共抓取 %d 条（%s）", len(results), page_url)
        return results

    @classmethod
    def _parse_zhiyuan_announcements(cls, soup: BeautifulSoup, page_url: str) -> list[Announcement]:
        """
        通知动态（/announcement），HTML 结构:
          <div class="announcement-list">
            <a class="item" href="https://zhiyuan.sjtu.edu.cn/post/3121">
              <div class="ala-calendar">
                <div class="day">30</div><div class="month">2026-09</div>
              </div>
              <div class="item-body"><div class="title">标题</div></div>
              <div class="item-foot"><div class="ala-tag">学生事务</div></div>
            </a>
          </div>
        """
        results: list[Announcement] = []
        for item in soup.select("div.announcement-list a.item"):
            card = cls._zhiyuan_card(item, page_url)
            if card is None:
                continue
            url, title, tag = card
            results.append(
                Announcement(
                    title=title,
                    url=url,
                    date=cls._zhiyuan_calendar_date(item),
                    section=tag or _ZHIYUAN_ANNOUNCEMENT_SECTION,
                    source=_SOURCE_ZHIYUAN,
                )
            )
        return results

    @classmethod
    def _parse_zhiyuan_events(cls, soup: BeautifulSoup, page_url: str) -> list[Announcement]:
        """
        学术活动（/event），HTML 结构:
          <div class="event-list">
            <a class="event-card" href="https://zhiyuan.sjtu.edu.cn/event/2639">
              <div class="ala-calendar">
                <div class="day">30</div><div class="month">2026-09</div>
              </div>
              <div class="ala-tag">ChalkTalk</div>
              <div class="title">标题</div>
              <div class="ala-info">
                <div class="item"><i class="icon-calendar-o"></i> 2026-09-30 12:00</div>
                <div class="item"><i class="icon-location-o"></i> 地点</div>
              </div>
            </a>
          </div>

        日期取活动举办时间（精确到分钟），缺失时退回日历块上的日期；同时取活动地点。
        """
        results: list[Announcement] = []
        for card_tag in soup.select("div.event-list a.event-card"):
            card = cls._zhiyuan_card(card_tag, page_url)
            if card is None:
                continue
            url, title, tag = card
            time, location = cls._zhiyuan_event_info(card_tag)
            results.append(
                Announcement(
                    title=title,
                    url=url,
                    date=time or cls._zhiyuan_calendar_date(card_tag),
                    section=f"{_ZHIYUAN_EVENT_SECTION}·{tag}" if tag else _ZHIYUAN_EVENT_SECTION,
                    source=_SOURCE_ZHIYUAN,
                    location=location,
                )
            )
        return results

    @classmethod
    def _zhiyuan_card(cls, item: Tag, page_url: str) -> tuple[str, str, str] | None:
        """取出（链接, 标题, 分类标签）；缺链接或标题时返回 None"""
        url = cls._normalize_href(str(item.get("href", "")), page_url)
        title_tag = item.select_one(".title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if url is None or not title:
            return None
        tag = item.select_one(".ala-tag")
        return url, title, tag.get_text(strip=True) if tag else ""

    @staticmethod
    def _zhiyuan_calendar_date(item: Tag) -> str:
        """<div class="day">30</div><div class="month">2026-09</div> -> '2026-09-30'"""
        day_tag = item.select_one(".ala-calendar .day")
        month_tag = item.select_one(".ala-calendar .month")
        if day_tag is None or month_tag is None:
            return ""
        day = day_tag.get_text(strip=True)
        match = re.fullmatch(r"(\d{4})-(\d{1,2})", month_tag.get_text(strip=True))
        if match is None or not day.isdigit():
            return ""
        return f"{match.group(1)}-{match.group(2).zfill(2)}-{day.zfill(2)}"

    @staticmethod
    def _zhiyuan_event_info(card: Tag) -> tuple[str, str]:
        """
        活动信息栏：（举办时间, 地点）。时间如 '2026-09-30 12:00'，地点那一栏带定位图标；
        找不到的返回空串。
        """
        time = location = ""
        for info in card.select(".ala-info .item"):
            text = info.get_text(" ", strip=True)
            if info.select_one(".icon-location-o") is not None:
                location = location or text
            elif not time and _ZHIYUAN_EVENT_TIME_RE.search(text):
                time = _tidy_event_time(text)
        return time, location

    @staticmethod
    def _laypage(html: str) -> tuple[int, int] | None:
        """
        读取 layui 分页组件参数：laypage.render({ count: 1143, limit: 12, curr: 1, ... })。

        注意：站点公共脚本里每页都有一段样板 laypage.render({ count: 100, ... })（没有 limit），
        必须跳过它，只认同时带 count 和 limit 的那次调用。

        Returns:
            (总条数, 每页条数)；没有真正的分页组件（条目不足一页）时返回 None
        """
        for options in _LAYPAGE_OPTIONS_RE.findall(html):
            count = re.search(r"\bcount\s*:\s*(\d+)", options)
            limit = re.search(r"\blimit\s*:\s*(\d+)", options)
            if count is not None and limit is not None and int(limit.group(1)) > 0:
                return int(count.group(1)), int(limit.group(1))
        return None

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

        return Announcement(title=title, url=url, date=date, section=section, source=_SOURCE_JWC)

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

        # 列表自带正文开头作为摘要：<div class="wz"><a>...</a><p>摘要</p></div>
        summary_tag = wz.select_one("p")
        summary = _summarize(_element_text(summary_tag), title) if summary_tag else ""
        date = cls._extract_mxxsdtz_date(item)
        return Announcement(
            title=title, url=url, date=date, section=section, source=_SOURCE_JWC, summary=summary
        )

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


def _with_query_param(url: str, key: str, value: str) -> str:
    """设置（或替换）URL 中的某个查询参数，保留其余参数"""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _element_text(element: Tag) -> str:
    """
    元素的纯文本：片段之间用空格分隔、压缩空白，再去掉两个中文字符之间多余的空格
    （「主讲嘉宾： 陈昱」->「主讲嘉宾：陈昱」），英文单词之间的空格保留。
    """
    text = " ".join(element.get_text(" ").split())
    return _CJK_GAP_RE.sub("", text)


def _summarize(text: str, title: str = "") -> str:
    """压缩空白、去掉开头重复的标题，截断到 _SUMMARY_MAX_CHARS 字"""
    text = _strip_prefix_ignoring_spaces(" ".join(text.split()), title)
    text = text.lstrip(" ：:，,。；;、-—")
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[:_SUMMARY_MAX_CHARS].rstrip() + "…"
    return text


def _strip_prefix_ignoring_spaces(text: str, prefix: str) -> str:
    """text 以 prefix 开头（忽略空白差异）时去掉这段前缀"""
    target = "".join(prefix.split())
    if not target:
        return text
    i = j = 0
    while i < len(text) and j < len(target):
        if text[i].isspace():
            i += 1
        elif text[i] == target[j]:
            i += 1
            j += 1
        else:
            return text
    return text[i:] if j == len(target) else text


def _tidy_event_time(raw: str) -> str:
    """
    规范活动时间：'2026-09-30  12:00-13:30' -> '2026-09-30 12:00-13:30'。

    站点用 00:00 表示「未填时间」，起止相同表示只有开始时间，这两种情况都简化掉。
    """
    match = _ZHIYUAN_EVENT_TIME_RE.search(raw)
    if match is None:
        return ""
    day, start, end = match.groups()
    if start is None or (start == "00:00" and end in (None, "00:00")):
        return day
    if end is None or end == start:
        return f"{day} {start}"
    return f"{day} {start}-{end}"
