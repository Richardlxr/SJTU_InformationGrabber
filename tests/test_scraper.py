"""
单元测试 - scraper（本地 HTML fixture + 假 Session，不发真实请求）

覆盖布局 A（xwtg 板块式）、布局 B（mxxsdtz 列表式）、布局 C（cs.sjtu AJAX 分页式）。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import requests

from web_bugger.config import ScraperConfig
from web_bugger.scraper import FetchResult, Scraper

# 布局 A —— xwtg.htm 板块式
SAMPLE_XWTG_HTML = """
<html>
<body>
<div class="w50l">
  <div class="nytit2"><h2>新闻中心</h2></div>
  <div class="Newslist1">
    <ul>
      <li><span>2026-03-02</span><a href="info/1025/1001.htm">公告标题一</a></li>
      <li><span>2026-03-01</span><a href="https://external.com/post">外部链接公告</a></li>
      <li><span>2026-03-01</span><a href="#">空锚点</a></li>
      <li><span>2026-03-01</span><a href="javascript:void(0)">脚本链接</a></li>
      <li><span>2026-03-01</span><a>没有 href</a></li>
    </ul>
  </div>
</div>
<div class="w50r">
  <div class="nytit2"><h2>质控办</h2></div>
  <div class="Newslist1">
    <ul>
      <li><span>2026-02-28</span><a href="info/1258/2001.htm">质控办公告</a></li>
    </ul>
  </div>
</div>
</body>
</html>
"""

# 布局 B —— mxxsdtz.htm 列表式
SAMPLE_MXXSDTZ_HTML = """
<html>
<body>
<div class="nytit1">面向学生的通知</div>
<div class="Newslist">
  <ul>
    <li class="clearfix">
      <div class="sj"><h2>15</h2><p>2026.03</p></div>
      <div class="wz">
        <a href="../info/1222/12345.htm"><h2>关于 2026 年春季选课的通知</h2></a>
        <p>摘要内容</p>
      </div>
    </li>
    <li class="clearfix">
      <div class="sj"><h2>02</h2><p>2026.03</p></div>
      <div class="wz">
        <a href="https://example.com/external"><h2>外部通知链接</h2></a>
      </div>
    </li>
  </ul>
