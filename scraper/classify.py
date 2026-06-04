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


# 매체명 접미("제목 - 약업신문"), 머리 태그([속보][단독]), 꼬리(종합/속보) 제거 → 같은 사건 변형 통합
_TAG_HEAD = re.compile(r"^(\s*[\[\(（【][^\]\)）】]{1,14}[\]\)）】]\s*)+")
_TAG_TAIL = re.compile(r"\s*[\(\[（【]?\s*(종합\s*\d*\s*보?|속보|단독|영상|포토|인터뷰)\s*[\)\]）】]?\s*$")
_PUB_SUFFIX = re.compile(r"\s[-–—]\s[^-–—]{1,40}$")   # 구글뉴스 '제목 - 매체' (양옆 공백 있는 대시만)
_STOP = set("the a an of for in on to and or with at by from vs 및 the 위해 관련 대한".split())

def _clean_title(t: str) -> str:
    t = (t or "").strip()
    t = _PUB_SUFFIX.sub("", t)
    t = _TAG_HEAD.sub("", t)
    t = _TAG_TAIL.sub("", t)
    return t.strip()

def _sig_key(t: str):
    """제목의 의미있는 토큰 집합(정렬·중복제거). 토큰 4개 이상일 때만 근접중복 키로 사용."""
    t = _clean_title(t).lower()
    toks = re.findall(r"[0-9a-z]+|[가-힣]{2,}", t)
    toks = [w for w in toks if len(w) >= 2 and w not in _STOP]
    uniq = sorted(set(toks))
    return ("|".join(uniq), len(uniq))


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
    """defs = [{"id":..., "keywords":[...], "exclude":[...]}], 텍스트에 키워드가 있으면 그 id 반환.
    단, exclude 단어가 텍스트에 있으면 그 id는 제외(코드명 직접 매칭은 예외로 항상 인정)."""
    text = (text or "").lower()
    hits = []
    for d in defs:
        did = d["id"]
        excl = [w.lower() for w in d.get("exclude", [])]
        # 코드명(id 자체)이 본문에 있으면 제외어 무시하고 무조건 인정
        if did.lower() in text:
            hits.append(did)
            continue
        matched = False
        for kw in d.get("keywords", []):
            if kw.lower() in text:
                matched = True
                break
        if matched and excl and any(w in text for w in excl):
            matched = False
        if matched:
            hits.append(did)
    return hits


def dedupe(items):
    """중복 제거. 1차: 정제 제목/URL 일치, 2차: 의미 토큰 집합 일치(근접 중복).
    중복 시: DART 우선 -> origin 우선순위 높은 쪽 -> 요약 긴 쪽."""
    seen = {}        # 정제 제목(or URL) -> item
    sig_index = {}   # 토큰 시그니처 -> 정제 제목 key
    out_keys = []

    def better(a, b):
        """a가 b보다 우선이면 True."""
        if a.get("source") == "dart" and b.get("source") != "dart":
            return True
        if b.get("source") == "dart" and a.get("source") != "dart":
            return False
        pa = PRIORITY.get(a.get("origin_cat", "mkt"), 3)
        pb = PRIORITY.get(b.get("origin_cat", "mkt"), 3)
        if pa != pb:
            return pa < pb
        return len(a.get("summary", "")) > len(b.get("summary", ""))

    for it in items:
        key = _norm_title(_clean_title(it.get("title"))) or it.get("url", "")
        if not key:
            continue
        sig, ntok = _sig_key(it.get("title"))

        # 근접 중복: 동일 토큰 집합(4토큰 이상)이 이미 있으면 그 기존 항목으로 합침
        target = None
        if key in seen:
            target = key
        elif ntok >= 4 and sig in sig_index:
            target = sig_index[sig]

        if target is None:
            seen[key] = it
            out_keys.append(key)
            if ntok >= 4:
                sig_index.setdefault(sig, key)
        else:
            if better(it, seen[target]):
                seen[target] = it

    return [seen[k] for k in out_keys if k in seen]
