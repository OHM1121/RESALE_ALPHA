# ARTITRADE v0.7.54 독립 마켓트렌드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. TDD, 원본 보호, 도구 검증 후 완료 보고.

**Goal:** 주식·코인 마켓트렌드를 AT·MA에서 분리하고 시간봉 4개 각각 별도 시드·원장으로 운영하며 RSI35 이하만 매수대기로 표시하고 RSI30 미도달 관측은 가상매수에서 제외.

**Architecture:** 기존 RSI20 계산기·시세 소켓을 읽기 경로로 공용화하되 독립 trend 전용 월렛/주문 출처를 추가한다. 기존 MT43 보유는 기존 지갑에서 청산하고, 신규 MT54 거래는 전용 원장에만 기록한다. 기존 AI·리서치·post-exit 경로에 소스버전별 연결을 추가한다.

**Tech Stack:** Android Java, Python 3.11 Chaquopy, SQLite, GitHub Actions Android Gradle.

**Spec:** `docs/superpowers/specs/2026-11-15-artitrade-v054-standalone-market-trend-design.md`

## Global Constraints

- v0.7.53 원본 배포 커밋 `0423bcea7fb967889d87d8d961cfd055aaab9a70`; 기존 배포 인증서 SHA-256 `37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6`; 패키지명 `com.artitrade.unified.s21dex`.
- 기존 데이터 삭제·초기화·원본 장부 수정·기존 보유분 자금 이전 금지.
- 주식·코인 각 trend 시간봉 `5m,30m,60m,day`별 독립 `seed,cash,max_positions` (각 초기 2,000,000원/4종목, 기존 설정은 불변).
- 시드/최대보유 개별 조정 가능. 지정 프레임만 차감·반환. 전시장 실제 브로커 잔고·동일심볼 예약 필수.
- RSI <=40 내부관측, <=35 표시, <=30+기존 래치·봉 마감 확인 시 신규 주문판정. 관찰에 그친 후보는 가상체결/고가추적 제외.
- 기존 기준봉·MA 원래 매수조건 유지; 구형 TREND_OPEN은 부모 지갑에서만 매도.
- 신규 LIVE 활성화 명시 이전에는 trend 전용 주문을 LIVE로 보내지 않음. 자금·주문 권한을 기존 LIVE 전환에서 상속하지 않음.

## Review Focus

1. 마이그레이션 중 기존 구형 보유와 신규 보유가 같은 심볼/프레임일 때 중복 매도·이중 체결 방지(Tasks 2-4).
2. RSI 34→36 / 봉판정 시각 경과 / 데이터 중단에서 대기 즉시 소멸하고 VIRTUAL 미생성(Tasks 1,3).
3. 신규 v054 전용 잔액/보유수 수정과 기존 6개 전략의 동일 시간봉 자금 완전 불변(Tasks 2,4).
4. LIVE가 기존 켜져 있어도 신설 trend는 별도 활성화 없으면 외부 주문이 절대 발생하지 않음(Tasks 2,3).
5. UI 대기표가 실주문 PENDING와 WATCH_PENDING을 혼합하거나 계좌 손익·거래건수를 중복 합산하지 않음(Tasks 4,5).

---

### Task 1: RSI 35 대기 수명주기

**Files**
- Create `ARTITRADE_ANDROID/app/src/main/python/market_trend_watch_v054.py`
- Test `ARTITRADE_ANDROID/ui_tests/test_v054_watch.py`

**Interfaces:** `observe(market,symbol,frame,bar_end,rsi,armed,observed_at,live_fresh=True)` -> state event; `expire(market,frame,bar_end)`; `snapshot(market)`; `confirmed`은 별도 기존 주문 래치 기반.

- [ ] 테스트: 40·35·30 경계, RSI 35초과 이탈, 봉마감, 수신 끊김, 동일종목·다른봉 분리; 미완료 관측은 어떤 경우에도 virtual 기록 미발생.
- [ ] 실패 재현 후 최소 순수 파이썬 관측기 구현.
- [ ] 단위테스트 재실행; PASS.

### Task 2: 시간봉별 독립 시드·전용 원장

**Files**
- Create `ARTITRADE_ANDROID/app/src/main/python/market_trend_wallet_v054.py`
- Modify `ARTITRADE_ANDROID/app/src/main/python/android_operation_bridge.py`
- Test `ARTITRADE_ANDROID/ui_tests/test_v054_wallet.py`

**Interfaces:** `TrendWalletV054(root,market,broker_adapter)`, `allocate(frame)`, `paper_buy(frame,...)`, `paper_sell(position,...)`, `snapshot()`; `settings['stock'|'coin']['trend']['frames'][tf]`.

