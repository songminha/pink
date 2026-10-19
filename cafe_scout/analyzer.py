"""수집한 텍스트로 보험 제휴 여부를 판정하는 순수 로직 (브라우저와 무관, 테스트 가능)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import keywords as kw

STATUS_CONFIRMED = "입점 확인"
STATUS_SUSPECTED = "입점 의심(수동확인)"
STATUS_BLUE_OCEAN = "미입점 후보(블루오션)"
STATUS_INCOMPLETE = "판정 보류(일부 영역 수집 실패)"
STATUS_FAILED = "수집 실패"

# 근거 하나당 점수. 합계가 CONFIRMED_SCORE 이상이면 '입점 확인'
SCORE_MENU = 3            # 보험 전용 게시판 존재
SCORE_NOTICE_BOTH = 3     # 공지 제목에 제휴 + 보험이 함께 등장
SCORE_NOTICE_INS = 2      # 공지 제목에 보험만 등장
SCORE_NOTICE_PARTNER = 1  # 공지 제목에 제휴만 등장 (업종 불명 → 수동확인)
SCORE_SEARCH_NOTICE = 2   # 검색 결과 중 공지/고정글에 보험 등장
SCORE_SEARCH_HIT = 1      # 검색 결과 일반글 제목에 보험 등장
CONFIRMED_SCORE = 3


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def has_insurance(text: str) -> bool:
    """보험 키워드가 있는지. 4대보험 같은 비영업성 표현은 지운 뒤 검사한다."""
    t = _norm(text)
    for ex in kw.INSURANCE_EXCLUDE:
        t = t.replace(ex.lower(), " ")
    return any(k.lower() in t for k in kw.INSURANCE_KEYWORDS)


def has_partnership(text: str) -> bool:
    t = _norm(text)
    return any(k.lower() in t for k in kw.PARTNERSHIP_KEYWORDS)


@dataclass
class Link:
    text: str
    href: str = ""
    is_notice: bool = False


@dataclass
class CafeResult:
    name: str
    cafe_id: str
    url: str
    status: str = ""
    score: int = 0
    menu_evidence: list[str] = field(default_factory=list)
    notice_evidence: list[str] = field(default_factory=list)
    search_evidence: list[str] = field(default_factory=list)
    partner_notices: list[str] = field(default_factory=list)  # 제휴 공지(업종 불명)
    failed_areas: list[str] = field(default_factory=list)
    note: str = ""

    def to_row(self) -> dict:
        return {
            "카페명": self.name,
            "카페ID": self.cafe_id,
            "URL": self.url,
            "판정": self.status,
            "점수": self.score,
            "A_메뉴_근거": " | ".join(self.menu_evidence),
            "B_공지_근거": " | ".join(self.notice_evidence),
            "B_제휴공지(업종확인필요)": " | ".join(self.partner_notices),
            "C_검색_근거": " | ".join(self.search_evidence),
            "수집실패_영역": ",".join(self.failed_areas),
            "비고": self.note,
        }


def analyze_menus(menu_names: list[str]) -> list[str]:
    """영역 A: 보험 관련 독립 게시판 이름 목록."""
    return [m for m in dict.fromkeys(menu_names) if has_insurance(m)]


def analyze_notices(titles: list[str]) -> tuple[list[str], list[str], int]:
    """영역 B: (보험 근거 공지, 제휴만 있는 공지, 점수)."""
    insurance, partner_only, score = [], [], 0
    for t in dict.fromkeys(titles):
        ins, par = has_insurance(t), has_partnership(t)
        if ins:
            insurance.append(t)
            score += SCORE_NOTICE_BOTH if par else SCORE_NOTICE_INS
        elif par:
            partner_only.append(t)
            score += SCORE_NOTICE_PARTNER
    return insurance, partner_only, score


def analyze_search(results: dict[str, list[Link]]) -> tuple[list[str], int]:
    """영역 C: {검색어: 결과 링크들} → (근거 문자열, 점수). 같은 글은 한 번만 센다."""
    evidence, score, seen = [], 0, set()
    for query, links in results.items():
        for link in links:
            key = link.href or link.text
            if key in seen or not has_insurance(link.text):
                continue
            seen.add(key)
            tag = "공지" if link.is_notice else "일반"
            evidence.append(f"[{query}/{tag}] {link.text}")
            score += SCORE_SEARCH_NOTICE if link.is_notice else SCORE_SEARCH_HIT
    return evidence, score


def decide(result: CafeResult) -> CafeResult:
    """영역별 근거가 채워진 result 의 점수와 판정을 확정한다."""
    if result.status == STATUS_FAILED:
        return result
    if result.score >= CONFIRMED_SCORE:
        result.status = STATUS_CONFIRMED
    elif result.score > 0:
        result.status = STATUS_SUSPECTED
    elif result.failed_areas:
        result.status = STATUS_INCOMPLETE
    else:
        result.status = STATUS_BLUE_OCEAN
    return result
