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

    # --- 국내 뉴스: 네이버 + 구글뉴스(한국어) → region=kr ---
    for cat, queries in cfg["queries"].items():
        for q in queries:
            print(f"수집: [{cat}] {q}")
            batch = sources.fetch_naver(q, display=display) + sources.fetch_gnews(q, display=display, lang="ko")
            for it in batch:
                it["origin_cat"] = cat
                it["layer"] = "news"
                it["region"] = "kr"
            items += batch
            time.sleep(0.3)

    # --- 해외 뉴스: 구글뉴스(영문) → region=os ---
    for cat, queries in cfg.get("en_queries", {}).items():
        for q in queries:
            print(f"수집(EN): [{cat}] {q}")
            batch = sources.fetch_gnews(q, display=display, lang="en")
            for it in batch:
                it["origin_cat"] = cat
                it["layer"] = "news"
                it["region"] = "os"
            items += batch
            time.sleep(0.3)

    # --- 해외 전문매체 RSS (키워드 필터링) → region=os ---
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
                it["region"] = "os"
                items.append(it)
                kept += 1
        print(f"          {len(raw)}건 중 {kept}건 관련 기사 채택")
        time.sleep(0.3)

    # --- 논문: PubMed (최근 N일 누적) ---
    pm = cfg.get("pubmed", {})
    if pm.get("enabled"):
        for cat, queries in pm.get("queries", {}).items():
            for q in queries:
                print(f"수집(논문): [{cat}] {q}")
                batch = sources.fetch_pubmed(q, recent_days=pm.get("recent_days", 1095),
                                             retmax=pm.get("retmax", 100), email=pm.get("email", ""))
                for it in batch:
                    it["origin_cat"] = cat
                    it["layer"] = "paper"
                items += batch
                time.sleep(0.4)

    # --- 임상: ClinicalTrials.gov (최근 N일 누적) ---
    ct = cfg.get("clinicaltrials", {})
    if ct.get("enabled"):
        for cat, queries in ct.get("queries", {}).items():
            for q in queries:
                print(f"수집(임상): [{cat}] {q}")
                batch = sources.fetch_clinicaltrials(q, recent_days=ct.get("recent_days", 1095),
                                                     pagesize=ct.get("pagesize", 100))
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
            it["region"] = "kr"
        items += dart_items

    return items


def to_records(items, cfg):
    """분류 + 중복 제거 + 파이프라인·회사 태깅 후 화면용 레코드로 변환."""
    # 노이즈 제외: 제목/요약에 제외 키워드가 있으면 버림 (단, 뉴스에만 적용 — 논문/임상은 그대로)
    excl = [w.lower() for w in cfg.get("exclude_keywords", []) if w.strip()]
    if excl:
        kept = []
        for it in items:
            if it.get("layer") == "news":
                blob = (it.get("title", "") + " " + it.get("summary", "")).lower()
                if any(w in blob for w in excl):
                    continue
            kept.append(it)
        items = kept
    items = clf.dedupe(items)
    ck = cfg["classify_keywords"]
    pdefs = cfg.get("pipelines", [])
    cdefs = cfg.get("companies", [])
    records = []
    for it in items:
        cat = clf.classify(it, ck)
        text = it.get("title", "") + " " + it.get("summary", "")
        published = it.get("published", "")
        time_label = ""
        date_label = ""
        year = None
        try:
            d = dt.datetime.fromisoformat(published).astimezone(KST)
            time_label = d.strftime("%H:%M")
            date_label = d.strftime("%Y.%m.%d")
            year = d.year
        except (ValueError, TypeError):
            pass
        rec = {
            "title": it.get("title", ""),
            "summary": it.get("summary", ""),
            "url": it.get("url", ""),
            "publisher": it.get("publisher", ""),
            "source": it.get("source", ""),
            "layer": it.get("layer", "news"),
            "region": it.get("region", ""),
            "published": published,
            "time": time_label,
            "date": date_label,
            "year": year,
            "category": cat,
            "pipelines": clf.tag_by_defs(text, pdefs),
            "companies": clf.tag_by_defs(text, cdefs),
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


def accumulate_layer(filename, new_records, years=None, days=None):
    """논문/임상/최근뉴스: 날짜에 가두지 않고 누적. 중복 제거 + 보존기간 + 최신순."""
    path = os.path.join(DATA_DIR, filename)
    old = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f).get("items", [])
        except (ValueError, OSError):
            old = []
    combined = clf.dedupe(old + new_records)

    if days:
        cutoff = dt.datetime.now(KST).date() - dt.timedelta(days=days)
    else:
        cutoff = dt.datetime.now(KST).date() - dt.timedelta(days=365 * (years or 3) + 1)
    def keep(r):
        try:
            return dt.datetime.fromisoformat(r.get("published", "")).date() >= cutoff
        except (ValueError, TypeError):
            return True
    combined = [r for r in combined if keep(r)]
    combined.sort(key=lambda r: r.get("published", ""), reverse=True)

    yrs = sorted({r["year"] for r in combined if r.get("year")})
    payload = {
        "updated_at": dt.datetime.now(KST).isoformat(),
        "total": len(combined),
        "year_min": yrs[0] if yrs else None,
        "year_max": yrs[-1] if yrs else None,
        "items": combined,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"누적 저장: {filename} ({len(combined)}건)")
    return payload


