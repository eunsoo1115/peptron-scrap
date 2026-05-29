# Peptron Scrap Master

펩트론 관련 뉴스·공시를 **매일 자동으로 수집**해서 날짜별로 보여주는 사이트.
GitHub Actions가 정해진 시간에 알아서 돌고, GitHub Pages로 무료 배포된다. **내 PC를 켜둘 필요 없음.**

수집 소스: **네이버 뉴스 API · 구글 뉴스 RSS(국내+해외) · 해외 전문매체 RSS · PubMed(논문) · ClinicalTrials.gov(임상)**
화면 구조: **뉴스 / 논문 / 임상** 3개 레이어 → 각 레이어 안에서 카테고리 탭으로 필터
분류 (6개): 펩트론 직접 / 비만·GLP-1(PT403 라인) / NPR-B·연골무형성증(PND3174 라인) / 서방형 플랫폼·경쟁사 / 규제·식약처 / 산업·시장

---

## 폴더 구조

```
peptron-scrap-master/
├─ config.json                  ← 키워드·분류 설정 (여기만 고치면 됨)
├─ scraper/
│  ├─ collect.py                ← 메인 수집 스크립트
│  ├─ sources.py                ← 네이버 / 구글뉴스 / DART 연결
│  └─ classify.py               ← 분류 + 중복제거
├─ docs/                        ← GitHub Pages가 이 폴더를 사이트로 띄움
│  ├─ index.html                ← 사이트 화면
│  └─ data/                     ← 수집 결과 JSON (자동 생성·갱신)
│     └─ manifest.json
├─ .github/workflows/
│  └─ daily-scrape.yml          ← 매일 자동 실행 설정
└─ requirements.txt
```

---

## 준비물 — API 키 1종 (무료)

> 키는 **GitHub Secrets**에만 넣고, 코드/사이트에는 절대 적지 않는다. 외부에 노출되지 않음.

### 네이버 뉴스 검색 API
1. https://developers.naver.com/apps/#/register 접속 → 로그인
2. 애플리케이션 등록 → **검색** API 선택, 환경은 **WEB 설정** (URL은 아무거나, 예: `http://localhost`)
3. 발급된 **Client ID** 와 **Client Secret** 복사
   - 무료 한도: 하루 25,000건 호출 (이 도구는 하루 수십~수백건이라 여유 충분)

> DART 공시 수집은 기본 **꺼져 있음**(`config.json`의 `"dart": {"enabled": false}`).
> 자사 공시라 따로 모아 볼 필요가 없어 제외. 나중에 켜고 싶으면 `true`로 바꾸고 DART 인증키(opendart.fss.or.kr)를 `DART_API_KEY` Secret으로 추가하면 된다.

---

## 설치 — 5단계 (한 번만)

### ① GitHub 저장소 만들고 업로드
1. github.com 에서 새 저장소 생성 (예: `peptron-scrap`, **Public** 권장 — Pages 무료)
2. 이 폴더 전체를 그 저장소에 올린다 (웹에서 드래그 업로드 또는 git push)

### ② 키를 Secrets에 등록
저장소 → **Settings → Secrets and variables → Actions → New repository secret**
아래 3개를 각각 등록 (이름 정확히):

| 이름 | 값 |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 Client Secret |

### ③ GitHub Pages 켜기
저장소 → **Settings → Pages**
- Source: **Deploy from a branch**
- Branch: **main** / 폴더 **/docs** 선택 → Save
- 잠시 뒤 사이트 주소가 나온다: `https://<내아이디>.github.io/<저장소이름>/`

### ④ Actions 쓰기 권한 확인
저장소 → **Settings → Actions → General → Workflow permissions**
→ **Read and write permissions** 선택 → Save
(수집 결과를 저장소에 커밋하려면 필요)

### ⑤ 첫 실행
저장소 → **Actions 탭 → "Daily Scrap" → Run workflow** 클릭
- 1~2분 뒤 `docs/data/`에 오늘 날짜 JSON이 생기고, 사이트에 바로 반영된다.
- 이후부터는 **매일 07시·12시·18시(KST)** 자동 실행.

---

## 키워드 / 카테고리 수정

`config.json` 만 고치면 된다 (커밋하면 다음 실행부터 반영).

