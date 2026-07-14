# 33. 자동 채팅 인수 절차 (Auto-Chat Acceptance)

- 문서 버전: v0.1
- 대상: FR-D6 자동 채팅(완전 자동 원안)을 실사용에 투입하기 위한 인수 체크리스트
- 관련: `data/chat_selectors.yaml`, `autochat/sender.py`, CLI `chat-login`/`chat-check`

자동 채팅 실발송은 **본인 계정 로그인**과 **플랫폼별 채팅 UI 셀렉터**가 필요하다.
개발/자동화 환경에서는 로그인 이후를 검증할 수 없으므로, 아래 절차로 인수자가
직접 검증한 뒤 켠다. 미검증 상태에서는 셀렉터가 비어 있어 발송이 **거부**된다.

> ⚠️ 계정 제재 리스크는 발주자가 인지·수용함(Q6). 개인·저빈도 운영이 전제다.
> 발송 상한(기본 시간당 2건·일 5건)과 dry-run 기본값이 안전장치다.

---

## 0. 사전 준비

```bash
pip install -e ".[autochat]"
playwright install chromium
```

`config.yaml`의 `autochat` 블록 (초기값):

```yaml
autochat:
  enabled: false      # 아직 끄기
  dry_run: true       # 아직 켜기
  hourly_limit: 2
  daily_limit: 5
  headless: true
  profile_dir: chat_profiles
```

## 1. 로그인 세션 저장

플랫폼마다 본인 계정으로 로그인해 세션(브라우저 프로필)을 저장한다.
자격증명은 코드가 다루지 않으며, 사람이 직접 로그인한 세션만 재사용한다.

```bash
joongo-notify chat-login bunjang    # 브라우저 창이 뜸 → 로그인 → Enter
joongo-notify chat-login joongna
joongo-notify chat-login daangn
```

프로필은 `chat_profiles/<platform>/`에 저장된다.

## 2. 셀렉터 기입

로그인 상태에서 각 플랫폼의 매물 페이지를 열고, 브라우저 개발자 도구로
채팅 UI 요소의 CSS 셀렉터를 확인해 `data/chat_selectors.yaml`에 채운다.

| 키 | 의미 | 예시 |
|---|---|---|
| `logged_in_marker` | 로그인 상태에서만 있는 요소 (없으면 발송 거부) | `a[href*='mypage']` |
| `chat_button` | 매물 페이지의 "채팅/문의" 버튼 | `button:has-text('문의')` |
| `message_input` | 메시지 입력창 | `textarea[name='message']` |
| `send_button` | 전송 버튼 | `button:has-text('전송')` |
| `sent_marker` | (선택) 전송 완료 확인 요소 | `.message.sent` |

`chat_button`/`message_input`/`send_button` 세 개는 필수다. 하나라도 비면
그 플랫폼은 발송이 거부된다(`selector_gaps`).

## 3. 셀렉터 인수 검증 (발송 없음)

실제 매물 URL로 각 단계가 해석되는지 확인한다. **전송 버튼은 클릭하지 않는다.**

```bash
joongo-notify chat-check bunjang "https://m.bunjang.co.kr/products/123456" --headed
```

출력 예:

```
[bunjang] 셀렉터 인수 검증 결과
  로그인 세션   : ✅
  채팅 버튼     : ✅
  메시지 입력창 : ✅
  전송 버튼     : ✅  (검증만 — 클릭 안 함)
  → 모든 셀렉터 정상. dry_run:false 로 전환하면 실발송됩니다.
```

`❌`가 나오면 해당 단계 셀렉터를 다시 확인하거나(로그인 세션 만료면 1단계 재실행)
`data/chat_selectors.yaml`을 수정한 뒤 재검증한다.

## 4. dry-run 통합 검증

`enabled: true`로 켜되 `dry_run: true`를 유지한 채 서버를 돌린다. auto 모드 Watch가
고점수 매물을 만나면 문의가 `queued`로 쌓이고, 스케줄러가 **입력창까지만 채운 뒤
전송 없이** 상태를 `dry_run_ok`로 옮긴다(무한 재처리 없음). 웹 `/chats`에서 확인한다.

```yaml
autochat:
  enabled: true
  dry_run: true
```

## 5. 실발송 전환

dry-run으로 문제가 없으면 실발송을 켠다.

```yaml
autochat:
  enabled: true
  dry_run: false
```

- 첫 실발송은 **approve(사전 승인)** 모드 Watch로 시작하기를 권장한다:
  웹 `/chats`에서 각 문의를 사람이 눈으로 확인 후 [발송 승인]을 눌러야 나간다.
- 완전 자동(auto)은 신뢰가 쌓인 뒤 Watch별로 켠다.
- 발송 상한은 항상 강제된다. 상한 도달 시 나머지는 다음 기회로 미뤄진다.

## 인수 완료 기준(AC)

- [ ] 3개 플랫폼 모두 `chat-check`가 로그인·필수 셀렉터 3종 ✅
- [ ] dry-run에서 문의가 `dry_run_ok`로 이동하고 재처리되지 않음
- [ ] approve 모드로 1건 실발송 성공, 발송 전문·시각이 `/chats`에 기록됨
- [ ] 발송 상한 초과 시 추가 발송이 보류됨
