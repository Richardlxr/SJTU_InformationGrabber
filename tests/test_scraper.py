"""
单元测试 - scraper（使用本地 HTML fixture，不发送真实请求）
"""

from web_bugger.config import ScraperConfig
from web_bugger.scraper import Scraper

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


class TestScraperXwtg:
    """布局 A: xwtg.htm 板块式"""

    def test_parse_sample_html(self) -> None:
        config = ScraperConfig(
            target_urls=["https://jwc.sjtu.edu.cn/xwtg.htm"],
            base_url="https://jwc.sjtu.edu.cn/",
        )
        scraper = Scraper(config)
        results = scraper._parse(SAMPLE_XWTG_HTML, "https://jwc.sjtu.edu.cn/xwtg.htm")

        assert len(results) == 3

        # 第一条：相对链接
        assert results[0].title == "公告标题一"
        assert results[0].url == "https://jwc.sjtu.edu.cn/info/1025/1001.htm"
        assert results[0].section == "新闻中心"
        assert results[0].date == "2026-03-02"

        # 第二条：绝对链接
        assert results[1].url == "https://external.com/post"

        # 第三条：不同板块
        assert results[2].section == "质控办"


class TestScraperMxxsdtz:
    """布局 B: mxxsdtz.htm 列表式"""

    def test_parse_sample_html(self) -> None:
        config = ScraperConfig(
            target_urls=["https://jwc.sjtu.edu.cn/index/mxxsdtz.htm"],
            base_url="https://jwc.sjtu.edu.cn/",
        )
        scraper = Scraper(config)
        results = scraper._parse(
            SAMPLE_MXXSDTZ_HTML,
            "https://jwc.sjtu.edu.cn/index/mxxsdtz.htm",
        )

        assert len(results) == 2

        # 第一条：相对链接 + 日期组合
        assert results[0].title == "关于 2026 年春季选课的通知"
        assert results[0].url == "https://jwc.sjtu.edu.cn/info/1222/12345.htm"
        assert results[0].date == "2026-03-15"
        assert results[0].section == "面向学生的通知"

        # 第二条：绝对链接
        assert results[1].url == "https://example.com/external"
        assert results[1].date == "2026-03-02"

    def test_date_extraction(self) -> None:
        config = ScraperConfig()
        scraper = Scraper(config)
        from bs4 import BeautifulSoup

        html = (
            '<li class="clearfix"><div class="sj"><h2>5</h2><p>2026.01</p></div></li>'
        )
        tag = BeautifulSoup(html, "html.parser").select_one("li")
        assert scraper._extract_mxxsdtz_date(tag) == "2026-01-05"


class TestScraperCommon:
    def test_parse_empty_html(self) -> None:
        config = ScraperConfig()
        scraper = Scraper(config)
        assert scraper._parse("<html></html>", "https://example.com") == []

    def test_resolve_url_relative(self) -> None:
        config = ScraperConfig(base_url="https://jwc.sjtu.edu.cn/")
        scraper = Scraper(config)
        assert (
            scraper._resolve_url("info/1025/1001.htm")
            == "https://jwc.sjtu.edu.cn/info/1025/1001.htm"
        )

    def test_resolve_url_absolute(self) -> None:
        config = ScraperConfig()
        scraper = Scraper(config)
        assert (
            scraper._resolve_url("https://external.com/x") == "https://external.com/x"
        )
