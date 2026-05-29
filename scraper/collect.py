"""
메인 수집 스크립트.
GitHub Actions에서 매일 실행되어 docs/data/{date}.json 과 manifest.json 을 갱신한다.

로컬 테스트: python scraper/collect.py
환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, DART_API_KEY (없으면 해당 소스 건너뜀)
"""

import os
import sys
import json
import time
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "docs", "data")
CONFIG_PATH = os.path.join(ROOT, "config.json")

sys.path.insert(0, HERE)
import sources  # noqa: E402
import classify as clf  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def match_rss_category(text, rss_match_terms, category_order):
    """RSS 기사 텍스트에서 추적 키워드를 찾아 카테고리를 정한다.
    우선순위(category_order 순)대로 검사, 하나도 안 걸리면 None(→ 버림)."""
    text = (text or "").lower()
    for cat in category_order:
        for term in rss_match_terms.get(cat, []):
            if term.lower() in text:
                return cat
    return None


def collect_all(cfg):
    items = []
    display = cfg.get("max_per_query", 30)

    # --- 국내: 네이버 + 구글뉴스(한국어) ---
    for cat, queries in cfg["queries"].items():
        for q in queries:
            print(f"수집: [{cat}] {q}")
            batch = sources.fetch_naver(q, display=display) + sources.fetch_gnews(q, display=display, lang="ko")
            for it in batch:
                it["origin_cat"] = cat
                it["layer"] = "news"
            items += batch
            time.sleep(0.3)

    # --- 해외: 구글뉴스(영문) ---
    for cat, queries in cfg.get("en_queries", {}).items():
        for q in queries:
            print(f"수집(EN): [{cat}] {q}")
            batch = sources.fetch_gnews(q, display=display, lang="en")
            for it in batch:
                it["origin_cat"] = cat
                it["layer"] = "news"
            items += batch
            time.sleep(0.3)

    # --- 해외 전문매체 RSS (키워드 필터링) ---
    rss_terms = cfg.get("rss_match_terms", {})
    order = cfg.get("category_order", [])
    for feed in cfg.get("rss_feeds", []):
        print(f"수집(RSS): {feed['name']}")
        raw = sources.fetch_rss(feed["name"], feed["url"])
        kept = 0
        for it in raw:
            cat = match_rss_category(it["title"] + " " + it.get("summary", ""), rss_terms, order)
            if cat:
                it["origin_cat"] = cat
                it["layer"] = "news"
                items.append(it)
                kept += 1
        print(f"          {len(raw)}건 중 {kept}건 관련 기사 채택")
        time.sleep(0.3)

    # --- 논문: PubMed ---
    pm = cfg.get("pubmed", {})
    if pm.get("enabled"):
        for cat, queries in pm.get("queries", {}).items():
            for q in queries:
                print(f"수집(논문): [{cat}] {q}")
                batch = sources.fetch_pubmed(q, recent_days=pm.get("recent_days", 30), email=pm.get("email", ""))
                for it in batch:
                    it["origin_cat"] = cat
                    it["layer"] = "paper"
                items += batch
                time.sleep(0.4)  # NCBI 키 없으면 초당 3회 제한

    # --- 임상: ClinicalTrials.gov ---
    ct = cfg.get("clinicaltrials", {})
    if ct.get("enabled"):
        for cat, queries in ct.get("queries", {}).items():
            for q in queries:
                print(f"수집(임상): [{cat}] {q}")
                batch = sources.fetch_clinicaltrials(q, recent_days=ct.get("recent_days", 60))
                for it in batch:
                    it["origin_cat"] = cat
                    it["layer"] = "trial"
                items += batch
                time.sleep(0.3)

    # --- DART 공시 (옵션) ---
    if cfg.get("dart", {}).get("enabled"):
        d = cfg["dart"]
        print(f"수집: [DART] {d['corp_name']} 공시")
        dart_items = sources.fetch_dart(d["corp_name"], d.get("stock_code"), days_back=3)
        for it in dart_items:
            it["origin_cat"] = "core"
            it["layer"] = "news"
        items += dart_items

    return items


