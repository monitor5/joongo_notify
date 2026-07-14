# joongo_notify — 중고 매물 조건 매칭 알리미

원하는 **중고 제품**과 **제품 상태 조건**(예: 전자제품의 번인, 기스, 배터리 효율 등)을 등록하면,
**당근마켓(지역 기반) · 중고나라 · 번개장터**를 주기적으로 수집·분석하여
조건에 맞는 매물을 찾아 알림을 보내주는 서비스.

## 핵심 아이디어

```
사용자 입력                          수집                        분석                       결과
┌──────────────┐    ┌──────────────────────┐    ┌──────────────────────────┐    ┌──────────┐
│ 제품 (모델명)  │    │ 당근마켓 (지역 기반)     │    │ 1차: 검색엔진 (키워드/모델) │    │ 매칭 점수  │
│ 상태 조건      │ →  │ 중고나라               │ →  │ 2차: 경량 로컬 LLM (본문)   │ →  │ 순위/필터  │
│ 지역/가격 범위  │    │ 번개장터               │    │ 3차: VL 모델 (사진 검증)    │    │ 알림 발송  │
└──────────────┘    └──────────────────────┘    └──────────────────────────┘    └──────────┘
```

- **검색엔진**: 모델명·키워드로 후보 매물을 빠르게 좁힘 (대부분의 매물을 저비용으로 걸러냄)
- **경량 로컬 LLM**: 매물 본문에서 상태 정보를 구조화 추출 ("번인 없음", "생활기스 약간" → 속성값)
- **VL(Vision-Language) 모델**: 매물 사진으로 상태 주장 검증 (번인 흔적, 기스, 파손 등)

## 문서

| 문서 | 내용 |
|---|---|
| [docs/01-project-plan.md](docs/01-project-plan.md) | 프로젝트 계획서 — 목표, 아키텍처, 기술 선택, 단계별 로드맵 |
| [docs/02-functional-requirements.md](docs/02-functional-requirements.md) | 기능 요구서 — 기능/비기능 요구사항, 수용 기준 |
| [docs/03-document-index.md](docs/03-document-index.md) | 문서 정의서 — 제작에 필요한 전체 문서 목록과 각 문서의 목적/작성 시점 |
| [docs/10-collection-research.md](docs/10-collection-research.md) | 플랫폼 수집 조사서 — 3사 robots.txt 실측, 접근 경로 판정, 자동 채팅 리스크 평가 |

## 설치와 실행 (Phase 1 MVP)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 1) 설정: 예시 복사 후 로그인 비밀번호 해시 생성
cp config.example.yaml config.yaml
.venv/bin/joongo-notify hash-password   # 출력값을 config.yaml의 auth.password_hash에

# 2) 실행 (웹 UI + 수집 스케줄러)
.venv/bin/joongo-notify serve           # http://127.0.0.1:8320

# 즉시 1회 수집 (스케줄러 없이)
.venv/bin/joongo-notify once
# 통계
.venv/bin/joongo-notify stats
# 테스트
.venv/bin/python -m pytest
```

- **LLM 분석**: 기본은 온톨로지 기반 휴리스틱 추출기. Ollama를 띄우고 `llm.enabled: true` + `llm.model` 지정 시 LLM 추출로 전환 (실패 시 휴리스틱 폴백).
- **VL 사진 검증**: `vl.enabled: true` + `vl.model` 지정 시, Watch 조건의 시각검증가능 속성(번인·기스 등)을 매물 사진으로 검증. 본문에 없던 정보는 📷사진판정으로 채우고, 본문과 사진이 다르면 ⚠️상충 표시 (최종 판단은 사람).
- **당근마켓 지역**: Watch 지역에 당근 웹 주소의 `?in=` 값("역삼동-6035" 형식)을 넣으면 해당 동네 피드를 수집. 일반 텍스트 지역은 번개장터·중고나라 텍스트 필터로만 동작.
- **알림 표면**: 조건 부합 매물은 **웹 대시보드**의 "최근 매칭"과 매칭 상세에서 확인합니다(푸시 채널 없음). 운영자 경고(어댑터 장애 등)는 서버 로그와 `/health`에 노출됩니다.
- **제품/조건 추가**: `data/products.yaml`(별칭 사전), `data/categories.yaml`(상태 속성) 수정만으로 가능 — 코드 변경 불필요.

## 자동 채팅 (FR-D6, Phase 3)

조건 부합 + 자동문의 임계 초과 매물에 판매자 문의를 발송합니다. Watch별로 `off`(안 함) / `approve`(웹 문의 화면에서 발송 승인) / `auto`(상한 내 자동 발송)를 고릅니다. 본인 계정 로그인이 필요하므로:

```bash
.venv/bin/pip install -e ".[autochat]" && playwright install chromium
.venv/bin/joongo-notify chat-login bunjang    # ① 브라우저에서 직접 로그인 → 세션 저장
# ② data/chat_selectors.yaml에 각 플랫폼 채팅 UI 셀렉터 기입 (로그인 상태에서 확인)
.venv/bin/joongo-notify chat-check bunjang "<매물URL>" --headed  # ③ 발송 없이 셀렉터·로그인 검증
# ④ config.yaml: dry_run=true로 통합 검증 → 문제없으면 dry_run=false, enabled=true
```

발송 상한(기본 시간당 2건·일 5건)과 dry-run 기본값으로 계정 리스크를 통제합니다.
전체 인수 절차: **[docs/33-autochat-acceptance.md](docs/33-autochat-acceptance.md)**.

## 현재 상태

**Phase 3 진행 중** — 자동 채팅 RPA(FR-D6), 웹 UI 고도화(Watch 수정·매물 이력·매칭 상세·시세 그래프·문의 큐·피드백) 완료. 3플랫폼 수집·분석·알림 전체 스택 동작 검증 완료.
남은 항목: 카테고리 확장. (카카오 OAuth·텔레그램 푸시는 범위에서 제외 — 인증은 자체 로그인, 알림은 웹 대시보드)
