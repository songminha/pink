from cafe_scout import analyzer as an
from cafe_scout.crawler import parse_cafe_id


def test_has_insurance_ignores_social_insurance():
    assert an.has_insurance("태아보험 상담방")
    assert an.has_insurance("무료 보장분석")
    assert not an.has_insurance("4대보험/실업급여 정보")
    assert not an.has_insurance("건강보험료 질문")
    assert not an.has_insurance("인테리어 설계사무소 추천")
    # 제외 표현과 진짜 보험이 같이 있으면 보험으로 본다
    assert an.has_insurance("4대보험 말고 실손보험 상담")


def test_analyze_menus():
    assert an.analyze_menus(["자유게시판", "보험상담", "보험상담", "육아정보"]) == ["보험상담"]


def test_analyze_notices_scores():
    ins, partner, score = an.analyze_notices([
        "[필독] 공식 보험 제휴업체 안내",   # 제휴+보험 → 3
        "[공지] 협력업체 모집",             # 제휴만 → 1
        "카페 이용 규칙",
    ])
    assert ins == ["[필독] 공식 보험 제휴업체 안내"]
    assert partner == ["[공지] 협력업체 모집"]
    assert score == 4


def test_analyze_search_dedupes_across_queries():
    link = an.Link("공식 보험스텝 OOO 입니다", "https://x/articles/1", is_notice=True)
    evidence, score = an.analyze_search({"보험스텝": [link], "공식보험": [link]})
    assert len(evidence) == 1
    assert score == an.SCORE_SEARCH_NOTICE


def test_decide():
    r = an.CafeResult("a", "a", "u")
    assert an.decide(r).status == an.STATUS_BLUE_OCEAN
    r = an.CafeResult("a", "a", "u", failed_areas=["C"])
    assert an.decide(r).status == an.STATUS_INCOMPLETE
    r = an.CafeResult("a", "a", "u", score=1)
    assert an.decide(r).status == an.STATUS_SUSPECTED
    r = an.CafeResult("a", "a", "u", score=3)
    assert an.decide(r).status == an.STATUS_CONFIRMED


def test_parse_cafe_id():
    assert parse_cafe_id("momsolleh") == "momsolleh"
    assert parse_cafe_id("https://cafe.naver.com/momsolleh") == "momsolleh"
    assert parse_cafe_id("cafe.naver.com/momsolleh?iframe_url=x") == "momsolleh"
