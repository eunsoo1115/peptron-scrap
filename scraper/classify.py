"""분류 + 중복 제거.

분류 원칙:
1) 기사를 잡아온 '검색어의 카테고리'(origin_cat)를 기본값으로 쓴다 — 검색 의도가 가장 정확.
2) 단, 제목/요약에 펩트론 신호가 있으면 무조건 core로 끌어올린다 (경쟁사 검색에서 펩트론이 언급된 경우 등).
3) DART 공시는 무조건 core.
4) core가 아니면서 규제기관/규정 신호가 있으면 reg로 승격 (RA 관점에서 규제 건은 묻히면 안 됨).
"""

import re

PRIORITY = {"core": 0, "reg": 1, "obesity": 2, "npr": 2, "platform": 2, "comp": 2, "mkt": 3}


def _norm_title(t: str) -> str:
    t = (t or "").lower()
    return re.sub(r"[^0-9a-z가-힣]", "", t)


def classify(item, classify_keywords):
    if item.get("source") == "dart":
        return "core"

    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    origin = item.get("origin_cat", "mkt")

    # 2) 펩트론 신호 -> core
    for kw in classify_keywords.get("core", []):
        if kw.lower() in text:
            return "core"

    # 4) 규제 신호 -> reg 승격
    if origin != "reg":
        for kw in classify_keywords.get("reg", []):
            if kw.lower() in text:
                return "reg"

    return origin


def tag_by_defs(text, defs):
    """defs = [{"id":..., "keywords":[...]}], 텍스트에 키워드가 있으면 그 id 목록 반환 (복수 가능)."""
    text = (text or "").lower()
    hits = []
    for d in defs:
        for kw in d.get("keywords", []):
            if kw.lower() in text:
                hits.append(d["id"])
                break
    return hits


def dedupe(items):
    """제목(정규화)/URL 기준 중복 제거.
    중복 시: DART 우선 -> origin 우선순위 높은 쪽 -> 요약 긴 쪽."""
    seen = {}
    for it in items:
        key = _norm_title(it.get("title")) or it.get("url", "")
        if not key:
            continue
        if key not in seen:
            seen[key] = it
            continue
        old = seen[key]
        if it.get("source") == "dart" and old.get("source") != "dart":
            seen[key] = it
        elif PRIORITY.get(it.get("origin_cat", "mkt"), 3) < PRIORITY.get(old.get("origin_cat", "mkt"), 3):
            seen[key] = it
        elif len(it.get("summary", "")) > len(old.get("summary", "")):
            seen[key] = it
    return list(seen.values())
