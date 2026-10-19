"""Selenium 으로 네이버 카페의 메뉴판(A), 공지(B), 내부 검색(C)을 수집한다.

네이버 카페는 구버전(iframe `cafe_main`)과 신버전(`/f-e/cafes/...`) 화면이 섞여 있어
CSS 선택자 대신 '모든 링크의 href 패턴'으로 메뉴/게시글을 구분한다.
이렇게 하면 화면 개편이 있어도 링크 주소 규칙만 유지되면 그대로 동작한다.
"""

from __future__ import annotations

import random
import re
import time
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import NoSuchFrameException, WebDriverException

from . import analyzer as an
from . import keywords as kw

CAFE_BASE = "https://cafe.naver.com"

# 메뉴 링크: 구버전 ...search.menuid=12 / 신버전 /cafes/123/menus/12
MENU_HREF = re.compile(r"(search\.menuid=\d+|/menus/\d+)", re.I)
# 게시글 링크: 구버전 articleid=123 / 신버전 /articles/123 또는 /카페ID/123
ARTICLE_HREF = re.compile(r"(articleid=\d+|/articles/\d+)", re.I)
CLUBID_PATTERNS = [
    re.compile(r"clubid[=\"':\s]+(\d+)", re.I),
    re.compile(r"/cafes/(\d+)"),
    re.compile(r"g_sClubId\s*=\s*\"?(\d+)"),
]
NOTICE_ROW = re.compile(r"(notice|공지|필독)", re.I)
NO_RESULT_TEXT = ("검색결과가 없습니다", "등록된 게시글이 없습니다", "게시글이 없습니다")

# 페이지 안의 모든 링크와, 링크가 속한 행(tr/li)의 클래스·텍스트를 한 번에 뽑는 JS
_COLLECT_LINKS_JS = """
return Array.from(document.querySelectorAll('a[href]')).map(a => {
  const row = a.closest('tr, li, .article-board, .ArticleItem');
  return {
    text: (a.innerText || a.textContent || '').trim(),
    href: a.href,
    row: row ? ((row.className || '') + ' ' + (row.innerText || '').slice(0, 120)) : ''
  };
});
"""


class BlockedError(RuntimeError):
    """캡차/로그인 요구 화면이 떴을 때. 계속 요청하면 IP 차단 위험이 커지므로 전체 실행을 멈춘다."""


def parse_cafe_id(value: str) -> str:
    """'momsolleh', 'https://cafe.naver.com/momsolleh', 'cafe.naver.com/momsolleh?x=1' → 'momsolleh'."""
    value = value.strip()
    m = re.search(r"cafe\.naver\.com/([^/?#]+)", value)
    return m.group(1) if m else value.strip("/")


def build_driver(headless: bool = False, profile_dir: str | None = None,
                 chrome_binary: str | None = None) -> webdriver.Chrome:
    """크롬 드라이버 생성. Selenium 4.6+ 는 드라이버를 자동으로 받아오므로 webdriver_manager 불필요."""
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--lang=ko-KR")
    if profile_dir:
        # 직접 한 번 로그인해 둔 크롬 프로필을 재사용 (회원 공개 메뉴까지 보려면 필요)
        options.add_argument(f"--user-data-dir={profile_dir}")
    if chrome_binary:
        options.binary_location = chrome_binary
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