- `queries` : 카테고리별 **국내 검색어**(네이버·구글뉴스 한국어). 추가/삭제 자유.
- `en_queries` : 카테고리별 **해외 영문 검색어**(구글뉴스 영문). 글로벌 기사 수집용.
- `rss_feeds` : 해외 전문매체 RSS 목록 `{name, url}`. 피드를 더 넣거나 뺄 수 있다. (URL이 막히면 그 매체만 안 들어올 뿐 전체는 정상 — 새 URL로 교체하면 됨.)
- `rss_match_terms` : RSS는 매체의 *모든* 기사가 들어오므로, 여기 단어가 제목/요약에 있는 기사만 채택하고 나머지는 버린다. (= 해외매체 필터)
- `classify_keywords` : 분류 보정 키워드 (펩트론 신호 → 직접, RA 절차 신호 → 규제 승격).
- `pubmed` : 논문(PubMed) 검색. `enabled`, `recent_days`(최근 N일), `email`(선택), `queries`(카테고리별 영문 학술 검색어). **키 불필요.**
- `clinicaltrials` : 임상(ClinicalTrials.gov) 검색. `enabled`, `recent_days`, `queries`. **키 불필요.**
- `dart.enabled` : 자사 공시 수집 on/off (기본 off).
- `keep_days` : 보관 일수. `0`이면 영구 보관(기본값).

> 논문·임상 검색어는 뉴스와 달리 **타깃·기전·적응증**(GLP-1, GIP, amylin, NPR-B, achondroplasia 등) 중심으로 넣는다. 영문이며 결과도 영문으로 표시된다.

분류 규칙 요약:
1. 어느 검색어로 잡혔는지(검색 의도)가 기본 카테고리 — 예: `복스조고`로 잡히면 NPR-B 칸
2. 제목/요약에 펩트론·PT403 등이 있으면 → **펩트론 직접**으로 끌어올림
3. 가이드라인·가이던스·행정예고·고시 등 RA 절차 신호가 있으면 → **규제·식약처**로 승격
   (경쟁약 기사에 흔한 'FDA 승인·급여' 같은 단어로는 승격하지 않음 → 경쟁약은 자기 파이프라인 칸에 그대로 남음)

> **검색어 작성 팁:** 네이버 검색은 띄어쓰기가 AND 조건이다. `마운자로 위고비`로 쓰면 둘 다 들어간 기사만 잡히므로, **약물·회사 이름은 각각 한 줄씩** 따로 넣는다. 표기가 갈리는 건(터제파타이드/티르제파타이드, 세마글루타이드/세마글루티드) 둘 다 넣어 누락을 막는다.

---

## 실행 시간 바꾸기

`.github/workflows/daily-scrape.yml` 의 `cron` 수정. **UTC 기준**이라 한국시간 -9시간.
예) 매일 오전 8시 KST 한 번만 → `- cron: "0 23 * * *"` 한 줄만 남기기.

---

## 로컬에서 미리 보기 (선택)

사이트는 JSON을 `fetch`로 읽기 때문에 파일을 더블클릭하면 안 뜬다. 간단한 로컬 서버로 띄운다:

```bash
cd docs
python -m http.server 8000
# 브라우저에서 http://localhost:8000 접속
```

수집 스크립트를 로컬에서 직접 돌려보려면 (키를 환경변수로):

```bash
# macOS/Linux
export NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... DART_API_KEY=...
python scraper/collect.py
```

---

## 비용 / 한도 / 주의

- **비용 0원.** GitHub Actions(공개 저장소 무료), Pages(무료), 네이버·DART API(무료 한도 내).
- 기사 **전문은 저장하지 않는다.** 제목 + 짧은 요약 + 원문 링크만 보관 → 클릭 시 원문으로 이동. (저작권 안전)
- 네이버 API는 최신순 정렬이라 과거 기사 소급 수집은 제한적. 운영 시작일부터 날짜가 쌓인다.
- 데이터는 **영구 보관**(`keep_days: 0`)이라 과거 날짜로 계속 거슬러 볼 수 있다. 텍스트 JSON이라 1년에 약 10MB 수준 — 용량 걱정 없음. (원하면 `config.json`에서 보관 일수를 정할 수 있다.)
- 무단 크롤링이 아니라 **공식 API/RSS** 기반이라 차단 위험이 낮다.

---

내부 참고용 · Peptron RA