def to_records(items, cfg):
    """분류 + 중복 제거 후 화면용 레코드로 변환."""
    items = clf.dedupe(items)
    ck = cfg["classify_keywords"]
    records = []
    for it in items:
        cat = clf.classify(it, ck)
        published = it.get("published", "")
        time_label = ""
        date_label = ""
        try:
            d = dt.datetime.fromisoformat(published).astimezone(KST)
            time_label = d.strftime("%H:%M")
            date_label = d.strftime("%m.%d")
        except (ValueError, TypeError):
            pass
        rec = {
            "title": it.get("title", ""),
            "summary": it.get("summary", ""),
            "url": it.get("url", ""),
            "publisher": it.get("publisher", ""),
            "source": it.get("source", ""),
            "layer": it.get("layer", "news"),
            "published": published,
            "time": time_label,
            "date": date_label,
            "category": cat,
        }
        if it.get("source") == "ctgov":
            rec["ct_status"] = it.get("ct_status", "")
            rec["ct_phase"] = it.get("ct_phase", "")
        records.append(rec)
    return records


def day_key(records):
    """오늘 날짜(KST) 기준 파일명."""
    return dt.datetime.now(KST).strftime("%Y-%m-%d")


def merge_existing(path, new_records):
    """같은 날짜 파일이 이미 있으면 병합 후 재-중복제거 (하루 여러 번 실행 대비)."""
    if not os.path.exists(path):
        return new_records
    try:
        with open(path, "r", encoding="utf-8") as f:
            old = json.load(f).get("items", [])
    except (ValueError, OSError):
        old = []
    combined = old + new_records
    return clf.dedupe(combined)


def write_day(date_str, records, cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{date_str}.json")
    records = merge_existing(path, records)

    # 카테고리 순서대로, 그 안에서는 시간 내림차순 정렬
    order = {c: i for i, c in enumerate(cfg["category_order"])}
    records.sort(key=lambda r: (order.get(r["category"], 99), r.get("published", "")), reverse=False)
    records.sort(key=lambda r: r.get("published", ""), reverse=True)
    records.sort(key=lambda r: order.get(r["category"], 99))

    counts = {}
    for r in records:
        counts[r["category"]] = counts.get(r["category"], 0) + 1

    payload = {
        "date": date_str,
        "generated_at": dt.datetime.now(KST).isoformat(),
        "total": len(records),
        "counts": counts,
        "items": records,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"저장: {path} ({len(records)}건)")
    return payload


def rebuild_manifest(cfg):
    """data 폴더의 날짜 파일들을 스캔해 manifest.json 재생성.
    keep_days: 보관 일수. 0 이하이면 영구 보관(아무것도 삭제하지 않음)."""
    keep = cfg.get("keep_days", 0)
    files = sorted(
        [f for f in os.listdir(DATA_DIR) if f.endswith(".json") and f != "manifest.json"],
        reverse=True,
    )
    if keep and keep > 0:
        files = files[:keep]

    entries = []
    valid = set()
    for fn in files:
        try:
            with open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            entries.append({
                "date": d.get("date", fn[:-5]),
                "total": d.get("total", 0),
                "counts": d.get("counts", {}),
            })
            valid.add(fn)
        except (ValueError, OSError):
            continue

    # 보존기간 초과 파일 삭제 (keep_days > 0 일 때만)
    if keep and keep > 0:
        for fn in os.listdir(DATA_DIR):
            if fn.endswith(".json") and fn != "manifest.json" and fn not in valid:
                try:
                    os.remove(os.path.join(DATA_DIR, fn))
                    print(f"정리: {fn} 삭제 (보존기간 초과)")
                except OSError:
                    pass

    manifest = {
        "site_title": cfg.get("site_title", "Scrap Master"),
        "site_subtitle": cfg.get("site_subtitle", ""),
        "category_meta": cfg.get("category_meta", {}),
        "category_order": cfg.get("category_order", []),
        "keywords": cfg.get("queries", {}),
        "updated_at": dt.datetime.now(KST).isoformat(),
        "dates": entries,
    }
    with open(os.path.join(DATA_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"manifest 갱신: {len(entries)}일치")


def main():
    cfg = load_config()
    items = collect_all(cfg)
    print(f"\n총 원시 수집: {len(items)}건")
    records = to_records(items, cfg)
    date_str = day_key(records)
    write_day(date_str, records, cfg)
    rebuild_manifest(cfg)
    print("\n완료.")


if __name__ == "__main__":
    main()
