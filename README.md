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
- **텔레그램 알림**: `telegram.enabled: true` + 토큰 설정 후, 웹 [설정]에서 chat_id 등록(테스트 발송 포함). 미설정 시 콘솔 출력.
- **제품/조건 추가**: `data/products.yaml`(별칭 사전), `data/categories.yaml`(상태 속성) 수정만으로 가능 — 코드 변경 불필요.

## 현재 상태

**Phase 2 구현 완료** — 3개 플랫폼(번개장터·당근마켓·중고나라) 동시 감시, 교차 플랫폼 중복 제거, VL 사진 검증(상충 표기). 실매물 대상 3플랫폼 엔드투엔드 검증 완료 (2026-07-11).
Phase 3 예정: 자동 채팅(FR-D6, RPA), 웹 UI 고도화, 카카오 OAuth, 카테고리 확장, 피드백 루프.