def write_day(date_str, records, cfg):
    """뉴스(국내+해외)만 날짜별로 저장."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{date_str}.json")
    records = merge_existing(path, records)

    order = {c: i for i, c in enumerate(cfg["category_order"])}
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
    print(f"뉴스 저장: {path} ({len(records)}건)")
    return payload


RESERVED = {"manifest.json", "papers.json", "trials.json", "news_recent.json", "regulatory.json"}

def _load_items(filename):
    p = os.path.join(DATA_DIR, filename)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except (ValueError, OSError):
        return []

def _aggregate(defs, key, all_items):
    """defs(파이프라인 or 회사)별로 layer 카운트 + 최신 published 집계."""
    out = []
    for d in defs:
        did = d["id"]
        sel = [it for it in all_items if did in (it.get(key) or [])]
        counts = {"news": 0, "paper": 0, "trial": 0}
        recent = ""
        for it in sel:
            counts[it.get("layer", "news")] = counts.get(it.get("layer", "news"), 0) + 1
            p = it.get("published", "")
            if p > recent:
                recent = p
        entry = {"id": did, "label": d.get("label", did),
                 "news": counts["news"], "papers": counts["paper"], "trials": counts["trial"],
                 "total": len(sel), "recent": recent}
        for k in ("desc", "area", "stage", "color", "logo", "chips"):
            if d.get(k):
                entry[k] = d[k]
        out.append(entry)
    return out

def rebuild_manifest(cfg):
    """날짜별 뉴스 파일 스캔 + 논문/임상 누적 요약 + 파이프라인/회사 집계."""
    keep = cfg.get("keep_days", 0)
    files = sorted(
        [f for f in os.listdir(DATA_DIR) if f.endswith(".json") and f not in RESERVED],
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
            entries.append({"date": d.get("date", fn[:-5]), "total": d.get("total", 0), "counts": d.get("counts", {})})
            valid.add(fn)
        except (ValueError, OSError):
            continue

    if keep and keep > 0:
        for fn in os.listdir(DATA_DIR):
            if fn.endswith(".json") and fn not in RESERVED and fn not in valid:
                try:
                    os.remove(os.path.join(DATA_DIR, fn))
                except OSError:
                    pass

    def layer_meta(filename):
        p = os.path.join(DATA_DIR, filename)
        if not os.path.exists(p):
            return {"total": 0, "year_min": None, "year_max": None}
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"total": d.get("total", 0), "year_min": d.get("year_min"), "year_max": d.get("year_max")}
        except (ValueError, OSError):
            return {"total": 0, "year_min": None, "year_max": None}

    # 교차 집계 대상: 최근 뉴스 + 논문 + 임상
    all_items = _load_items("news_recent.json") + _load_items("papers.json") + _load_items("trials.json")
    pipelines = _aggregate(cfg.get("pipelines", []), "pipelines", all_items)
    companies = _aggregate(cfg.get("companies", []), "companies", all_items)

    manifest = {
        "site_title": cfg.get("site_title", "PIR"),
        "site_subtitle": cfg.get("site_subtitle", ""),
        "site_tagline": cfg.get("site_tagline", ""),
        "greet_name": cfg.get("greet_name", ""),
        "category_meta": cfg.get("category_meta", {}),
        "category_order": cfg.get("category_order", []),
        "updated_at": dt.datetime.now(KST).isoformat(),
        "dates": entries,
        "papers": layer_meta("papers.json"),
        "trials": layer_meta("trials.json"),
        "news_recent_total": len(_load_items("news_recent.json")),
        "pipelines": pipelines,
        "companies": companies,
    }
    with open(os.path.join(DATA_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"manifest: 뉴스 {len(entries)}일 · 논문 {manifest['papers']['total']} · 임상 {manifest['trials']['total']} · 파이프라인 {len(pipelines)} · 회사 {len(companies)}")


def collect_regulatory(cfg):
    """규제 모니터링: 식약처 게시판 + FDA RSS 수집 후 의약품 관련만 필터.
    레코드 리스트 반환 (layer='regulatory', agency='MFDS'|'FDA')."""
    reg = cfg.get("regulatory")
    if not reg:
        return []
    inc = [w.lower() for w in reg.get("include_keywords", [])]
    exc = [w.lower() for w in reg.get("exclude_keywords", [])]

    def is_drug(text):
        t = (text or "").lower()
        if exc and any(w in t for w in exc):
            return False
        if inc and not any(w in t for w in inc):
            return False
        return True

    out = []
    # --- 식약처 게시판 ---
    for b in reg.get("mfds_boards", []):
        rows = sources.fetch_mfds_board(b["id"], b["label"], pages=1)
        kept = 0
        for r in rows:
            if not is_drug(r.get("title", "") + " " + r.get("summary", "")):
                continue
            r["layer"] = "regulatory"
            r["agency"] = "MFDS"
            r["board"] = b["label"]
            out.append(r)
            kept += 1
        print(f"    └ 의약품 필터 통과: {kept}/{len(rows)}")
    # --- FDA RSS ---
    for f in reg.get("fda_rss", []):
        rows = sources.fetch_rss(f.get("label", "FDA"), f["url"], limit=50)
        for r in rows:
            r["layer"] = "regulatory"
            r["agency"] = "FDA"
            r["board"] = f.get("label", "FDA Guidance")
            r["publisher"] = "FDA · " + r["board"]
            r["source"] = "fda"
            out.append(r)
        print(f"  [fda] {f.get('label')} {len(rows)}건")

    # 날짜/시간 표준화
    now_iso = dt.datetime.now(KST).isoformat()
    for r in out:
        if not r.get("published"):
            r["published"] = now_iso
        if not r.get("date"):
            r["date"] = r["published"][:10].replace("-", ".")
    return out


def main():
    cfg = load_config()
    items = collect_all(cfg)
    print(f"\n총 원시 수집: {len(items)}건")
    records = to_records(items, cfg)
    news = [r for r in records if r.get("layer") == "news"]
    papers = [r for r in records if r.get("layer") == "paper"]
    trials = [r for r in records if r.get("layer") == "trial"]

    date_str = day_key(records)
    write_day(date_str, news, cfg)
    accumulate_layer("news_recent.json", news, days=30)     # 교차 뷰용 최근 뉴스
    accumulate_layer("papers.json", papers, years=3)
    accumulate_layer("trials.json", trials, years=3)

    # 규제 모니터링 (식약처 + FDA)
    print("\n[규제 모니터링 수집]")
    try:
        reg = collect_regulatory(cfg)
        accumulate_layer("regulatory.json", reg, days=365)   # 최근 1년 누적
        print(f"규제 항목 누적: {len(reg)}건 신규")
    except Exception as e:
        print(f"규제 수집 실패(건너뜀): {e}")

    rebuild_manifest(cfg)
    print("\n완료.")


if __name__ == "__main__":
    main()