</div>
</body>
</html>
"""

# 布局 C —— cs.sjtu 页面外壳（含 article_list 与 cat_code）
SAMPLE_CS_SHELL = """
<html><body>
<div id="article_list"></div>
<script>var opts = {cat_code:'xsgz-tzgg-zyfz'};</script>
</body></html>
"""


# 布局 D —— zhiyuan.sjtu.edu.cn 致远学院（AJAX 空容器 + JSON 接口）
SAMPLE_ZHIYUAN_EVENTS_SHELL = """
<html><body>
<div class="events-list"></div>
<div class="pages" id="pages"></div>
</body></html>
"""

SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL = """
<html><body>
<div class="announcement-list"></div>
<div class="pages" id="pages"></div>
</body></html>
"""

ZHIYUAN_ANNOUNCEMENTS_URL = (
    "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/announcement_list.php"
)
ZHIYUAN_EVENTS_URL = "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/events_list.php"


def _zhiyuan_ann(
    ann_id: int,
    title: str,
    publish_time: str = "2026-09-10 15:14:31",
    category: int = 6,
) -> dict[str, Any]:
    return {
        "id": ann_id,
        "cn_title": title,
        "publish_time": publish_time,
        "announce_category_id": category,
    }


def _zhiyuan_event(
    event_id: int,
    title: str,
    date: str = "2026-06-10 16:00:00",
    category: int = 3,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "announcement_id": event_id,
        "cn_title": title,
        "date": date,
        "announce_category_id": category,
    }


def _cs_item(href: str, title: str, day: str = "09", ym: str = "2026-05") -> str:
    return (
        '<li><a href="' + href + '">'
        f'<div class="time"><p>{day}</p><span>{ym}</span></div>'
        '<div class="line"></div>'
        f'<div class="tit line-2">{title}</div>'
        '<div class="icon"><img src="/img/jt1.png" alt=""></div>'
        "</a></li>"
    )


class FakeJsonResponse:
    """伪造 AJAX 响应"""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.encoding = "utf-8"
        self.url = "https://cs.sjtu.edu.cn/active/ajax_type_list.html"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> Any:
        return self._payload


class FakeSession:
    """按 page 参数返回预设内容的假 Session"""

    def __init__(self, pages: dict[int, list[str]], count: int | None = None) -> None:
        self.pages = pages
        self.count = count if count is not None else sum(len(v) for v in pages.values())
        self.posted_pages: list[Any] = []

    def post(self, url: str, **kwargs: Any) -> FakeJsonResponse:
        page = kwargs["data"]["page"]
        self.posted_pages.append(page)
        items = self.pages.get(page)
        if items is None:
            return FakeJsonResponse({"content": "", "page": page, "count": self.count})
        return FakeJsonResponse({"content": "".join(items), "page": page, "count": self.count})

    def get(self, url: str, **kwargs: Any) -> Any:
        raise requests.ConnectionError("测试环境不联网")

    def close(self) -> None:
        pass


class FakeJsonGetSession:
    """zhiyuan 接口走 GET + params，按 page 返回预设 JSON"""

    def __init__(self, pages: dict[int, dict[str, Any]], list_key: str) -> None:
        self.pages = pages
        self.list_key = list_key
        self.get_calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeJsonResponse:
        params = kwargs.get("params") or {}
        page = int(params["page"])
        self.get_calls.append({"url": url, "params": params, "headers": kwargs.get("headers")})
        payload = self.pages.get(page)
        if payload is None:
            return FakeJsonResponse(
                {"status": 200, self.list_key: [], "total": 0, "count": 0, "pages": 0}
            )
        return FakeJsonResponse(payload)

    def close(self) -> None:
        pass


def _scraper(**kwargs: Any) -> Scraper:
    config = ScraperConfig(
        target_urls=kwargs.pop("target_urls", ["https://jwc.sjtu.edu.cn/xwtg.htm"]),
        **kwargs,
    )
    return Scraper(config)


class TestScraperXwtg:
    """布局 A: xwtg.htm 板块式"""

    def test_parse_sample_html(self) -> None:
        scraper = _scraper(base_url="https://jwc.sjtu.edu.cn/")
        results = scraper._parse(SAMPLE_XWTG_HTML, "https://jwc.sjtu.edu.cn/xwtg.htm")

        # 无效 href（#、javascript:、缺失）被跳过
        assert len(results) == 3

        assert results[0].title == "公告标题一"
        assert results[0].url == "https://jwc.sjtu.edu.cn/info/1025/1001.htm"
        assert results[0].section == "新闻中心"
        assert results[0].date == "2026-03-02"

        assert results[1].url == "https://external.com/post"
        assert results[2].section == "质控办"

    def test_relative_url_uses_page_url_not_global_base(self) -> None:
        """页面来自非 jwc 域名时，相对链接必须按该页面解析"""
        scraper = _scraper(base_url="https://jwc.sjtu.edu.cn/")
        results = scraper._parse(SAMPLE_XWTG_HTML, "https://cs.sjtu.edu.cn/xsgz-tzgg-xssw.html")
        assert results[0].url == "https://cs.sjtu.edu.cn/info/1025/1001.htm"


class TestScraperMxxsdtz:
    """布局 B: mxxsdtz.htm 列表式"""

    def test_parse_sample_html(self) -> None:
        scraper = _scraper(base_url="https://jwc.sjtu.edu.cn/")
        results = scraper._parse(SAMPLE_MXXSDTZ_HTML, "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm")

        assert len(results) == 2
        assert results[0].title == "关于 2026 年春季选课的通知"
        assert results[0].url == "https://jwc.sjtu.edu.cn/info/1222/12345.htm"
        assert results[0].date == "2026-03-15"
        assert results[0].section == "面向学生的通知"
        assert results[1].url == "https://example.com/external"
        assert results[1].date == "2026-03-02"

    def test_date_extraction(self) -> None:
        from bs4 import BeautifulSoup

        html = '<li class="clearfix"><div class="sj"><h2>5</h2><p>2026.01</p></div></li>'
        tag = BeautifulSoup(html, "html.parser").select_one("li")
        assert tag is not None
        assert Scraper._extract_mxxsdtz_date(tag) == "2026-01-05"

    def test_date_extraction_unpadded_month(self) -> None:
        from bs4 import BeautifulSoup

        html = '<li><div class="sj"><h2>3</h2><p>2026.1</p></div></li>'
        tag = BeautifulSoup(html, "html.parser").select_one("li")
        assert tag is not None
        assert Scraper._extract_mxxsdtz_date(tag) == "2026-01-03"

    def test_date_extraction_missing(self) -> None:
        from bs4 import BeautifulSoup

        tag = BeautifulSoup("<li></li>", "html.parser").select_one("li")
        assert tag is not None
        assert Scraper._extract_mxxsdtz_date(tag) == ""


class TestScraperCsSjtu:
    """布局 C: cs.sjtu.edu.cn AJAX 分页列表"""

    def _install(self, scraper: Scraper, session: Any) -> None:
        setattr(scraper, "_session", session)  # noqa: B010 - 测试替身注入

    def test_parse_single_page(self) -> None:
        scraper = _scraper()
        session = FakeSession(
            {
                1: [
                    _cs_item(
                        "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz/1627.html",
                        "关于2026届本科毕业生登记表填表注意事项的通知",
                    ),
                    _cs_item(
                        "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz/1584.html",
                        "计算机学院2026届本科生上海市优秀毕业生拟推荐人公示",
                        day="30",
                        ym="2026-04",
                    ),
                ]
            }
        )
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html")

        assert len(results) == 2
        assert results[0].section == "职业发展"
        assert results[0].date == "2026-05-09"
        assert results[0].url.endswith("/1627.html")
        assert results[1].date == "2026-04-30"

    def test_pagination_collects_all_pages(self) -> None:
        scraper = _scraper()
        session = FakeSession(
            {
                1: [_cs_item(f"https://cs.sjtu.edu.cn/a/{i}.html", f"标题{i}") for i in range(20)],
                2: [
                    _cs_item(f"https://cs.sjtu.edu.cn/a/{i}.html", f"标题{i}")
                    for i in range(20, 23)
                ],
            },
            count=23,
        )
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")

        assert len(results) == 23
        assert session.posted_pages == [1, 2]

    def test_pagination_respects_max_pages(self) -> None:
        """服务端若始终返回同一页且 count 虚高，必须有硬上限避免无限翻页"""
        scraper = _scraper(max_pages=3)
        same_page = [_cs_item(f"https://cs.sjtu.edu.cn/a/{i}.html", f"标题{i}") for i in range(5)]
        session = FakeSession({p: same_page for p in range(1, 100)}, count=9999)
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")

        assert len(results) == 5, "同一页重复内容应被去重"
        assert session.posted_pages == [1, 2, 3], "必须停在 max_pages"

    def test_relative_href_resolved_against_referer(self) -> None:
        scraper = _scraper()
        session = FakeSession({1: [_cs_item("/xsgz-tzgg-zyfz/1.html", "相对链接通知")]})
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html")
        assert results[0].url == "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz/1.html"

    def test_invalid_hrefs_and_empty_titles_skipped(self) -> None:
        scraper = _scraper()
        session = FakeSession(
            {
                1: [
                    _cs_item("javascript:void(0)", "脚本"),
                    _cs_item("", "空链接"),
                    _cs_item("https://cs.sjtu.edu.cn/ok.html", ""),
                    _cs_item("https://cs.sjtu.edu.cn/ok.html", "有效标题"),
                ]
            }
        )
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")
        assert [r.title for r in results] == ["有效标题"]

    def test_missing_cat_code_returns_empty(self) -> None:
        scraper = _scraper()
        results = scraper._parse(
            '<html><body><div id="article_list"></div></body></html>',
            "https://cs.sjtu.edu.cn/x.html",
        )
        assert results == []

    def test_ajax_failure_is_swallowed(self) -> None:
        scraper = _scraper()

        class Boom:
            def post(self, url: str, **kwargs: Any) -> Any:
                raise requests.ConnectionError("boom")

        self._install(scraper, Boom())
        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")
        assert results == []

    def test_non_dict_json_is_handled(self) -> None:
        scraper = _scraper()

        class ListJsonSession:
            def post(self, url: str, **kwargs: Any) -> FakeJsonResponse:
                return FakeJsonResponse(["not", "a", "dict"])

        self._install(scraper, ListJsonSession())
        results = scraper._parse(SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")
        assert results == []

    def test_unknown_cat_code_falls_back_to_active_tab(self) -> None:
        scraper = _scraper()
        session = FakeSession({1: [_cs_item("https://cs.sjtu.edu.cn/a/1.html", "标题")]})
        self._install(scraper, session)
        shell = (
            '<html><body><div id="article_list"></div>'
            '<script>cat_code: "brand-new-section"</script>'
            '<div class="swiper-slide"><a class="on">新板块</a></div>'
            "</body></html>"
        )
        results = scraper._parse(shell, "https://cs.sjtu.edu.cn/x.html")
        assert results[0].section == "新板块"


class TestFetchWithStatus:
    def test_failed_pages_are_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper(
            target_urls=["https://ok.com/1", "https://bad.com/2"],
            base_url="https://ok.com/",
        )

        def fake_download(url: str) -> str | None:
            return SAMPLE_XWTG_HTML if url.startswith("https://ok.com") else None

        monkeypatch.setattr(scraper, "_download", fake_download)
        result = scraper.fetch_with_status()

        assert isinstance(result, FetchResult)
        assert len(result.items) == 3
        assert result.pages_total == 2
        assert result.failed_urls == ("https://bad.com/2",)
        assert result.ok is False
        assert result.all_failed is False

    def test_all_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper(target_urls=["https://bad.com/1"])
        monkeypatch.setattr(scraper, "_download", lambda url: None)
        result = scraper.fetch_with_status()
        assert result.items == []
        assert result.all_failed is True

    def test_empty_target_urls(self) -> None:
        result = _scraper(target_urls=[]).fetch_with_status()
        assert result.items == []
        assert result.pages_total == 0
        assert result.all_failed is False

    def test_fetch_wrapper_returns_items_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper()
        monkeypatch.setattr(scraper, "_download", lambda url: SAMPLE_XWTG_HTML)
        items = scraper.fetch()
        assert len(items) == 3

    def test_dedup_across_pages_and_stable_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def block(url: str, title: str) -> str:
            return (
                '<div class="w50l"><div class="nytit2"><h2>S</h2></div>'
                '<div class="Newslist1"><ul><li><span>d</span>'
                f'<a href="{url}">{title}</a></li></ul></div></div>'
            )

        shared = block("https://x.com/1.htm", "共同公告")
        second = block("https://x.com/2.htm", "独有公告")
        scraper = _scraper(target_urls=["https://a.com/1", "https://b.com/2"])
        html = {"https://a.com/1": shared, "https://b.com/2": shared + second}
        monkeypatch.setattr(scraper, "_download", lambda url: html[url])

        result = scraper.fetch_with_status()
        assert [i.url for i in result.items] == [
            "https://x.com/1.htm",
            "https://x.com/2.htm",
        ]

    def test_unexpected_parse_error_marks_page_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scraper = _scraper(target_urls=["https://a.com/1"])
        monkeypatch.setattr(scraper, "_download", lambda url: "<html></html>")

        def boom(html: str, page_url: str) -> list[Any]:
            raise RuntimeError("解析炸了")

        monkeypatch.setattr(scraper, "_parse", boom)
        result = scraper.fetch_with_status()
        assert result.failed_urls == ("https://a.com/1",)


class TestDecode:
    class _Resp:
        def __init__(
            self,
            encoding: str | None,
            text: str,
            detected: str | None,
            content: bytes | None = None,
        ) -> None:
            self.encoding = encoding
            self.text = text
            self.apparent_encoding = detected
            self.content = content if content is not None else text.encode("utf-8")
            self.url = "https://x.com/"

    def test_uses_sniffed_encoding_when_header_missing(self) -> None:
        # 真实字节是 UTF-8，但响应头只给出不可信的 ISO-8859-1
        resp = self._Resp(
            "ISO-8859-1",
            "中文公告标题".encode().decode("latin-1"),
            "utf-8",
            content="中文公告标题".encode(),
        )
        assert Scraper._decode(cast(Any, resp)) == "中文公告标题"

    def test_keeps_declared_encoding_when_trustworthy(self) -> None:
        resp = self._Resp("utf-8", "中文公告标题", "utf-8")
        assert Scraper._decode(cast(Any, resp)) == "中文公告标题"

    def test_falls_back_to_text_without_detection(self) -> None:
        resp = self._Resp(None, "中文", None)
        assert Scraper._decode(cast(Any, resp)) == "中文"


class TestScraperZhiyuan:
    """布局 D: zhiyuan.sjtu.edu.cn 致远学院 AJAX 列表"""

    def _install(self, scraper: Scraper, session: Any) -> None:
        setattr(scraper, "_session", session)  # noqa: B010 - 测试替身注入

    def test_announcements_parsed_and_nulls_skipped(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {
                1: {
                    "status": 200,
                    "total": 2,
                    "pages": 1,
                    "announcements": [
                        None,
                        _zhiyuan_ann(4810, "致远学院2026-2027学年秋季学期通识课程介绍"),
                    ],
                }
            },
            list_key="announcements",
        )
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)

        assert len(results) == 1, "null 元素应被跳过"
        assert results[0].section == "致远通知公告"
        assert results[0].date == "2026-09-10"
        assert results[0].url == (
            "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/announcement_view.php?id=4810"
        )

    def test_announcement_request_params(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {1: {"status": 200, "total": 1, "announcements": [_zhiyuan_ann(1, "标题")]}},
            list_key="announcements",
        )
        self._install(scraper, session)
        scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)

        call = session.get_calls[0]
        assert call["url"] == (
            "https://zhiyuan.sjtu.edu.cn/api/get_announcements_by_category"
        )
        assert call["params"] == {"num": 50, "category": -1, "page": 1}
        assert call["headers"]["X-Requested-With"] == "XMLHttpRequest"

    def test_events_parsed_with_active_param(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {
                1: {
                    "status": 200,
                    "count": 1,
                    "pages": 1,
                    "events": [_zhiyuan_event(4817, "ZY-INS沙龙 No.314|海洋机器人智能与仿生")],
                }
            },
            list_key="events",
        )
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_ZHIYUAN_EVENTS_SHELL, ZHIYUAN_EVENTS_URL)

        assert len(results) == 1
        assert results[0].section == "致远讲座活动"
        assert results[0].date == "2026-06-10", "活动用 date（活动时间）"
        assert results[0].url == (
            "https://zhiyuan.sjtu.edu.cn/html/zhiyuan/event_view.php?id=4817"
        )
        call = session.get_calls[0]
        assert call["url"] == "https://zhiyuan.sjtu.edu.cn/api/get_event_by_category"
        assert call["params"]["active"] == 0

    def test_event_in_ins_category_uses_external_site(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {
                1: {
                    "status": 200,
                    "count": 1,
                    "events": [_zhiyuan_event(4817, "INS 研讨会", category=523)],
                }
            },
            list_key="events",
        )
        self._install(scraper, session)
        results = scraper._parse(SAMPLE_ZHIYUAN_EVENTS_SHELL, ZHIYUAN_EVENTS_URL)
        assert results[0].url == "https://ins.sjtu.edu.cn/seminars/4817"

    def test_items_without_title_or_id_skipped(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {
                1: {
                    "status": 200,
                    "total": 4,
                    "announcements": [
                        _zhiyuan_ann(1, ""),
                        {"id": None, "cn_title": "没有 id"},
                        {"cn_title": "没有 id 字段"},
                        _zhiyuan_ann(2, "有效标题"),
                    ],
                }
            },
            list_key="announcements",
        )
        self._install(scraper, session)
        results = scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)
        assert [r.title for r in results] == ["有效标题"]

    def test_english_title_fallback(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {
                1: {
                    "status": 200,
                    "announcements": [
                        {
                            "id": 7,
                            "cn_title": "",
                            "en_title": "English Only Title",
                            "publish_time": "2026-01-02 03:04:05",
                        }
                    ],
                }
            },
            list_key="announcements",
        )
        self._install(scraper, session)
        results = scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)
        assert results[0].title == "English Only Title"

    def test_stops_after_recent_pages(self) -> None:
        """历史很深，必须只取最近 _ZHIYUAN_RECENT_PAGES 页"""
        scraper = _scraper()
        full_page = {
            "status": 200,
            "total": 1141,
            "pages": 23,
            "announcements": [_zhiyuan_ann(i, f"通知{i}") for i in range(50)],
        }
        session = FakeJsonGetSession({p: full_page for p in range(1, 30)}, list_key="announcements")
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)

        assert len(session.get_calls) == 2, "应只请求最近 2 页"
        assert [c["params"]["page"] for c in session.get_calls] == [1, 2]
        # 两页内容相同 -> 按 URL 去重
        assert len(results) == 50

    def test_stops_early_when_page_not_full(self) -> None:
        scraper = _scraper()
        page1 = {
            "status": 200,
            "total": 3,
            "announcements": [_zhiyuan_ann(i, f"通知{i}") for i in range(3)],
        }
        page2 = {
            "status": 200,
            "total": 3,
            "announcements": [_zhiyuan_ann(99, "不该被请求")],
        }
        session = FakeJsonGetSession({1: page1, 2: page2}, list_key="announcements")
        self._install(scraper, session)

        results = scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)

        assert len(session.get_calls) == 1, "未满一页说明已到末页，无需再请求"
        assert len(results) == 3

    def test_duplicate_urls_deduped(self) -> None:
        scraper = _scraper()
        item = _zhiyuan_ann(4810, "重复通知")
        session = FakeJsonGetSession(
            {
                1: {"status": 200, "total": 2, "announcements": [item, dict(item)]},
            },
            list_key="announcements",
        )
        self._install(scraper, session)
        results = scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)
        assert len(results) == 1

    def test_status_not_200_returns_empty(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {1: {"status": 500, "message": "boom"}}, list_key="announcements"
        )
        self._install(scraper, session)
        assert scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL) == []

    def test_non_list_payload_returns_empty(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession(
            {1: {"status": 200, "announcements": "not-a-list"}},
            list_key="announcements",
        )
        self._install(scraper, session)
        assert scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL) == []

    def test_empty_first_page_returns_empty(self) -> None:
        scraper = _scraper()
        session = FakeJsonGetSession({}, list_key="events")
        self._install(scraper, session)
        assert scraper._parse(SAMPLE_ZHIYUAN_EVENTS_SHELL, ZHIYUAN_EVENTS_URL) == []

    def test_request_failure_is_swallowed(self) -> None:
        scraper = _scraper()

        class Boom:
            def get(self, url: str, **kwargs: Any) -> Any:
                raise requests.ConnectionError("boom")

        self._install(scraper, Boom())
        assert scraper._parse(SAMPLE_ZHIYUAN_EVENTS_SHELL, ZHIYUAN_EVENTS_URL) == []

    def test_max_pages_config_lowers_cap(self) -> None:
        scraper = _scraper(max_pages=1)
        full_page = {
            "status": 200,
            "total": 999,
            "announcements": [_zhiyuan_ann(i, f"通知{i}") for i in range(50)],
        }
        session = FakeJsonGetSession({1: full_page, 2: full_page}, list_key="announcements")
        self._install(scraper, session)
        scraper._parse(SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL)
        assert len(session.get_calls) == 1

    def test_dispatch_routes_by_container(self) -> None:
        """_parse 应根据容器 class 选择对应接口"""
        scraper = _scraper()
        ann_session = FakeJsonGetSession(
            {1: {"status": 200, "total": 1, "announcements": [_zhiyuan_ann(1, "通知")]}},
            list_key="announcements",
        )
        self._install(scraper, ann_session)
        parsed = scraper._parse(
            SAMPLE_ZHIYUAN_ANNOUNCEMENTS_SHELL, ZHIYUAN_ANNOUNCEMENTS_URL
        )
        assert len(parsed) == 1
        assert "get_announcements" in ann_session.get_calls[0]["url"]

        ev_session = FakeJsonGetSession(
            {1: {"status": 200, "count": 1, "events": [_zhiyuan_event(2, "活动")]}},
            list_key="events",
        )
        self._install(scraper, ev_session)
        assert len(scraper._parse(SAMPLE_ZHIYUAN_EVENTS_SHELL, ZHIYUAN_EVENTS_URL)) == 1
        assert "get_event" in ev_session.get_calls[0]["url"]

    def test_config_default_urls_include_zhiyuan(self) -> None:
        from web_bugger.config import ScraperConfig

        urls = ScraperConfig().target_urls
        assert ZHIYUAN_EVENTS_URL in urls
        assert ZHIYUAN_ANNOUNCEMENTS_URL in urls
        assert "https://jwc.sjtu.edu.cn/xwtg.htm" not in urls, "新闻通告应已移除"
        assert "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm" in urls


class TestHelpers:
    def test_parse_empty_html(self) -> None:
        assert _scraper()._parse("<html></html>", "https://example.com") == []

    def test_resolve_url_relative(self) -> None:
        scraper = _scraper(base_url="https://jwc.sjtu.edu.cn/")
        assert (
            scraper._resolve_url("info/1025/1001.htm")
            == "https://jwc.sjtu.edu.cn/info/1025/1001.htm"
        )

    def test_resolve_url_absolute(self) -> None:
        assert _scraper()._resolve_url("https://external.com/x") == "https://external.com/x"

    @pytest.mark.parametrize(
        "href",
        ["", "   ", "#", "#top", "javascript:void(0)", "mailto:a@b.c", "tel:123"],
    )
    def test_normalize_href_rejects_invalid(self, href: str) -> None:
        assert Scraper._normalize_href(href, "https://x.com/") is None

    def test_normalize_href_accepts_relative_and_absolute(self) -> None:
        assert (
            Scraper._normalize_href("a/b.htm", "https://x.com/dir/") == "https://x.com/dir/a/b.htm"
        )
        assert Scraper._normalize_href("https://y.com/z", "https://x.com/") == "https://y.com/z"

    def test_context_manager_closes_session(self) -> None:
        with _scraper() as scraper:
            assert scraper._session is not None
