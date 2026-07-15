# 법률/컴플라이언스 Claude Code 기반 AI Agent

법령 기반 금융보안·개인정보 **컴플라이언스 어시스턴트**. 소송/판례 검색기가 아니라 규제 준수 지원 도구다.

## 프로젝트 문서
- [docs/00-data-sources.md](docs/00-data-sources.md) — 데이터 3계층(L1 법령 / L2 행정규칙 / L3 인증·점검기준)과 수집 갭
- [docs/01-architecture.md](docs/01-architecture.md) — 시스템 구성, 4대 파이프라인, 기술스택
- [docs/02-mcp-setup.md](docs/02-mcp-setup.md) — 한국 법령 MCP 연결·테스트 절차
- [docs/03-legalize-integration.md](docs/03-legalize-integration.md) — legalize-kr 연동(개정이력·diff), 헬퍼 [tools/legalize.py](tools/legalize.py)
- [docs/04-p2-p3-implementation.md](docs/04-p2-p3-implementation.md) — P2 크로스매핑([tools/crossmap.py](tools/crossmap.py)) · P3 개정영향(analyze)
- [docs/05-rag.md](docs/05-rag.md) — 범용 RAG(아무 PDF→지식베이스), 헬퍼 [tools/rag.py](tools/rag.py)

## 대상 법률
전자금융거래법(+시행령/규칙/세칙) · 정보통신망법(+시행령) · 신용정보법 · 개인정보보호법(+시행령+안전성확보조치) · 위치정보법 · 전자금융기반시설/주요정보통신기반시설 점검기준 · ISMS-P/ISO27001

## 법령 기반
- 법령 원문: [legalize-kr](https://github.com/legalize-kr/legalize-kr) (Markdown + Git 개정이력)
- 실시간 조회: 한국 법령 MCP (`korean-law-mcp`)

---

## ⚠️ 출력 규약 (MANDATORY)

이 저장소에서 **법률/규제 질의에 답할 때는 반드시 아래 형식과 규칙을 따른다.** 표준화된 답변은 `/legal-answer` 스킬 참고.

### 절대 규칙
1. **근거 없는 단정 금지.** 모든 규범적 결론에는 조·항·호 단위 원문 인용과 출처를 붙인다. 확인 못 하면 "미확인"이라고 명시한다.
2. **조문을 지어내지 않는다.** 조문 번호·문구는 MCP/legalize-kr로 조회한 실제 원문만 사용. 기억에 의존한 인용 금지.
3. **개정일 명시.** 인용한 법령/고시의 최종 개정·시행일을 표기한다(개정이 잦음).
4. **법률 자문 아님.** 답변 끝에 참고용 고지를 붙인다.
5. **계층 구분.** 법령(L1)·행정규칙/고시(L2)·인증기준/점검항목(L3)을 명확히 구분해 인용한다.

### 표준 답변 골격
```
## 결론
(핵심 1~3문장)

## 근거
- **[법령명 제N조 제M항 제K호]** "원문 인용…" — (출처)
- **[고시명 제N조]** … (L2면 행정규칙임을 표시)

## 크로스 매핑        ← 통제/인증기준 관련 질의일 때만
| 인증기준(ISMS-P) | 고시(안전성확보조치) | 법령 | 비고 |
|---|---|---|---|

## 검증
- 인용 조항 원문 대조: ✅ 완료 / ⚠️ 미검증
- 기준 개정·시행일: YYYY-MM-DD

## 유의
본 답변은 참고용이며 법률 자문이 아닙니다. 실제 적용 전 최신 개정본과 소관기관 유권해석을 확인하세요.
```

## 코드/문서 작성 원칙
- 문서는 한국어. 파일 참조는 마크다운 링크로.
- 신규 조사 결과는 `docs/`에 축적하고 이 파일 목차에 한 줄 추가.
