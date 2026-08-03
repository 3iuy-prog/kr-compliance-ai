# C. MCP 셋업 & 테스트

한국 법령 MCP(`chrisryugj/korean-law-mcp`)를 Claude Code에 연결한다.
환각 검증 내장 + 법령·행정규칙·판례 조회를 한 번에 제공.

## 사전 준비 — 법제처 API 키(OC) 발급 (사용자 작업)

MCP는 법제처 Open API 인증키(`OC`)가 필요하다. **직접 발급해야 함:**

1. [법제처 Open API](https://open.law.go.kr/LSO/openApi/guideList.do) 접속 → 회원가입/로그인
2. OpenAPI 활용 신청 → 발급된 **인증키(OC)** 확보
3. 키를 두 곳에 저장 (둘 다 gitignore 대상 — 커밋 금지):
   - [`.env`](../.env) — 셸에서 도구 직접 실행용 (`source .env`)
   - `.claude/settings.local.json`의 `env` 블록 — Claude Code가 MCP 기동 시 주입

   ```json
   { "env": { "LAW_OC": "발급받은_OC값" }, "permissions": { ... } }
   ```

   > `~/.zshrc` 등 전역 프로필에 넣지 말 것 — 무관한 프로젝트까지 노출된다.
   > `.env`만으로는 부족하다: Claude Code는 `.env`를 자동으로 읽지 않으므로
   > `${LAW_OC}` 치환이 안 되고 `Missing environment variables: LAW_OC` 경고가 뜬다.

## 연결 방법

### 방법 A — 로컬 stdio 실행 (기본, 이미 설정됨)

저장소 루트의 [`.mcp.json`](../.mcp.json)이 npm 패키지를 로컬에서 직접 실행한다.

```json
{
  "mcpServers": {
    "korean-law": {
      "command": "npx",
      "args": ["-y", "korean-law-mcp@latest"],
      "env": { "LAW_OC": "${LAW_OC}" }
    }
  }
}
```

- 항상 npm 최신판(`@latest`) 사용 — 호스팅판보다 릴리스 반영이 빠르다.
- 중계 서버를 거치지 않아 호스트 장애와 무관하게 동작한다.
- 확인: `claude mcp list` → `korean-law … ✔ Connected`

### 방법 B — 호스팅 원격 (비권장, 장애 이력)

```json
"korean-law": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://mcp.gomdori.app/law?oc=${LAW_OC}"]
}
```

⚠️ `mcp.gomdori.app`은 fly.io 머신 **1대**에 MCP 5종이 동거하는 구조라 통째로 내려가는
일이 있다(2026-08-03 전 엔드포인트 502/timeout 관측). claude.ai 웹 커넥터
(`https://mcp.gomdori.app/law?oc=<본인OC>`)도 같은 호스트를 쓴다.

## 제공 도구 (10개)

- `search_law`, `get_law_text`, `get_annexes`(별표) — 법령 + 행정규칙 별표
- `search_decisions`, `get_decision_text` — 판례/결정 통합검색
- `legal_research`(8 task), `legal_analysis`(4 mode) — 리서치·분석
- `ordinance_radar` — 조례
- `discover_tools`, `execute_tool` — 메타

⚠️ **출력의 링크 필드에 실제 OC 키가 그대로 찍힌다** (`…lawService.do?OC=<키>…`).
답변에 검색 결과를 인용할 때 링크를 복사하지 말 것 — [`/legal-answer`](../.claude/skills/legal-answer/SKILL.md)
스킬의 식별번호 노출 금지 규칙과 같은 맥락이다.

## 트러블슈팅 (2026-08-03 실측)

| 증상 | 원인 | 조치 |
|---|---|---|
| `Missing environment variables: LAW_OC` 경고 + 검색 결과 링크에 `OC=${LAW_OC}` 리터럴 | Claude Code 프로세스 환경에 `LAW_OC` 없음 (`.env`는 자동 로드되지 않음) | `.claude/settings.local.json`의 `env` 블록에 키 추가 후 재시작 |
| `get_decision_text` → `EXTERNAL_API_ERROR: precedent iframe did not include taxlaw redirect location (HTTP 200)` | 1차 JSON 경로 실패 시 taxlaw(국세) 리다이렉트 전제의 HTML 폴백으로 빠지는데, 비조세 판례는 리다이렉트가 없어 항상 실패. 방아쇠는 대개 무효한 OC | OC 유효성부터 확인(아래), 폴백 자체는 업스트림 버그 |
| `사용자 정보 검증에 실패하였습니다` (법제처 응답) | OC 값이 무효 | `curl "https://www.law.go.kr/DRF/lawService.do?OC=$LAW_OC&target=prec&ID=237875&type=JSON"` 으로 단독 검증 — `PrecService` JSON이 나와야 정상 |
| 원격(방법 B)만 전면 실패, `SocketError: other side closed` / 502 | 호스트 다운 | 방법 A(로컬 stdio)로 전환 |
| `claude mcp list`가 `✘ Failed to connect`인데 세션 내 도구는 동작 | 헬스체크가 매번 `npx`를 새로 띄우다 타임아웃 나는 오탐 | 실제 판정은 세션 내 도구 호출로 |

## 테스트 시나리오 (연결 후 실행)

`/legal-answer` 스킬 형식으로 답이 나오는지 검증한다.

### T1. 규정 Q&A (P1)
```
개인정보보호법상 개인정보 파기 의무의 근거 조항과 파기 기한은?
```
기대: `get_law_text`로 개인정보보호법 제21조 조회 → 조·항 원문 인용 + 검증 + 개정일.

### T2. 행정규칙(고시) 조회
```
개인정보의 안전성 확보조치 기준 고시에서 접근권한 관리 관련 조항을 원문으로 보여줘. 별표도 포함해서.
```
기대: 행정규칙 조회 + `get_annexes`로 별표 확보 → [행정규칙·고시] 태깅.

### T3. 크로스 매핑 (P2, 킬러)
```
'접근권한 최소화' 통제를 구현할 때 관련되는 개인정보보호법 조항, 안전성확보조치 고시 조항, ISMS-P 인증기준을 매핑 표로 정리해줘.
```
기대: 법령/고시는 MCP로 조회, ISMS-P는 인증기준 데이터 참조 → 크로스매핑 표.

### T4. 개정 영향 (P3)
```
안전성확보조치 기준 2025-10-31 개정에서 인터넷망 차단 관련 변경점과 대응 체크리스트를 정리해줘.
```
기대: 개정 전후 비교(legalize-kr Git diff 연동) → 변경 요약 + 체크리스트.

## 현재 상태 (2026-08-03)

- [x] `.mcp.json` — 로컬 stdio 방식(방법 A)으로 전환
- [x] `LAW_OC` 발급·등록 (`.env` + `.claude/settings.local.json`)
- [x] 연결 확인 — `claude mcp list` ✔ Connected, `korean-law-mcp@4.9.2`
- [x] 실쿼리 검증 3/3 — `get_law_text`(제29조) · `search_decisions` · `get_decision_text`(대법원 2022두68923 판례 본문)
