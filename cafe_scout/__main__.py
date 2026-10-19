"""실행: python -m cafe_scout cafes.csv [-o 결과.csv] [--headless] ...

입력 CSV 는 '카페명,카페ID' 두 열(헤더 필수). 카페ID 칸에는 ID 대신 카페 URL 을 넣어도 된다.
영문 헤더(name,id)도 인식한다.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from pathlib import Path

from . import analyzer as an
from .crawler import BlockedError, CafeCrawler, build_driver, parse_cafe_id

NAME_COLS = ("카페명", "name")
ID_COLS = ("카페ID", "카페id", "id", "url", "URL", "주소")


def load_cafes(path: Path) -> list[tuple[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        name_col = next((c for c in NAME_COLS if c in cols), None)
        id_col = next((c for c in ID_COLS if c in cols), None)
        if not id_col:
            sys.exit(f"입력 CSV 에 카페ID 열이 없습니다. 지원하는 헤더: {', '.join(ID_COLS)} / 현재: {cols}")
        return [((row.get(name_col) or "").strip() if name_col else "", row[id_col].strip())
                for row in reader if (row.get(id_col) or "").strip()]


def load_done(path: Path) -> set[str]:
    """이미 결과 파일에 있는 카페ID (실패 건은 다시 시도하도록 제외)."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["카페ID"] for r in csv.DictReader(f) if r.get("판정") != an.STATUS_FAILED}


def append_rows(path: Path, rows: list[dict]):
    if not rows:
        return
    new = not path.exists()
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new:
            w.writeheader()
        w.writerows(rows)


def write_blue_ocean(result_path: Path, out_path: Path):
    with result_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # 같은 카페가 재시도로 여러 번 있으면 마지막 결과만 사용
    latest = {r["카페ID"]: r for r in rows}
    picked = [r for r in latest.values() if r["판정"] == an.STATUS_BLUE_OCEAN]
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["카페ID"])
        w.writeheader()
        w.writerows(picked)
    return len(picked)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cafe_scout", description="네이버 카페 보험 제휴 미입점(빈틈) 카페 탐색기")
    p.add_argument("input", type=Path, help="카페 목록 CSV (카페명,카페ID)")
    p.add_argument("-o", "--output", type=Path, default=Path("카페_보험제휴_조사결과.csv"))
    p.add_argument("--blue-ocean", type=Path, default=Path("보험_미입점_카페_리스트.csv"),
                   help="'미입점 후보'만 추린 파일")
    p.add_argument("--headless", action="store_true", help="브라우저 창을 띄우지 않음 (캡차 위험이 조금 더 높음)")
    p.add_argument("--profile-dir", help="로그인해 둔 크롬 프로필 폴더 (회원공개 메뉴까지 보려면)")
    p.add_argument("--chrome-binary", help="크롬 실행 파일 경로 (자동 탐지가 안 될 때)")
    p.add_argument("--min-delay", type=float, default=3.0, help="페이지 요청 간 최소 대기(초)")
    p.add_argument("--max-delay", type=float, default=6.0, help="페이지 요청 간 최대 대기(초)")
    p.add_argument("--cafe-pause", type=float, nargs=2, default=(8.0, 15.0), metavar=("MIN", "MAX"),
                   help="카페와 카페 사이 추가 휴식(초)")
    p.add_argument("--limit", type=int, help="이번 실행에서 조사할 최대 카페 수")
    p.add_argument("--fresh", action="store_true", help="기존 결과를 무시하고 처음부터 (기본은 이어하기)")
    args = p.parse_args(argv)

    cafes = load_cafes(args.input)
    if args.fresh and args.output.exists():
        args.output.unlink()
    done = load_done(args.output)
    todo = [(n, c) for n, c in cafes if parse_cafe_id(c) not in done]
    already = len(cafes) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"=== 대상 {len(cafes)}개 중 이미 조사 {already}개, 이번 실행 {len(todo)}개 ===")
    if not todo:
        return

    driver = build_driver(args.headless, args.profile_dir, args.chrome_binary)
    crawler = CafeCrawler(driver, args.min_delay, args.max_delay)
    try:
        for i, (name, cafe) in enumerate(todo, 1):
            try:
                r = crawler.crawl(name, cafe)
            except BlockedError as e:
                print(f"\n[⛔ 중단] {e}\n  → 몇 시간 뒤 다시 실행하면 이어서 진행합니다. (--min-delay 를 늘려 보세요)")
                break
            append_rows(args.output, [r.to_row()])
            icon = {an.STATUS_BLUE_OCEAN: "🔥", an.STATUS_CONFIRMED: "❌", an.STATUS_SUSPECTED: "🔎"}.get(r.status, "⚠️")
            detail = r.menu_evidence + r.notice_evidence + r.search_evidence + r.partner_notices
            print(f"[{i}/{len(todo)}] {icon} {r.name} ({r.cafe_id}) → {r.status} (점수 {r.score})"
                  + (f" : {detail[0]}" if detail else "") + (f" / {r.note}" if r.note else ""))
            if i < len(todo):
                time.sleep(random.uniform(*args.cafe_pause))
    finally:
        driver.quit()

    if args.output.exists():
        n = write_blue_ocean(args.output, args.blue_ocean)
        print(f"=== 완료: 전체 결과 {args.output} / 미입점 후보 {n}곳 → {args.blue_ocean} ===")


if __name__ == "__main__":
    main()
