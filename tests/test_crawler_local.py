"""가짜 카페 페이지를 로컬 서버로 띄워 Selenium 수집 흐름(iframe 전환, 링크 분류, 검색)을 검증한다.

실제 네이버 화면 구조와 100% 같지는 않으며, 링크 패턴 기반 분류가 동작하는지 확인하는 용도.
크롬이 없으면 건너뛴다. 크롬 경로는 CHROME_BINARY 환경변수로 지정할 수 있다.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from cafe_scout import analyzer as an
from cafe_scout.crawler import BlockedError, CafeCrawler

PAGES = {
    # 구버전: 왼쪽 메뉴 + cafe_main iframe(대문/전체공지)
    "/insured": """
      <div id="cafe-menu">
        <a href="/ArticleList.nhn?search.clubid=111&search.menuid=1">공지사항</a>
        <a href="/ArticleList.nhn?search.clubid=111&search.menuid=2">자유게시판</a>
        <a href="/ArticleList.nhn?search.clubid=111&search.menuid=3">태아보험 상담</a>
      </div>
      <iframe name="cafe_main" id="cafe_main" src="/intro?clubid=111"></iframe>""",
    "/intro": """
      <table>
        <tr class="board-notice"><td><a href="/ArticleRead.nhn?articleid=10">[필독] 공식 협력업체 안내</a></td></tr>
        <tr><td><a href="/ArticleRead.nhn?articleid=11">오늘 날씨 좋네요</a></td></tr>
      </table>""",
    "/ArticleList.nhn": """
      <iframe name="cafe_main" src="/noticelist"></iframe>""",
    "/noticelist": """
      <ul><li><a href="/ArticleRead.nhn?articleid=20">보험 제휴 업체 변경 안내</a></li></ul>""",
    # 신버전: iframe 없음
    "/blue": """
      <nav>
        <a href="/f-e/cafes/222/menus/0">전체글보기</a>
        <a href="/f-e/cafes/222/menus/4">부동산 질문</a>
        <a href="/f-e/cafes/222/menus/5">4대보험·세금 정보</a>
      </nav>
      <ul><li class="notice"><a href="/f-e/cafes/222/articles/1">카페 이용 규칙</a></li></ul>""",
    "/search": "<p>검색결과가 없습니다.</p>",
    "/captcha": "<p>자동입력 방지 문자를 입력해 주세요</p>",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/search" and parse_qs(u.query).get("clubid") == ["111"]:
            body = '<ul><li><a href="/ArticleRead.nhn?articleid=30">보험스텝 김OO입니다</a></li></ul>'
        else:
            body = PAGES.get(u.path, "<p>not found</p>")
        data = f"<html><head><meta charset='utf-8'></head><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def crawler():
    try:
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        for a in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
            options.add_argument(a)
        binary = os.environ.get("CHROME_BINARY")
        if binary:
            options.binary_location = binary
        driver = webdriver.Chrome(options=options)
    except Exception as e:  # 크롬/드라이버 없음
        pytest.skip(f"Chrome 실행 불가: {e}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield CafeCrawler(driver, 0, 0, base_url=base,
                      search_url_template=base + "/search?clubid={clubid}&q={query}")
    driver.quit()
    server.shutdown()


def test_insured_cafe_old_layout(crawler):
    r = crawler.crawl("보험있는카페", "insured")
    assert r.menu_evidence == ["태아보험 상담"]
    assert r.notice_evidence == ["보험 제휴 업체 변경 안내"]
    assert r.partner_notices == ["[필독] 공식 협력업체 안내"]
    assert any("보험스텝" in e for e in r.search_evidence)
    assert r.failed_areas == []
    assert r.status == an.STATUS_CONFIRMED


def test_blue_ocean_cafe_new_layout(crawler):
    r = crawler.crawl("빈틈카페", "https://cafe.naver.com/blue")
    assert r.menu_evidence == []  # 4대보험은 제외어
    assert r.failed_areas == []
    assert r.score == 0
    assert r.status == an.STATUS_BLUE_OCEAN


def test_captcha_stops_run(crawler):
    with pytest.raises(BlockedError):
        crawler.crawl("막힘", "captcha")
