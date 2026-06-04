"""
데이터 수집 소스 모듈.
- 네이버 뉴스 검색 API (NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 필요)
- 구글 뉴스 RSS (키 불필요)
- DART OpenDART 공시 (DART_API_KEY 필요)

각 함수는 공통 스키마의 dict 리스트를 반환한다:
  { "title", "summary", "url", "publisher", "source", "published" (ISO8601 KST) }
키가 없거나 호출이 실패하면 빈 리스트를 반환하고 경고만 출력 (전체 파이프라인은 계속 진행).
"""

import os
import re
import html
import time
import datetime as dt
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

KST = dt.timezone(dt.timedelta(hours=9))
UA = "PeptronScrapMaster/1.0 (+https://github.com)"


def _clean(text: str) -> str:
    """HTML 태그/엔티티 제거."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _get(url, headers=None, timeout=20):
    req = Request(url, headers=headers or {"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _publisher_from_url(url: str) -> str:
    """기사 URL의 도메인에서 대략적인 언론사명을 추정."""
    if not url:
        return ""
    m = re.search(r"https?://(?:www\.|news\.|m\.)?([^/]+)", url)
    if not m:
        return ""
    host = m.group(1)
    known = {
        "dailypharm.com": "데일리팜", "yakup.com": "약업신문",
        "hitnews.co.kr": "히트뉴스", "bosa.co.kr": "의학신문",
        "monews.co.kr": "메디칼옵저버", "docdocdoc.co.kr": "청년의사",
        "pharmnews.com": "팜뉴스", "medipana.com": "메디파나뉴스",
        "biospectator.com": "바이오스펙테이터", "newsthevoice.com": "더보이스",
        "mt.co.kr": "머니투데이", "hankyung.com": "한국경제",
        "edaily.co.kr": "이데일리", "mk.co.kr": "매일경제",
    }
    for dom, name in known.items():
        if host.endswith(dom):
            return name
    return host


# ---------------------------------------------------------------- 네이버 뉴스
def fetch_naver(query, display=30):
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not csec:
        print("  [naver] 키 없음 — 건너뜀")
        return []

    url = (
        "https://openapi.naver.com/v1/search/news.json"
        f"?query={quote(query)}&display={display}&sort=date"
    )
    headers = {
        "X-Naver-Client-Id": cid,
        "X-Naver-Client-Secret": csec,
        "User-Agent": UA,
    }
    try:
        import json
        raw = _get(url, headers=headers)
        data = json.loads(raw.decode("utf-8"))
    except (URLError, HTTPError, ValueError) as e:
        print(f"  [naver] '{query}' 실패: {e}")
        return []

    out = []
    for it in data.get("items", []):
        # 네이버 pubDate: 'Mon, 26 May 2026 09:30:00 +0900'
        published = None
        try:
            published = dt.datetime.strptime(
                it.get("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z"
            ).astimezone(KST).isoformat()
        except ValueError:
            published = dt.datetime.now(KST).isoformat()

        link = it.get("originallink") or it.get("link") or ""
        publisher = _publisher_from_url(link)
        out.append({
            "title": _clean(it.get("title")),
            "summary": _clean(it.get("description")),
            "url": link,
            "publisher": publisher,
            "source": "naver",
            "published": published,
        })
    return out


# ----------------------------------------------------------- 구글 뉴스 RSS
def fetch_gnews(query, display=30, lang="ko"):
    if lang == "en":
        url = (
            "https://news.google.com/rss/search"
            f"?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
        )
    else:
        url = (
            "https://news.google.com/rss/search"
            f"?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
        )
    try:
        raw = _get(url)
        root = ET.fromstring(raw)
    except (URLError, HTTPError, ET.ParseError) as e:
        print(f"  [gnews-{lang}] '{query}' 실패: {e}")
        return []

    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        publisher = (src_el.text.strip() if src_el is not None and src_el.text else "")

        # 구글뉴스 제목은 보통 '제목 - 언론사' 형태 → 언론사 분리
        if not publisher and " - " in title:
            publisher = title.rsplit(" - ", 1)[-1].strip()
        if " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()

        published = None
        try:
            published = dt.datetime.strptime(
                pub, "%a, %d %b %Y %H:%M:%S %Z"
            ).replace(tzinfo=dt.timezone.utc).astimezone(KST).isoformat()
        except ValueError:
            published = dt.datetime.now(KST).isoformat()

        out.append({
            "title": _clean(title),
            "summary": "",  # 구글뉴스 RSS는 요약이 빈약해 비워둠
            "url": link,
            "publisher": publisher or "Google News",
            "source": "gnews",
            "published": published,
        })
        if len(out) >= display:
            break
    return out


# --------------------------------------------------- 해외 전문매체 RSS/Atom
def fetch_rss(name, url, limit=50):
    """전문매체 RSS/Atom 피드 전체 기사 목록을 가져온다.
    (키워드 필터링은 collect.py에서 수행 — 여기선 원시 목록만 반환)"""
    try:
        raw = _get(url, timeout=30)
        root = ET.fromstring(raw)
    except (URLError, HTTPError, ET.ParseError) as e:
        print(f"  [rss] '{name}' 실패: {e}")
        return []

    def _tag(el):
        return el.tag.split("}")[-1]  # 네임스페이스 제거

    out = []
    # RSS(item) / Atom(entry) 모두 지원
    nodes = [el for el in root.iter() if _tag(el) in ("item", "entry")]
    for node in nodes:
        title = link = desc = pub = ""
        for ch in node:
            t = _tag(ch)
            if t == "title":
                title = (ch.text or "").strip()
            elif t == "link":
                # RSS는 text, Atom은 href 속성
                link = (ch.text or ch.attrib.get("href", "")).strip()
            elif t in ("description", "summary", "content"):
                if not desc:
                    desc = (ch.text or "").strip()
            elif t in ("pubDate", "published", "updated"):
                if not pub:
                    pub = (ch.text or "").strip()

        published = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                dtobj = dt.datetime.strptime(pub, fmt)
                if dtobj.tzinfo is None:
                    dtobj = dtobj.replace(tzinfo=dt.timezone.utc)
                published = dtobj.astimezone(KST).isoformat()
                break
            except ValueError:
                continue
        if not published:
            try:  # Atom ISO8601
                published = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(KST).isoformat()
            except ValueError:
                published = dt.datetime.now(KST).isoformat()

        if title and link:
            out.append({
                "title": _clean(title),
                "summary": _clean(desc)[:300],
                "url": link,
                "publisher": name,
                "source": "rss",
                "published": published,
            })
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------- PubMed (논문)
def fetch_pubmed(query, recent_days=30, retmax=20, email=""):
    import json
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    common = "&tool=peptron-scrap" + (f"&email={quote(email)}" if email else "")
    es = (base + "esearch.fcgi?db=pubmed"
          f"&term={quote(query)}&retmax={retmax}&sort=date"
          f"&datetype=pdat&reldate={recent_days}&retmode=json" + common)
    try:
        data = json.loads(_get(es).decode("utf-8"))
        ids = data.get("esearchresult", {}).get("idlist", [])
    except (URLError, HTTPError, ValueError) as e:
        print(f"  [pubmed] '{query}' esearch 실패: {e}")
        return []
    if not ids:
        return []

    su = base + f"esummary.fcgi?db=pubmed&id={','.join(ids)}&retmode=json" + common
    try:
        sdata = json.loads(_get(su).decode("utf-8"))
        res = sdata.get("result", {})
    except (URLError, HTTPError, ValueError) as e:
        print(f"  [pubmed] '{query}' esummary 실패: {e}")
        return []

    out = []
    for pmid in res.get("uids", []):
        r = res.get(pmid, {})
        title = (r.get("title") or "").strip().rstrip(".")
        journal = (r.get("fulljournalname") or r.get("source") or "").strip()
        authors = r.get("authors", [])
        first = authors[0].get("name", "") if authors else ""
        etal = " et al." if len(authors) > 1 else ""
        pub = (r.get("sortpubdate") or r.get("pubdate") or "").strip()
        published = dt.datetime.now(KST).isoformat()
        for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y %b %d", "%Y %b", "%Y"):
            try:
                published = dt.datetime.strptime(pub, fmt).replace(tzinfo=KST).isoformat()
                break
            except ValueError:
                continue
        out.append({
            "title": _clean(title),
            "summary": _clean((first + etal + (" · " if first else "") + journal)),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "publisher": journal[:45],
            "source": "pubmed",
            "layer": "paper",
            "published": published,
        })
    return out


# ----------------------------------------------- ClinicalTrials.gov (임상)
_CT_STATUS = {
    "RECRUITING": "모집중", "NOT_YET_RECRUITING": "모집예정",
    "ACTIVE_NOT_RECRUITING": "진행중(모집종료)", "COMPLETED": "완료",
    "TERMINATED": "중단", "SUSPENDED": "일시중지", "WITHDRAWN": "철회",
    "ENROLLING_BY_INVITATION": "초청모집", "UNKNOWN": "미상",
}
_CT_PHASE = {"EARLY_PHASE1": "초기1상", "PHASE1": "1상", "PHASE2": "2상",
             "PHASE3": "3상", "PHASE4": "4상", "NA": "해당없음"}

def fetch_clinicaltrials(query, recent_days=60, pagesize=20):
    import json
    url = ("https://clinicaltrials.gov/api/v2/studies"
           f"?query.term={quote(query)}&pageSize={pagesize}"
           "&sort=LastUpdatePostDate:desc&countTotal=false")
    try:
        data = json.loads(_get(url, timeout=30).decode("utf-8"))
    except (URLError, HTTPError, ValueError) as e:
        print(f"  [ctgov] '{query}' 실패: {e}")
        return []

    today = dt.datetime.now(KST).date()
    cutoff = today - dt.timedelta(days=recent_days)
    out = []
    for st in data.get("studies", []):
        ps = st.get("protocolSection", {})
        idm = ps.get("identificationModule", {})
        nct = idm.get("nctId", "")
        title = (idm.get("briefTitle") or "").strip()
        sm = ps.get("statusModule", {})
        status = _CT_STATUS.get(sm.get("overallStatus", ""), sm.get("overallStatus", ""))
        lup = sm.get("lastUpdatePostDateStruct", {}).get("date", "")  # 'YYYY-MM-DD' or 'YYYY-MM'
        ddate = None
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                ddate = dt.datetime.strptime(lup, fmt).date()
                break
            except ValueError:
                continue
        if ddate and ddate < cutoff:
            continue  # 최근 업데이트만
        phases = ps.get("designModule", {}).get("phases", [])
        phase = ", ".join(_CT_PHASE.get(p, p) for p in phases) if phases else "단계미정"
        sponsor = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
        conds = ps.get("conditionsModule", {}).get("conditions", [])
        cond = ", ".join(conds[:2])
        published = dt.datetime.now(KST).isoformat()
        if ddate:
            published = dt.datetime(ddate.year, ddate.month, ddate.day, tzinfo=KST).isoformat()
        if not (title and nct):
            continue
        out.append({
            "title": _clean(title),
            "summary": _clean(f"{status} · {phase}" + (f" · {cond}" if cond else "")),
            "url": f"https://clinicaltrials.gov/study/{nct}",
            "publisher": (sponsor or "ClinicalTrials.gov")[:45],
            "source": "ctgov",
            "layer": "trial",
            "published": published,
            "ct_status": status,
            "ct_phase": phase,
        })
    return out


# ------------------------------------------------------------------- DART
_DART_CORP_CACHE = {}

def _resolve_dart_corp_code(api_key, corp_name, stock_code=None):
    """corpCode.zip을 1회 내려받아 회사명(or 종목코드)으로 corp_code(8자리)를 찾는다."""
    if corp_name in _DART_CORP_CACHE:
        return _DART_CORP_CACHE[corp_name]
    import io, zipfile
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={api_key}"
    try:
        raw = _get(url, timeout=60)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        xml_bytes = zf.read(zf.namelist()[0])
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        print(f"  [dart] corp_code 조회 실패: {e}")
        return None

    found = None
    for el in root.iter("list"):
        name = (el.findtext("corp_name") or "").strip()
        scode = (el.findtext("stock_code") or "").strip()
        if stock_code and scode == str(stock_code):
            found = (el.findtext("corp_code") or "").strip()
            break
        if name == corp_name and scode:  # 상장사 우선
            found = (el.findtext("corp_code") or "").strip()
    _DART_CORP_CACHE[corp_name] = found
    return found


def fetch_dart(corp_name, stock_code=None, days_back=3):
    api_key = os.environ.get("DART_API_KEY")
    if not api_key:
        print("  [dart] 키 없음 — 건너뜀")
        return []

    corp_code = _resolve_dart_corp_code(api_key, corp_name, stock_code)
    if not corp_code:
        print(f"  [dart] '{corp_name}' corp_code 못 찾음 — 건너뜀")
        return []

    import json
    today = dt.datetime.now(KST).date()
    bgn = (today - dt.timedelta(days=days_back)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    url = (
        "https://opendart.fss.or.kr/api/list.json"
        f"?crtfc_key={api_key}&corp_code={corp_code}"
        f"&bgn_de={bgn}&end_de={end}&page_count=100"
    )
    try:
        raw = _get(url)
        data = json.loads(raw.decode("utf-8"))
    except (URLError, HTTPError, ValueError) as e:
        print(f"  [dart] 공시 조회 실패: {e}")
        return []

    if data.get("status") not in ("000", "013"):  # 013 = 데이터 없음
        print(f"  [dart] 응답 코드 {data.get('status')}: {data.get('message')}")
    out = []
    for it in data.get("list", []):
        rcept = it.get("rcept_no", "")
        rdt = it.get("rcept_dt", "")
        try:
            published = dt.datetime.strptime(rdt, "%Y%m%d").replace(tzinfo=KST).isoformat()
        except ValueError:
            published = dt.datetime.now(KST).isoformat()
        out.append({
            "title": it.get("report_nm", "").strip(),
            "summary": f"제출인: {it.get('flr_nm','')} · 접수일 {rdt}",
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
            "publisher": "전자공시(DART)",
            "source": "dart",
            "published": published,
        })
    return out

# ============ 규제 모니터링: 식약처(MFDS) 게시판 ============
# 게시판은 mfds.go.kr/brd/m_XX/list.do 구조. HTML을 파싱해 제목/등록번호/날짜/링크 추출.
# 식약처가 봇 차단/불안정할 수 있어 브라우저형 헤더 사용. 실패 시 빈 리스트 반환(전체 수집은 계속).
_MFDS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def fetch_mfds_board(board_id, board_label, base="https://www.mfds.go.kr", pages=1, timeout=25):
    """식약처 게시판 목록을 긁어 레코드 리스트 반환.
    board_id 예: 'm_1060'(민원인안내서), 'm_74'(공지), 'm_99'(보도자료)."""
    out = []
    for page in range(1, pages + 1):
        url = f"{base}/brd/{board_id}/list.do?page={page}"
        try:
            raw = _get(url, headers={
                "User-Agent": _MFDS_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer": base,
            }, timeout=timeout)
            htmltext = raw.decode("utf-8", "replace")
        except (URLError, HTTPError, Exception) as e:
            print(f"  [mfds:{board_id}] 실패: {e}")
            break

        rows = _parse_mfds_list(htmltext, board_id, base)
        if not rows:
            # 구조가 안 맞거나 차단된 경우 — 한 번만 경고
            if page == 1:
                print(f"  [mfds:{board_id}] 목록 파싱 0건 (구조 변경/차단 가능)")
            break
        for r in rows:
            r["publisher"] = "식약처 · " + board_label
            r["source"] = "mfds"
            r["reg_agency"] = "MFDS"
            r["reg_board"] = board_label
            out.append(r)
        time.sleep(0.5)
    print(f"  [mfds:{board_id}] {board_label} {len(out)}건")
    return out


def _parse_mfds_list(htmltext, board_id, base):
    """게시판 목록 HTML에서 항목 추출. 여러 패턴을 시도(테이블/리스트형 모두 대응)."""
    items = []
    # 식약처 list.do는 보통 <a href="view.do?...seq=NNN">제목</a> 형태.
    # view 링크 + 제목을 뽑고, 주변 텍스트에서 날짜(YYYY.MM.DD 또는 YYYY-MM-DD)와 등록번호를 찾는다.
    # 1) view 링크 블록 단위로 분리
    link_re = re.compile(
        r'href="([^"]*view\.do[^"]*?(?:seq|nttId|board_seq)=\d+[^"]*)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL)
    date_re = re.compile(r'(20\d{2}[.\-]\d{1,2}[.\-]\d{1,2})')
    regno_re = re.compile(r'(안내서\s*-?\s*\d+[\-\d]*|지침서\s*-?\s*\d+[\-\d]*)')

    for m in link_re.finditer(htmltext):
        href = m.group(1)
        title = _clean(m.group(2))
        if not title or len(title) < 4:
            continue
        # 절대 URL
        if href.startswith("http"):
            link = href
        elif href.startswith("/"):
            link = base + href
        else:
            link = f"{base}/brd/{board_id}/" + href.lstrip("./")
        link = link.replace("&amp;", "&")
        # 제목 뒤쪽 일정 구간에서 날짜/등록번호 탐색
        tail = htmltext[m.end():m.end() + 600]
        dm = date_re.search(tail)
        date_str = dm.group(1).replace("-", ".") if dm else ""
        rm = regno_re.search(tail)
        regno = re.sub(r"\s+", "", rm.group(1)) if rm else ""
        # 중복 제목 스킵
        if any(it["title"] == title for it in items):
            continue
        items.append({
            "title": title,
            "summary": (("등록번호 " + regno) if regno else "") + ((" · " + date_str) if date_str else ""),
            "url": link,
            "date": date_str,
            "regno": regno,
        })
        if len(items) >= 30:
            break
    return items