class CafeCrawler:
    def __init__(self, driver, min_delay: float = 3.0, max_delay: float = 6.0,
                 base_url: str = CAFE_BASE, search_url_template: str | None = None):
        self.driver = driver
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.base_url = base_url.rstrip("/")
        # {clubid}, {query} 를 채워 쓰는 검색 URL. 기본값은 구버전 검색 화면(EUC-KR 인코딩).
        self.search_url_template = search_url_template

    # ---- 공통 유틸 -------------------------------------------------------
    def _sleep(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _get(self, url: str):
        self.driver.get(url)
        self._sleep()
        self._check_blocked()

    def _check_blocked(self):
        # page_source 에는 평소에도 'captcha' 문자열이 섞여 있을 수 있어 URL 과 보이는 텍스트만 본다
        url = self.driver.current_url
        text = self.driver.execute_script("return document.body ? document.body.innerText : ''") or ""
        if "nid.naver.com" in url or "captcha" in url.lower() or "자동입력 방지" in text:
            raise BlockedError(f"캡차/로그인 화면 감지: {url}")

    def _collect_links(self) -> list[dict]:
        """최상위 문서 + cafe_main iframe 의 링크를 모두 모은다."""
        links = list(self.driver.execute_script(_COLLECT_LINKS_JS) or [])
        try:
            self.driver.switch_to.frame("cafe_main")
            links += self.driver.execute_script(_COLLECT_LINKS_JS) or []
        except NoSuchFrameException:
            pass  # 신버전 화면은 iframe 이 없음
        finally:
            self.driver.switch_to.default_content()
        return links

    def _page_text(self) -> str:
        text = self.driver.execute_script("return document.body ? document.body.innerText : ''") or ""
        try:
            self.driver.switch_to.frame("cafe_main")
            text += "\n" + (self.driver.execute_script("return document.body ? document.body.innerText : ''") or "")
        except NoSuchFrameException:
            pass
        finally:
            self.driver.switch_to.default_content()
        return text

    def _find_clubid(self, links: list[dict]) -> str | None:
        sources = [l["href"] for l in links] + [self.driver.page_source]
        for src in sources:
            for pat in CLUBID_PATTERNS:
                m = pat.search(src)
                if m:
                    return m.group(1)
        return None

    @staticmethod
    def _articles(links: list[dict]) -> list[an.Link]:
        out = []
        for l in links:
            if l["text"] and ARTICLE_HREF.search(l["href"]):
                out.append(an.Link(text=l["text"], href=l["href"],
                                   is_notice=bool(NOTICE_ROW.search(l.get("row", "")))))
        return out

    # ---- 영역별 수집 -----------------------------------------------------
    def _search_url(self, clubid: str, query: str) -> str:
        if self.search_url_template:
            return self.search_url_template.format(clubid=clubid, query=quote(query))
        # 구버전 카페 검색은 검색어를 EUC-KR 로 인코딩해야 한글이 깨지지 않는다
        return (f"{self.base_url}/ArticleSearchList.nhn?search.clubid={clubid}"
                f"&search.searchBy=0&search.query={quote(query, encoding='euc-kr')}")

    def crawl(self, name: str, cafe_id_or_url: str) -> an.CafeResult:
        cafe_id = parse_cafe_id(cafe_id_or_url)
        url = f"{self.base_url}/{cafe_id}"
        result = an.CafeResult(name=name or cafe_id, cafe_id=cafe_id, url=url)

        # 영역 A + B: 카페 메인(대문) — 왼쪽 메뉴판과 대문/전체공지가 함께 보인다
        try:
            self._get(url)
            links = self._collect_links()
        except BlockedError:
            raise
        except WebDriverException as e:
            result.status = an.STATUS_FAILED
            result.note = f"메인 접속 실패: {e.msg or e}"[:200]
            return result

        menus = [l["text"] for l in links if l["text"] and MENU_HREF.search(l["href"])]
        if menus:
            result.menu_evidence = an.analyze_menus(menus)
            result.score += an.SCORE_MENU * len(result.menu_evidence)
        else:
            result.failed_areas.append("A")

        notice_titles = [a.text for a in self._articles(links) if a.is_notice]
        # 대문에 공지가 없으면 '공지사항' 메뉴에 들어가서 제목을 읽는다
        notice_menu = next((l for l in links if MENU_HREF.search(l["href"]) and "공지" in l["text"]), None)
        if notice_menu:
            try:
                self._get(notice_menu["href"])
                notice_titles += [a.text for a in self._articles(self._collect_links())]
            except BlockedError:
                raise
            except WebDriverException:
                pass
        if notice_titles:
            ins, partner, score = an.analyze_notices(notice_titles)
            result.notice_evidence, result.partner_notices = ins, partner
            result.score += score
        else:
            result.failed_areas.append("B")

        # 영역 C: 카페 내부 검색
        clubid = self._find_clubid(links)
        if not clubid:
            result.failed_areas.append("C")
            result.note = "clubid 를 찾지 못해 내부 검색 생략"
            return an.decide(result)
        search_results: dict[str, list[an.Link]] = {}
        search_ok = True
        for query in kw.SEARCH_QUERIES:
            try:
                self._get(self._search_url(clubid, query))
            except BlockedError:
                raise
            except WebDriverException:
                search_ok = False
                continue
            articles = self._articles(self._collect_links())
            if not articles and not any(t in self._page_text() for t in NO_RESULT_TEXT):
                search_ok = False  # 결과 0건인지 화면 구조가 달라 못 읽은 건지 구분 불가
            search_results[query] = articles
        result.search_evidence, score = an.analyze_search(search_results)
        result.score += score
        if not search_ok:
            result.failed_areas.append("C")
        return an.decide(result)
