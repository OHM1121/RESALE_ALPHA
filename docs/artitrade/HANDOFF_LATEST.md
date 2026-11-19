# ARTITRADE 0.1.0 개발 인수인계서

작성일: 2026-11-19 (KST)
사용자 최종 지시: 오직 0.1.0 활성화 이후 신규 매수 PAPER 포지션의 앱 종료 중 매도조건 복원.
기준 소스: 0.0.19 서명 후보의 정확한 소스 커밋 `c99f2246887e34ffbd3a534503a20095c35a6985`.
0.1.0 신규 개발 브랜치: `artitrade-010-future-paper-offline-chronology-20261119`.
출시 요구 버전: `0.1.0` / versionCode `142` / 파일 `0.1.0.apk`.
패키지: `com.artitrade.unified.s21dex`, 기존 인증서 SHA256 `37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6`.
최신 GitHub 정식 릴리스: `0.0.18`; 0.0.19는 사용자에게 제공한 서명된 후보 APK만 존재하며 GitHub 정식 release 미게시. 0.0.19 CI: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44539092013 (SUCCESS), 0.0.19 화면 목표계획 연결·8원장 회귀·원본 서명 확인. 실제 DEVICE_VERIFIED 아님.
0.1.0 상태: SPECIFIED ONLY. 아직 0.1.0 프로그램 구현/CI/서명 APK/실기기 검증 없음. **0.1.0 배포 완료라고 주장 금지.**

## 1. 필수 인수 규정

원본 `AGENTS.md`, `docs/artitrade/USER_ACTIVE_POLICY.md`, `docs/artitrade/DEPENDENCY_MAP.md`, `docs/artitrade/dependency_graph.json`, `docs/artitrade/version_issues.json`, NEW LEGACY(11.18).xlsx/rev1 원본 표시와 Issues #109/#110을 읽고 모든 전략의 API → 공식 시세/체결 → 큐 → BUY → 원본 정책/손절 → OWNER 지갑/원장 → SQLite/GMO JSON → UI → 백업까지 매핑.
원장 SQLite/ZIP·계정·개인 체결·API 키를 public GitHub에 업로드하지 말 것. 사용자 앱·원장 삭제/초기화 금지. FreshEpochGate, LedgerAntiRollback 독립 증인, 원본 wallet UID와 기존 브로커 권한 불변.
증거 상태 CONFIRMED/INFERRED/UNVERIFIED와 코드 상태 SOURCE_FIXED/CI_VERIFIED/DEVICE_VERIFIED 구분.

## 2. 새로운 범위 — 과거 전체 소급 정산 제외

**0.1.0 설치·기능 활성화 이후 정확한 최초 원본 BUY가 기록된 신규 PAPER 포지션만 적용.**
기존 0.0.19 및 그 이전에 매수한 보유/청산 포지션은 자동 재정산 금지. 사용자는 과거 전체 PAPER 거래를 소급 정산할 필요 없다고 명시함.
0.1.0 활성화 시각·epoch·사용 중인 원본 지갑·새 BUY 원본 receipt를 증명하고 UI의 기존 포지션/이전 epoch와 혼동 금지.

## 3. 동작 — 반드시 완성

예: 100원 신규 BUY, 매수 당시 1차 PAPER 목표가 104원/계획수량 확정, 앱 종료 중 공식 같은 거래소 가격이 105원까지 거래, 재실행 현재가 102원.
(1) BUY 원장 커밋과 함께 원본 target, 최초 수량, 매수가, 단계, native position ID, UID, epoch, 정확한 entry instant, 방어/손절/후행/만기 상태를 내구적으로 확정.
(2) 앱 종료/가격 수신 중단 시각과 마지막 공식 단조 증가 event checkpoint를 남김.
(3) 재시작 시 해당 BUY 이후의 **완전한 공식 KRX/Upbit 시간순 이력**을 같은 시장·종목에 대해 검증·복구; 거래일/장중 중단/공식 확정봉·가능한 틱·호가와 데이터 결손을 구분.
(4) 그 당시 실제 전략의 모든 보호 이벤트(하드스톱, forced full exit, 만기, 기준봉 저가+확정 종가, trailing, TP1/TP2, 잔량)를 시간순 재생.
(5) 105원이 104원 TP보다 **엄격히 높고**, 선행 손절·강제전량매도가 없고, 동봉 선후 불명·과거 봉 결손이 없으며 당시 PAPER limit eligibility까지 입증된 경우 **104원에 원래 계획량만 PAPER 한정 가정체결**. 2차는 실제 정당화된 TP1 이후에만 검증.
(6) 손절이 TP보다 먼저면 손절이 우선, 뒤의 105원으로 익절 생성 금지. 같은 1분봉 OHLC 손절·익절 동시 터치하나 tick order 불명: `EXIT_SEQUENCE_UNVERIFIED`, 현금/수량/익절 원장 수정 보류.
(7) 원본 wallet 소유 하에서 PAPER 가정 체결을 브로커 실주문/확정 REAL 체결과 다른 영수증 reason으로 기록. owner+epoch+native position ID+stage 단위 EXACTLY ONCE. 수수료·세금·현금·실현손익·잔량·배분/체결 영수증을 하나의 원자적 트랜잭션에 반영.
(8) 복구 도중 중복 주문/별도 가격 큐가 발생하지 않도록 포지션별 gate, 기존 원본 realtime SELL과 직렬화, WAL-safe 사전백업 및 independent rollback witness 재검증. 앱 크래시·재시작 복원.
(9) REAL/브로커 주문 결코 인위 소급 생성 금지, 시세누락·대응 실패·원본 BUY 미확인 시 FAIL-CLOSED. 사용자에게 단계별 `PAPER_OFFLINE_RECOVERY_PENDING/VERIFIED/APPLIED/EXIT_SEQUENCE_UNVERIFIED` 상태 표시.

