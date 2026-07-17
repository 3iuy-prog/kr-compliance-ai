# legalize-kr 연동

법령 원문 + **Git 개정이력**을 로컬에서 활용하기 위한 연동. 개정영향 분석(P3)의 데이터·엔진 기반.

## 역할 분담 (MCP vs legalize-kr)
| 기능 | 담당 | 이유 |
|---|---|---|
| 실시간 법령/행정규칙 원문·조문 조회 | **korean-law MCP** | 최신 반영, 별표/서식, 판례까지 |
| 조문 개정이력·개정 diff (P3) | **legalize-kr** | Git commit = 개정 1건 구조, diff 즉시 |

## 설치
```bash
git clone https://github.com/legalize-kr/legalize-kr vendor/legalize-kr   # 약 677MB (전체 히스토리)
```
`vendor/legalize-kr/`는 [.gitignore](../.gitignore)로 제외(용량). 각자 clone.

## 저장소 구조
- 경로: `kr/{법령명}/{구분}.md` (법령명은 공백 제거, 예: `정보통신망이용촉진및정보보호등에관한법률`)
- 구분: `법률.md` / `시행령.md` / `시행규칙.md` (시행세칙·고시는 없음 → MCP 담당)
- Frontmatter(YAML): 제목·법령MST·법령ID·소관부처·공포일자·시행일자·상태·출처
- 본문: `##### 제N조 (제목)`, 조항 `**①**`, 개정마커 `<개정 YYYY.M.D>`
- 커밋: 1개 = 개정 1건, 커밋 날짜 = 공포일자, 메시지 = `법률: OOO (일부개정)`

## 대상 5법 매핑 (별칭 → 디렉토리)
| 별칭 | 디렉토리 | 보유 구분 |
|---|---|---|
| 전자금융거래법 | 전자금융거래법 | 법률, 시행령 |
| 정보통신망법 | 정보통신망이용촉진및정보보호등에관한법률 | 법률, 시행령, 시행규칙 |
| 신용정보법 | 신용정보의이용및보호에관한법률 | 법률, 시행령, 시행규칙 |
| 개인정보보호법 | 개인정보보호법 | 법률, 시행령 |
| 위치정보법 | 위치정보의보호및이용등에관한법률 | 법률, 시행령 |

## 헬퍼 CLI — [tools/legalize.py](../tools/legalize.py)
외부 의존 없음(Python stdlib + git).

```bash
python3 tools/legalize.py list                          # 대상 법률 + 시행일/상태
python3 tools/legalize.py show 개인정보보호법 --jo 21     # 특정 조문 원문 추출
python3 tools/legalize.py show 전자금융거래법 --kind 시행령
python3 tools/legalize.py history 개인정보보호법          # 개정이력(커밋=개정)
python3 tools/legalize.py diff 개인정보보호법 --at 2023-03-14   # 해당 개정 변경분
python3 tools/legalize.py diff 개인정보보호법             # 최근 두 개정 비교
```

기능: `list`(대상 목록) · `show`(법령/조문, frontmatter 시행일 포함) · `history`(개정 목록) · `diff`(개정 변경분).

## 검증 상태 (2026-07-15)
- [x] clone 완료 (677MB, 102,549 커밋)
- [x] 대상 5법 전부 존재 확인
- [x] `list` / `show --jo` / `history` / `diff --at` 동작 확인 (개인정보보호법 제21조, 2023-03-14 개정 diff)

## 다음 연동 (선택)
- diff 결과 → LLM 요약 + 영향 통제항목 역매핑(P3 완성)
- MCP 최신본과 legalize-kr 시행일 대조 → 개정 알림
