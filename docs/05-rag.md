# 범용 RAG (L3 지식베이스)

"아무 PDF/텍스트나 넣으면 지식베이스가 되는" 검색 계층. 법령 아닌 문서(L3: ISMS-P 안내서,
전자금융기반시설 점검항목, 금융보안원 가이드 등)를 컬렉션 단위로 흡수한다.

## 설계
- **오프라인·무API 기본**: 문자 n-gram(2·3) TF-IDF 코사인. 한국어 토크나이저·임베딩 모델·API 키 불필요.
- **결정적 해시**: `zlib.crc32` (내장 `hash()`는 프로세스별 랜덤 시드라 사용 불가 — 이 함정 주의).
- **임베딩 교체 지점**: [tools/rag.py](../tools/rag.py) `embed()` 하나만 바꾸면 BGE-m3(sentence-transformers)나 API 임베딩으로 승격 가능. 인터페이스(희소벡터+cosine)는 동일.
- 인덱스는 컬렉션별 `data/kb/<name>/{chunks.jsonl, index.json}`. 원본·인덱스는 [.gitignore](../.gitignore)(대용량·재생성 가능).

## 사용
```bash
python3 -m pip install --user pypdf          # PDF 처리 시 1회
python tools/rag.py ingest <파일...> --collection isms   # 로컬 pdf/txt/md
python tools/rag.py search "접속기록 보관 기간" --collection isms -k 5
python tools/rag.py list
python tools/rag.py drop --collection isms
```
- HWP는 미지원 → 텍스트/PDF로 변환 후 투입.

## URL에서 바로 확보: kb_add ([tools/kb_add.py](../tools/kb_add.py))
웹에서 찾은 가이드/고시 PDF를 다운로드+인그레스트 한 번에. discover→ingest→answer 루프의 중간 단계.
```bash
python tools/kb_add.py <PDF_URL> --collection sec_measures --name 안내서.pdf
```
- 공식 도메인(law.go.kr·kisa.or.kr·fsec.or.kr·pipc.go.kr·data.go.kr 등) 화이트리스트, 그 외는 경고.
- PDF 시그니처 검사(HTML 오류페이지 감지). 저장은 `data/kb_src/`(.gitignore).
- **버전 주의**: 최신 개정 반영본을 넣을 것. (예: 안전성확보조치는 2025-10-31 시행 → 2025.11 안내서, 2024.10 아님)

### 실증: discover→ingest→answer (2026-07-16)
1. 웹검색 → 개인정보 안전성 확보조치 기준 안내서 **2025.11**(2025-10-31 시행 개정 반영) 발견
2. `kb_add` → 3.9MB PDF, **318 청크** 인그레스트(collection `sec_measures`)
3. `rag search` → 제5조 접근권한 관리(3년 보관), **제6조의2 인터넷망 차단(2025 신설)** 원문 정확 추출
→ crossmap의 ⚠️ 안전성확보조치 조문 갭을 원문으로 보강.

## 검증 (2026-07-15)
- 실제 **ISMS-P 인증기준 안내서 PDF(10MB)** 인그레스트: 441 청크 / 2.4초.
- 검색 품질:
  - "비밀번호 관리 작성규칙" → p.100 **"항목 2.5.4 비밀번호 관리 / 주요 확인사항"** 정확 검색(score 0.61).
  - "접속기록 보관 기간" → p.147 접속기록 항목 + 결함사례.
- → 하드코딩 못 하던 **항목별 주요 확인사항·결함사례**를 PDF 투입만으로 확보.

## legal-answer 연결
- **L3 근거**: `crossmap.py isms`(항목ID·명칭) + `rag.py search`(원문 상세, 출처·페이지 표기).
- **DIAGNOSIS(P4)**: 업로드 문서 → rag 검색으로 관련 인증기준·결함사례 매칭 → crossmap으로 통제/법령 연결 → gap.

## 확장 로드맵
- 임베딩 백엔드 승격(의미검색 강화) — `embed()` 교체.
- 컬렉션 예: `isms`(인증기준), `efin`(전자금융기반시설 점검항목), `fsec`(금융보안원 가이드).
- RAG 결과 스니펫을 crossmap 항목과 자동 링크.
