"""
单元测试 - scraper（本地 HTML fixture + 假 Session，不发真实请求）

覆盖布局 A（xwtg 板块式）、布局 B（mxxsdtz 列表式）、布局 C（cs.sjtu AJAX 分页式）、
布局 D（zhiyuan 服务端渲染列表 + ?page=N 翻页）。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import requests

from web_bugger.config import ScraperConfig
from web_bugger.models import Announcement
from web_bugger.scraper import FetchResult, Scraper, _with_query_param

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


# 布局 D —— zhiyuan.sjtu.edu.cn 致远学院（服务端渲染列表，?page=N 翻页）
ZHIYUAN_ANNOUNCEMENTS_URL = "https://zhiyuan.sjtu.edu.cn/announcement"
ZHIYUAN_EVENTS_URL = "https://zhiyuan.sjtu.edu.cn/event"

# 站点公共脚本里每页都有的样板分页（count: 100 且没有 limit），不能当成真实分页
_ZHIYUAN_BOILERPLATE_PAGER = (
    "<script>layui.use(['laypage'], function () { var laypage = layui.laypage;"
    " laypage.render({ elem: 'pages' , count: 100 , theme: '#00275D' , groups: 3"
    " ,layout: ['prev', 'page', 'next'] }); });</script>"
)


def _zhiyuan_pager(count: int, curr: int = 1) -> str:
    """真实分页组件（条目超过一页时才会输出）"""
    return (
        "<script>$(document).ready(function () { setTimeout(function () {"
        " layui.use(['laypage'], function () { var laypage = layui.laypage;"
        f" laypage.render({{ elem: 'pages' , count: {count} , limit: 12 , curr: {curr} ,"
        " jump: function (obj, first) { if (!first) { location.href = '?page=' + obj.curr; } }"
        " }); }); }, 400); });</script>"
    )


def _zhiyuan_ann(
    post_id: int, title: str, day: str = "30", month: str = "2026-09", tag: str = "学生事务"
) -> str:
    return (
        f'<a class="item" href="https://zhiyuan.sjtu.edu.cn/post/{post_id}">'
        '<div class="item-head"><div class="ala-calendar">'
        f'<div class="day fnt36">{day}</div><div class="month fnt18">{month}</div></div></div>'
        f'<div class="item-body"><div class="title ellipsis--2 fnt24">{title}</div></div>'
        f'<div class="item-foot"><div class="ala-tag is-plain">{tag}</div></div>'
        "</a>"
    )


def _zhiyuan_event(
    event_id: int,
    title: str,
    time: str = "2026-09-30 12:00",
    tag: str = "ChalkTalk",
    day: str = "30",
    month: str = "2026-09",
) -> str:
    return (
        '<div class="layui-col-lg3">'
        f'<a class="event-card no-speaker" href="https://zhiyuan.sjtu.edu.cn/event/{event_id}">'
        '<div class="event-card_head"><div class="ala-calendar is-solid">'
        f'<div class="day fnt32">{day}</div><div class="month fnt16">{month}</div></div>'
        f'<div class="ala-tag is-plain fnt16">{tag}</div></div>'
        f'<div class="event-card_body"><div class="title ellipsis--2 fnt22">{title}</div>'
        '<div class="ala-info">'
        f'<div class="item ellipsis--1"><i class="iconfont icon-calendar-o"></i> {time} </div>'
        '<div class="item ellipsis--1"><i class="iconfont icon-location-o"></i> 110教室 </div>'
        "</div></div></a></div>"
    )


def _zhiyuan_page(container: str, items: list[str], count: int | None = None, curr: int = 1) -> str:
    """container: "announcement-list" / "event-list"；count 为 None 表示不足一页（无真实分页）"""
    pager = _zhiyuan_pager(count, curr) if count is not None else ""
    return (
        f'<html><body><div class="{container}">{"".join(items)}</div>'
        f'<div class="pages" id="pages"></div>{_ZHIYUAN_BOILERPLATE_PAGER}{pager}</body></html>'
    )


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


def _parse_ok(scraper: Scraper, html: str, url: str) -> list[Announcement]:
    """解析并断言页面结构可识别（_parse 返回 None 表示无法识别）"""
    results = scraper._parse(html, url)
    assert results is not None
    return results


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
        results = _parse_ok(scraper, SAMPLE_XWTG_HTML, "https://jwc.sjtu.edu.cn/xwtg.htm")

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
        results = _parse_ok(scraper, SAMPLE_XWTG_HTML, "https://cs.sjtu.edu.cn/xsgz-tzgg-xssw.html")
        assert results[0].url == "https://cs.sjtu.edu.cn/info/1025/1001.htm"


class TestScraperMxxsdtz:
    """布局 B: mxxsdtz.htm 列表式"""

    def test_parse_sample_html(self) -> None:
        scraper = _scraper(base_url="https://jwc.sjtu.edu.cn/")
        results = _parse_ok(
            scraper, SAMPLE_MXXSDTZ_HTML, "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm"
        )

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

        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html")

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

        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")

        assert len(results) == 23
        assert session.posted_pages == [1, 2]

    def test_pagination_respects_max_pages(self) -> None:
        """服务端若始终返回同一页且 count 虚高，必须有硬上限避免无限翻页"""
        scraper = _scraper(max_pages=3)
        same_page = [_cs_item(f"https://cs.sjtu.edu.cn/a/{i}.html", f"标题{i}") for i in range(5)]
        session = FakeSession({p: same_page for p in range(1, 100)}, count=9999)
        self._install(scraper, session)

        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")

        assert len(results) == 5, "同一页重复内容应被去重"
        assert session.posted_pages == [1, 2, 3], "必须停在 max_pages"

    def test_relative_href_resolved_against_referer(self) -> None:
        scraper = _scraper()
        session = FakeSession({1: [_cs_item("/xsgz-tzgg-zyfz/1.html", "相对链接通知")]})
        self._install(scraper, session)

        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/xsgz-tzgg-zyfz.html")
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

        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")
        assert [r.title for r in results] == ["有效标题"]

    def test_missing_cat_code_returns_empty(self) -> None:
        scraper = _scraper()
        results = _parse_ok(
            scraper,
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
        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")
        assert results == []

    def test_non_dict_json_is_handled(self) -> None:
        scraper = _scraper()

        class ListJsonSession:
            def post(self, url: str, **kwargs: Any) -> FakeJsonResponse:
                return FakeJsonResponse(["not", "a", "dict"])

        self._install(scraper, ListJsonSession())
        results = _parse_ok(scraper, SAMPLE_CS_SHELL, "https://cs.sjtu.edu.cn/x.html")
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
        results = _parse_ok(scraper, shell, "https://cs.sjtu.edu.cn/x.html")
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

    def test_unrecognized_layout_marks_page_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper(target_urls=["https://a.com/1"])
        monkeypatch.setattr(scraper, "_download", lambda url: "<html><p>维护中</p></html>")
        result = scraper.fetch_with_status()
        assert result.failed_urls == ("https://a.com/1",)

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
    """布局 D: zhiyuan.sjtu.edu.cn 致远学院服务端渲染列表"""

    def _serve(
        self, monkeypatch: pytest.MonkeyPatch, scraper: Scraper, pages: dict[str, str | None]
    ) -> list[str]:
        """把 _download 换成按 URL 返回预设 HTML（未预设的 URL 视为请求失败）"""
        requested: list[str] = []

        def fake_download(url: str) -> str | None:
            requested.append(url)
            return pages.get(url)

        monkeypatch.setattr(scraper, "_download", fake_download)
        return requested

    def test_announcements_parsed(self) -> None:
        html = _zhiyuan_page(
            "announcement-list",
            [
                _zhiyuan_ann(3121, "“致远阳光领袖奖学金”评选通知"),
                _zhiyuan_ann(997, "班主任招募通知", day="1", month="2026-7", tag="综合事务"),
            ],
        )
        results = _scraper()._parse(html, ZHIYUAN_ANNOUNCEMENTS_URL)

        assert results is not None
        assert [r.url for r in results] == [
            "https://zhiyuan.sjtu.edu.cn/post/3121",
            "https://zhiyuan.sjtu.edu.cn/post/997",
        ]
        assert results[0].title == "“致远阳光领袖奖学金”评选通知"
        assert results[0].date == "2026-09-30"
        assert results[0].section == "致远通知公告·学生事务"
        assert results[1].date == "2026-07-01", "月、日补零"
        assert results[1].section == "致远通知公告·综合事务"

    def test_events_parsed_with_time_and_series(self) -> None:
        html = _zhiyuan_page(
            "event-list",
            [_zhiyuan_event(2629, "ZY-INS沙龙 No.317| 从现象理解深度学习", tag="ZY-INS沙龙")],
        )
        results = _scraper()._parse(html, ZHIYUAN_EVENTS_URL)

        assert results is not None and len(results) == 1
        assert results[0].url == "https://zhiyuan.sjtu.edu.cn/event/2629"
        assert results[0].date == "2026-09-30 12:00", "活动日期用举办时间（精确到分钟）"
        assert results[0].section == "致远讲座活动·ZY-INS沙龙"

    def test_event_time_falls_back_to_calendar(self) -> None:
        html = _zhiyuan_page("event-list", [_zhiyuan_event(1, "活动", time="时间待定")])
        results = _scraper()._parse(html, ZHIYUAN_EVENTS_URL)
        assert results is not None
        assert results[0].date == "2026-09-30"

    def test_missing_tag_and_calendar(self) -> None:
        item = (
            '<a class="item" href="https://zhiyuan.sjtu.edu.cn/post/1">'
            '<div class="title">无标签通知</div></a>'
        )
        results = _scraper()._parse(
            _zhiyuan_page("announcement-list", [item]), ZHIYUAN_ANNOUNCEMENTS_URL
        )
        assert results is not None
        assert results[0].section == "致远通知公告"
        assert results[0].date == ""

    def test_items_without_title_or_href_skipped(self) -> None:
        items = [
            '<a class="item"><div class="title">没有 href</div></a>',
            '<a class="item" href="javascript:;"><div class="title">脚本链接</div></a>',
            '<a class="item" href="/post/2"><div class="day">1</div></a>',
            _zhiyuan_ann(3, "有效标题"),
        ]
        results = _scraper()._parse(
            _zhiyuan_page("announcement-list", items), ZHIYUAN_ANNOUNCEMENTS_URL
        )
        assert results is not None
        assert [r.title for r in results] == ["有效标题"]

    def test_event_card_without_title_skipped(self) -> None:
        cards = [
            '<a class="event-card" href="/event/1"><div class="ala-tag">无标题</div></a>',
            _zhiyuan_event(2, "有效活动"),
        ]
        results = _scraper()._parse(_zhiyuan_page("event-list", cards), ZHIYUAN_EVENTS_URL)
        assert results is not None
        assert [r.title for r in results] == ["有效活动"]

    def test_malformed_calendar_gives_empty_date(self) -> None:
        items = [
            _zhiyuan_ann(1, "月份格式异常", month="2026/09"),
            _zhiyuan_ann(2, "日期不是数字", day="--"),
        ]
        results = _scraper()._parse(
            _zhiyuan_page("announcement-list", items), ZHIYUAN_ANNOUNCEMENTS_URL
        )
        assert results is not None
        assert [r.date for r in results] == ["", ""]

    def test_relative_href_resolved(self) -> None:
        item = '<a class="item" href="/post/42"><div class="title">相对链接</div></a>'
        results = _scraper()._parse(
            _zhiyuan_page("announcement-list", [item]), ZHIYUAN_ANNOUNCEMENTS_URL
        )
        assert results is not None
        assert results[0].url == "https://zhiyuan.sjtu.edu.cn/post/42"

    def test_fetches_only_recent_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """历史很深（1143 条 / 96 页），只取最近 _ZHIYUAN_RECENT_PAGES 页"""
        scraper = _scraper()

        def page(n: int) -> str:
            items = [_zhiyuan_ann(n * 100 + i, f"通知{n}-{i}") for i in range(12)]
            return _zhiyuan_page("announcement-list", items, count=1143, curr=n)

        requested = self._serve(
            monkeypatch,
            scraper,
            {f"{ZHIYUAN_ANNOUNCEMENTS_URL}?page={n}": page(n) for n in range(2, 10)},
        )
        results = scraper._parse(page(1), ZHIYUAN_ANNOUNCEMENTS_URL)

        assert requested == [
            f"{ZHIYUAN_ANNOUNCEMENTS_URL}?page=2",
            f"{ZHIYUAN_ANNOUNCEMENTS_URL}?page=3",
        ]
        assert results is not None and len(results) == 36

    def test_page_count_bounded_by_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper()
        page2 = _zhiyuan_page("event-list", [_zhiyuan_event(99, "第二页")], count=13, curr=2)
        requested = self._serve(monkeypatch, scraper, {f"{ZHIYUAN_EVENTS_URL}?page=2": page2})
        first = [_zhiyuan_event(i, f"活动{i}") for i in range(12)]

        results = scraper._parse(_zhiyuan_page("event-list", first, count=13), ZHIYUAN_EVENTS_URL)

        assert requested == [f"{ZHIYUAN_EVENTS_URL}?page=2"], "13 条只有 2 页，不应请求第 3 页"
        assert results is not None and len(results) == 13

    def test_boilerplate_pager_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回归：样板分页 count: 100 不能被当成真实分页"""
        scraper = _scraper()
        html = _zhiyuan_page("event-list", [_zhiyuan_event(1, "唯一活动")])
        assert Scraper._laypage(html) is None
        requested = self._serve(monkeypatch, scraper, {})

        results = scraper._parse(html, ZHIYUAN_EVENTS_URL)

        assert requested == [], "不足一页时不应翻页"
        assert results is not None and len(results) == 1

    def test_laypage_reads_real_pager(self) -> None:
        html = _zhiyuan_page("announcement-list", [], count=1143)
        assert Scraper._laypage(html) == (1143, 12)

    def test_stops_when_page_has_no_new_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """服务端若忽略 page 参数（每页都一样），不应一直翻下去"""
        scraper = _scraper()
        same = _zhiyuan_page(
            "announcement-list", [_zhiyuan_ann(i, f"通知{i}") for i in range(12)], count=1143
        )
        requested = self._serve(
            monkeypatch,
            scraper,
            {
                f"{ZHIYUAN_ANNOUNCEMENTS_URL}?page=2": same,
                f"{ZHIYUAN_ANNOUNCEMENTS_URL}?page=3": same,
            },
        )
        results = scraper._parse(same, ZHIYUAN_ANNOUNCEMENTS_URL)

        assert requested == [f"{ZHIYUAN_ANNOUNCEMENTS_URL}?page=2"]
        assert results is not None and len(results) == 12

    def test_later_page_failure_keeps_first_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper()
        self._serve(monkeypatch, scraper, {})  # 第 2 页请求失败
        first = _zhiyuan_page(
            "announcement-list", [_zhiyuan_ann(i, f"通知{i}") for i in range(12)], count=1143
        )
        results = scraper._parse(first, ZHIYUAN_ANNOUNCEMENTS_URL)
        assert results is not None and len(results) == 12, "后续页失败不应让整个页面算失败"

    def test_unparseable_items_with_pager_is_failure(self) -> None:
        """有真实分页（声称有内容）却一条都解析不出 -> 页面结构变了，按失败处理"""
        items = ['<a class="item" href="/post/1"><div class="new-title">改版后</div></a>'] * 12
        html = _zhiyuan_page("announcement-list", items, count=1143)
        assert _scraper()._parse(html, ZHIYUAN_ANNOUNCEMENTS_URL) is None

    def test_empty_list_is_not_failure(self) -> None:
        """空分类（容器在、没有条目、没有真实分页）是正常情况"""
        html = _zhiyuan_page("event-list", [])
        assert _scraper()._parse(html, ZHIYUAN_EVENTS_URL) == []

    def test_page_param_keeps_existing_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper()
        url = "https://zhiyuan.sjtu.edu.cn/announcement?category=74"
        requested = self._serve(monkeypatch, scraper, {})
        scraper._parse(
            _zhiyuan_page("announcement-list", [_zhiyuan_ann(1, "通知")], count=316), url
        )
        assert requested == ["https://zhiyuan.sjtu.edu.cn/announcement?category=74&page=2"]

    def test_max_pages_config_lowers_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper(max_pages=1)
        requested = self._serve(monkeypatch, scraper, {})
        scraper._parse(
            _zhiyuan_page("announcement-list", [_zhiyuan_ann(1, "通知")], count=1143),
            ZHIYUAN_ANNOUNCEMENTS_URL,
        )
        assert requested == []

    def test_fetch_with_status_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scraper = _scraper(target_urls=[ZHIYUAN_EVENTS_URL, ZHIYUAN_ANNOUNCEMENTS_URL])
        broken = _zhiyuan_page(
            "announcement-list", ['<a class="item" href="/post/1"></a>'], count=1143
        )
        self._serve(
            monkeypatch,
            scraper,
            {
                ZHIYUAN_EVENTS_URL: _zhiyuan_page("event-list", [_zhiyuan_event(1, "活动")]),
                ZHIYUAN_ANNOUNCEMENTS_URL: broken,
            },
        )
        result = scraper.fetch_with_status()
        assert [a.url for a in result.items] == ["https://zhiyuan.sjtu.edu.cn/event/1"]
        assert result.failed_urls == (ZHIYUAN_ANNOUNCEMENTS_URL,)

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://x.com/a", "https://x.com/a?page=2"),
            ("https://x.com/a?category=74", "https://x.com/a?category=74&page=2"),
            ("https://x.com/a?page=5&category=74", "https://x.com/a?category=74&page=2"),
        ],
    )
    def test_with_query_param(self, url: str, expected: str) -> None:
        assert _with_query_param(url, "page", "2") == expected

    def test_config_default_urls_include_zhiyuan(self) -> None:
        urls = ScraperConfig().target_urls
        assert ZHIYUAN_EVENTS_URL in urls
        assert ZHIYUAN_ANNOUNCEMENTS_URL in urls
        assert not any("/html/zhiyuan/" in u for u in urls), "旧版致远网址已失效"
        assert "https://jwc.sjtu.edu.cn/xwtg.htm" not in urls, "新闻通告应已移除"
        assert "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm" in urls


class TestHelpers:
    def test_unrecognized_layout_returns_none(self) -> None:
        """无法识别的页面结构按失败处理，而不是静默返回空列表"""
        assert _scraper()._parse("<html></html>", "https://example.com") is None

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
