# P2 크로스매핑 · P3 개정영향 구현

두 킬러 기능을 우리 코드로 구현. legal-answer 스킬이 의도별로 호출.

## P2 — 크로스매핑 (통제 ↔ 규제)
"한 통제를 구현하면 어떤 법·기준을 동시에 충족하나."

- 데이터:
  - [data/crossmap.json](../data/crossmap.json) — ISMS-P ↔ 안전성확보조치(고시) ↔ 법령. 개인정보/접근통제 축 시드 8건.
  - [data/isms_p.json](../data/isms_p.json) — **L3 정형화**: ISMS-P 인증기준(도메인 3영역, 카테고리 2.1~2.12, 통제 시드 17). 항목ID·명칭은 권위출처(개인정보위/data.go.kr) 대조.
- 조회: [tools/crossmap.py](../tools/crossmap.py)
  ```bash
  python3 tools/crossmap.py list                       # 전체 매핑
  python3 tools/crossmap.py lookup 2.6.1               # ISMS-P 코드 조회
  python3 tools/crossmap.py search 암호화               # 키워드
  python3 tools/crossmap.py by-law 개인정보보호법 --jo 29  # 역매핑(이 조항에 걸리는 통제)
  python3 tools/crossmap.py isms 2.6                    # L3 ISMS-P 항목 조회
  python3 tools/crossmap.py validate                   # crossmap↔isms_p 정합성(코드 지어내기 방지)
  ```
- **L3 상세(주요 확인사항·결함사례)는 미포함** → 개인정보위 ISMS-P 인증기준 안내서 PDF 인그레스트 필요(P4 셀프진단 정확도용). 항목 목록·매핑에는 현재 시드로 충분.
- **검증 규약**: `law`의 `verified:true` 조항은 legalize.py로 원문 대조 완료(✅).
  `isms`/`notice`(고시·인증기준 조번호)는 버전별 변동 → 대조 전에는 `verified:false`(⚠️), 답변 시 "미검증" 표기 후 MCP 재확인.
  `notice`는 `_meta.notice_version`(현재 제2026-9호, 2026-07-01 시행) 기준으로 조번호·조문 제목을 법령 MCP 원문과 대조해 `verified:true`(✅)로 둔다. 고시가 개정되면 다시 대조한다.
- 검증된 법령 앵커(원문 대조 완료):
  - 개인정보보호법 제29조(안전조치의무) · 신용정보법 제19조(신용정보전산시스템의 안전보호)
  - 전자금융거래법 제21조(안전성의 확보의무) · 위치정보법 제16조(위치정보의 보호조치 등)
  - 정보통신망법 제45조(정보통신망의 안정성 확보 등)

## P3 — 개정영향 분석
개정 1건을 **조문별 신설/삭제/개정으로 구조화** → LLM이 요약·체크리스트로 가공.

- 엔진: [tools/legalize.py](../tools/legalize.py) `analyze` (Git blob 비교, 외부의존 없음)
  ```bash
  python3 tools/legalize.py history 개인정보보호법                  # 개정일 확인
  python3 tools/legalize.py analyze 개인정보보호법 --at 2023-03-14 --detail 5
  # → 신설 19 · 삭제 9 · 개정 52, 조문 목록 + 신설 조항 상세
  ```
- 파이프라인(스킬 AMENDMENT): `history` → `analyze` → crossmap 역매핑으로 영향 통제 도출 → 대응 체크리스트.

## legal-answer 스킬 연결
[skills/legal-answer/SKILL.md](../skills/legal-answer/SKILL.md) 의 "의도별 도구 호출":
- MAPPING → `crossmap.py`
- AMENDMENT → `legalize.py history/analyze`
- 법령 원문(로컬 대안) → `legalize.py show`

## 검증 상태 (2026-07-15)
- [x] P3 analyze: 개인정보보호법 2023-03-14 개정 구조화 확인
- [x] P2 crossmap: lookup/by-law/search 동작, 법령 앵커 5건 원문 대조
- [x] 스킬에 도구 호출 명시
- [ ] (다음) analyze/crossmap 출력을 LLM이 최종 리포트로 가공하는 실제 세션 시연