## 4. 원본 전략 구분(변경 금지)

- 주식 AT·MA·MarketTrend / 코인 Reference·MA·MarketTrend: 원래 정책의 분봉별 TP1/TP2 유지. 최초 수량 TP1 30%, TP2 35%, 잔량 35% 원본 보호/후행.
- 주식 GMO: 최초 점수별 TP1 +4/+5/+7%, 초기 수량 20%; TP2는 **TP1 목표가격 기준 추가 +3%**, 초기 수량 40%. 별도 `PROJECTGMO_V1.0_account_state.json` 원장·현금/잔량/수수료 구현 필수, 일반 SQLite 코드 도용 금지.
- 주식 CW: 매수가 대비 +1.2%에서 전량, 2차 없음.
- 기존 0.0.18 rev1 MA/Trend 선행 강제매도, -15% 손절 및 만기, 구 5m Trend 신규진입 금지, 이전 보유 5m SELL 경로 유지.
- PAPER 미리 계산된 지정가 계획은 거래소가 접수한 실제 주문 아님. 재실행 시 1분봉 HIGH만 보고 매도했다고 주장 금지.

## 5. 현재 자산과 위험

- 기존 `staged_exit_preview_v019.py`: 8원장 목표/수량 화면, GMO 원본 매수가(소수 정밀도)·보유량과 표시 반올림 차이 보완, 타 전략 비율 적용 방지.
- 기존 `paper_exit_chronology_v019.py`: 읽기 전용 이벤트 감사.
- 기존 `paper_exit_replay_v019.py`: 사용자 승인된 PAPER 가정체결 후보 산출; 완전한 원본 장중 이력 공급과 실시간 전략 손절 재생 미연결.
- 기존 `paper_exit_native_ledger_v019.py`: 7개 SQLite 지갑별 원장 정산 소스 시범, 실행 경로 **미연결·전 owner 검증 미완료**. 임의 활성화 금지.
- 사용자 제공 2026-11-19 휴대폰 원장 ZIP은 개인자료: 공개 GitHub 업로드 금지. 기존 기록 점검에서 3건 coin MA·1건 stock GMO OPEN, 과거 마감 이후 HIGH를 매도 전에 소급할 수 없다는 사실 확인. 신규 버전은 **기존 보유 소급 보정하지 않음**.
- 공식 1분봉 OHLC에 선후 불명인 stop/TP가 함께 있으면 원천적으로 정확한 시간순 복원이 불가능; 거래/호가 tick 또는 기록된 원본 정책 평가 없이 수익 체결 금지.
- 원본 서명 키는 동일 저장소라도 브랜치별 GitHub cache가 다를 수 있음. 0.0.19에서 작업 브랜치 캐시 지문이 다른 사례가 발생함. **검증된 0.0.18 원본 서명 브랜치 작업 CI에서만** 이전 인증서 SHA256 일치 확인 후 빌드해야 함.

## 6. 개발 순서 / 0.1.0 배포 인수조건

1. 공식 API 자료 복구 가능성과 1분봉 내 순서 증명 방식(이벤트 누락/시장 재개, 원본 틱/체결 시각, policy evaluation) 조사. 부족한 구간은 보류 상태로 명확히 노출.
2. 원본 신규 BUY 내구 저장 및 오프라인 cutover marker, activation gate 구현.
3. 8개 owner별 정책 재생(손절 first, TP1 then stop, TP1 then TP2, full exit, same-bar ambiguous), GMO JSON 별도 atomic 정산과 원본 SQLite 7원장/UID 매핑.
4. 원장 백업→witness→중복 방지→재시작/중단 복구 실제 검증. 현금/수수료/손익/수량 불일치 시 거래 중단 및 원본 보존.
5. Android UI·hub_records(for_ui)·연구·내보내기·WAL 백업과 모든 출처 상태 연결. 별도 브로커 주문 생성 금지.
6. 전체 8전략 회귀, 새 버전 시뮬레이션, Java 컴파일, `0.1.0`/`142`/`com.artitrade.unified.s21dex` 확인, 원본 인증서 SHA256 검사, 비식별 GitHub CI/Issue #110 갱신.
7. 실기기 DEVICE_VERIFIED는 별도. 위 **신규매수 오프라인 복원**을 실제 CI로 확인하기 전 APK를 완성·배포되었다고 주장하지 말 것.

## 7. GitHub 보관 / 입력창 금지

- 날짜별 보관 경로: `docs/artitrade/handoffs/ARTITRADE_HANDOFF_2026-11-19_0.1.0.md`.
- 최신본 경로: `docs/artitrade/HANDOFF_LATEST.md`.
- 기존 날짜별 0.0.16/0.0.19 이전 문서는 덮어쓰지 않음.
- 이 문서의 본문은 사용자 채팅에서 복사 가능하도록 동일하게 전달.
- **채팅 입력창 자동입력, 메시지 제출 준비·자동전송, 후속 메시지 자동 삽입 금지.** 오직 현재 답변과 승인된 GitHub 파일만 기록.

추적: https://github.com/OHM1121/RESALE_ALPHA/issues/109, https://github.com/OHM1121/RESALE_ALPHA/issues/110
