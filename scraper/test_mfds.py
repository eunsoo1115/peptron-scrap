"""식약처(MFDS) 게시판 수집 테스트 — GitHub Actions에서 1회 실행해 실제 긁히는지 확인용.
수집 파이프라인/사이트에 영향 없음. 결과를 로그에만 출력한다.

실행: python scraper/test_mfds.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import sources as S

BOARDS = [
    ("m_1060", "민원인안내서"),
    ("m_74", "공지"),
    ("m_99", "보도자료"),
]

def main():
    print("=" * 60)
    print("식약처(MFDS) 게시판 수집 테스트")
    print("=" * 60)
    total = 0
    for bid, label in BOARDS:
        print(f"\n--- {label} ({bid}) ---")
        rows = S.fetch_mfds_board(bid, label, pages=1)
        total += len(rows)
        for r in rows[:5]:
            print(f"  · {r['title'][:50]}")
            print(f"      날짜:{r.get('date','')} 번호:{r.get('regno','')}")
            print(f"      {r['url']}")
        if len(rows) > 5:
            print(f"  ... 외 {len(rows)-5}건")
    print("\n" + "=" * 60)
    if total == 0:
        print("결과: 0건 — 식약처가 차단했거나 HTML 구조가 달라 파싱 실패.")
        print("→ 위 [mfds:...] 로그에서 '실패' 또는 '0건' 원인 확인 필요.")
    else:
        print(f"결과: 총 {total}건 수집 성공! 파서가 실제 사이트에서 작동함.")
    print("=" * 60)

if __name__ == "__main__":
    main()