- [ ] 실패 테스트: 프레임별 4개 시드 독립, 5m 매수가 30m 잔고 불변, 보유·매도·재시작 복원, 기존 v076 설정 자동 보정 시 6개 기존 시드 불변, 기존 6계좌 원본 수정 없음.
- [ ] 새 DB 테이블 및 전역 자금/중복예약 보호 설계, 원본 부모 DB 안 씀.
- [ ] 운영설정 저장/적용에 trend 프레임 추가.
- [ ] 테스트 재실행; PASS.

### Task 3: 신규 독립 전략 스케줄러, 기존 전환모드 종료

**Files**
- Modify `ARTITRADE_ANDROID/app/src/main/python/market_trend_runtime_v043.py`
- Modify `ARTITRADE_ANDROID/app/src/main/python/android_runtime_v078.py` (필요한 연결만)
- Tests `ARTITRADE_ANDROID/ui_tests/test_v054_runtime.py`

**Interfaces:** `Coordinator` 신규진입은 `strategy='trend'`로만, `market_trend_status` 진단 필드 및 `market_trend_watch` 목록 조회. 기존 `TrendWallet`은 LEGACY 매도 전용.

- [ ] 실패 테스트: 시장점수±·수동 설정 여부와 상관없이 AT/MA는 기존 모드, trend는 독립 스캔; 신규 신호만 trend 월렛 사용; MT43 기존 보유 청산 경로 유지.
- [ ] 추적기 내부 <=40, 대기 <=35, 새 원장 주문 <=30 연결. 가상 거래는 확정 진입신호 실패 시에만 만들고 대기→시간경과는 건너뜀.
- [ ] 고유 `MT54` 주문·관측 이벤트키, 실제 프레임·포지션ID 저장, stale quote/book은 fail-closed.
- [ ] Python 회귀검사; PASS.

### Task 4: Android 전략 탭·운용 설정·계좌 화면

**Files**
- Modify `ARTITRADE_ANDROID/app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java`
- Modify `ARTITRADE_ANDROID/app/src/main/java/com/artitrade/mobile/UnifiedTradingService.java` (필요 시)
- Tests `ARTITRADE_ANDROID/ui_tests/test_v054_ui.py` 및 기존 Java/Android UI tests

**Interfaces:** `strategyList(stock)` 5전략, `strategyList(coin)` 3전략; `trend` 대기/보유/체결/손익·독립 시드/최대보유; 종합 대시보드 합산 시 중복금지.

- [ ] 실패 테스트: 신규 탭/네비게이션, 시장별 4개 프레임별 seed 필드, watch<=35 표시와 <=30 미달 만료 시 제거, 기존 카운트와 구형 MT43 표시 보존.
- [ ] 새 strategy tab 및 account render 연결, research/AI 진입은 기존 trend 별도 분류 재사용.
- [ ] Java·UI 회귀; PASS.

### Task 5: 리서치·AI·매도후 고가 데이터 정합

**Files**
- Modify `ARTITRADE_ANDROID/app/src/main/python/ai_trend_analysis_v047.py`
- Modify `ARTITRADE_ANDROID/app/src/main/python/ai_strategy_analysis_v040.py` (필요 시)
- Modify `ARTITRADE_ANDROID/app/src/main/python/post_exit_sources_v053.py`
- Tests `ARTITRADE_ANDROID/ui_tests/test_v054_research.py`

**Interfaces:** `market/trend` 기존 리서치·AI에서 MT43/MT54 구분; 실제/가상 매도는 확정 체결 이벤트만 `POST_EXIT_HIGH`로.

- [ ] 실패 테스트: WATCH_PENDING/RSI35 무체결은 전부 제외, MT43 과거와 MT54 신규 거래 정확히 1회 연결, PAPER/LIVE 구분 및 동일종목 재진입 회차 분리.
- [ ] 소스 등록·분석/ZIP 백업 추가, 기존 DB 원본 보존.
- [ ] 연구 관련 테스트 전부 PASS.

### Task 6: 배포 빌드·서명 및 실기기 이전 체크

**Files**
- Modify `ARTITRADE_ANDROID/app/build.gradle` (versionName 0.7.54, versionCode 기존 60 이상)
- Modify/build GitHub workflow as needed for all new tests and source packaging
- Tests existing v043/v045/v051/v052/v053 and new v054, Java, Android 15 emulator

- [ ] GitHub Actions CI/test 추가 후 기존 v053 regression 통과.
- [ ] 새 APK 원본 Android 인증서 지문 / 패키지 / versionCode 및 SHA256 확인.
- [ ] 실제 삼성 기기 데이터 보존 업데이트 가능성 문서화(실기기 미검증이면 명시).
- [ ] 기존 LIVE 주문·PC MSERVER/업비트 장중 장기 시세는 실기기 별도 검증 전 '검증 완료'로 표기하지 않음.

## 검증 및 인수인계

- 각 Task 테스트/리뷰 후 고립 브랜치에 커밋, 기존 PR20/배포 커밋 불변.
- 코딩 완료되더라도 신규 독립 전략은 PAPER 기본. 별도 LIVE 승인 없이 실제 계좌 주문을 활성화하지 않음.
