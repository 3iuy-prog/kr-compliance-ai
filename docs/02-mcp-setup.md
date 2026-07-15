# C. MCP 셋업 & 테스트

한국 법령 MCP(`chrisryugj/korean-law-mcp`)를 Claude Code에 연결한다.
환각 검증 내장 + 법령·행정규칙·판례 조회를 한 번에 제공.

## 사전 준비 — 법제처 API 키(OC) 발급 (사용자 작업)

MCP는 법제처 Open API 인증키(`OC`)가 필요하다. **직접 발급해야 함:**

1. [법제처 Open API](https://open.law.go.kr/LSO/openApi/guideList.do) 접속 → 회원가입/로그인
2. OpenAPI 활용 신청 → 발급된 **인증키(OC)** 확보 (보통 이메일 아이디 형태)
3. 아래처럼 환경변수로 등록:
   ```bash
   export LAW_OC="발급받은_OC값"     # 예: honggildong
   # 영구 적용: ~/.zshrc 에 추가
   echo 'export LAW_OC="발급받은_OC값"' >> ~/.zshrc && source ~/.zshrc
   ```

## 연결 방법

### 방법 A — 프로젝트 스코프 (이미 설정됨)
저장소 루트의 [`.mcp.json`](../.mcp.json)이 이미 구성되어 있다. `LAW_OC` 환경변수만 있으면 이 프로젝트에서 Claude Code가 자동으로 MCP를 로드한다.

```json
{
  "mcpServers": {
    "korean-law": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.gomdori.app/law?oc=${LAW_OC}"]
    }
  }
}
```

확인:
```bash
claude mcp list          # korean-law 가 연결(✓)로 보이는지
```

### 방법 B — 플러그인(가장 간단, 대화형 세션에서)
Claude Code 대화창에서:
```
/plugin marketplace add chrisryugj/korean-law-mcp
/plugin install korean-law@korean-law-marketplace
```
설치 중 API 키 입력 프롬프트가 뜬다.

### 방법 C — 원격 커넥터(설치 없음, claude.ai 웹)
커스텀 커넥터 URL: `https://mcp.gomdori.app/law?oc=<본인OC>`

## 제공 도구 (10개)
- `search_law`, `get_law_text`, `get_annexes`(별표) — L1 법령 + L2 행정규칙 별표
- `search_decisions`, `get_decision_text` — 판례/결정 통합검색
- `legal_research`(8 task), `legal_analysis`(4 mode) — 리서치·분석
- `ordinance_radar` — 조례
- `discover_tools`, `execute_tool` — 메타

## 테스트 시나리오 (연결 후 실행)

`/legal-answer` 스킬 형식으로 답이 나오는지 검증한다.

### T1. 규정 Q&A (P1)
```
개인정보보호법상 개인정보 파기 의무의 근거 조항과 파기 기한은?
```
기대: `get_law_text`로 개인정보보호법 제21조 조회 → 조·항 원문 인용 + 검증 + 개정일.

### T2. 행정규칙(고시) 조회 (L2 확인)
```
개인정보의 안전성 확보조치 기준 고시에서 접근권한 관리 관련 조항을 원문으로 보여줘. 별표도 포함해서.
```
기대: 행정규칙 조회 + `get_annexes`로 별표 확보 → L2 태깅.

### T3. 크로스 매핑 (P2, 킬러)
```
'접근권한 최소화' 통제를 구현할 때 관련되는 개인정보보호법 조항, 안전성확보조치 고시 조항, ISMS-P 인증기준을 매핑 표로 정리해줘.
```
기대: 법령/고시는 MCP로 조회, ISMS-P는 L3 데이터 참조 → 크로스매핑 표.

### T4. 개정 영향 (P3)
```
안전성확보조치 기준 2025-10-31 개정에서 인터넷망 차단 관련 변경점과 대응 체크리스트를 정리해줘.
```
기대: 개정 전후 비교(향후 legalize-kr Git diff 연동) → 변경 요약 + 체크리스트.

## 현재 상태
- [x] `.mcp.json` 프로젝트 설정 생성
- [x] 런타임 확인 (node v26, npx, claude CLI 2.1.181)
- [ ] **`LAW_OC` 발급·등록 (사용자 작업, 미완)** ← 이것만 하면 라이브 테스트 가능
- [ ] `claude mcp list`로 연결 확인 → T1~T4 실행

> 라이브 조회는 API 키 발급 후 가능. 키 등록 즉시 T1부터 돌려보면 된다.
