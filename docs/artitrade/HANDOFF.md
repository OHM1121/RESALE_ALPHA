# ARTITRADE 개발 인수인계서

작성일: 2026-11-19 (한국시간)
현재 배포 버전: 0.0.13
GitHub 저장소: https://github.com/OHM1121/RESALE_ALPHA
배포 APK: https://github.com/OHM1121/RESALE_ALPHA/releases/download/0.0.13/0.0.13.apk
검증 기록: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44477866299
배포 소스 커밋(고정): 17d47cf53411182fb717d362950d6d437ad75d2a
APK SHA-256: 57858287c8d1c8ff9896c9f1ad2ab30e1dac7436058c1320b43954b0d0177a13

## 1. 인계 대상 및 작업 경로
- 실작업 소스 브랜치: `fix-artitrade-010-v009-cross-strategy-rootcause-20261119`
- 서명 빌드 실행 브랜치: `artitrade-phone-main-v070-live-orders-20261112`
- 원본 서명 빌드 워크플로: `.github/workflows/artitrade-013-original-signer-release.yml`
- GitHub 기본 브랜치 `main`과 실작업 브랜치 양쪽에 `AGENTS.md`, `docs/artitrade/USER_ACTIVE_POLICY.md`를 기록함. 미래 코딩 담당자는 이 문서부터 읽고 변경지침 충돌 여부를 확인할 것.

## 2. 이번 주식 매수대기 → 체결 실패의 확정 원인과 변경
- 구버전에는 주식 기준봉 D19 PAPER 매수대기 주문 다수가 남고 신규 BUY 체결이 누락된 이력이 있었음. 과거 로그 요약은 대기 105건 및 원본 BUY 0건임. 모든 대기가 실제 체결 가능했다는 의미는 아님.
- 소스상 확정 원인: 원본 `DStore.root`는 `<app>/stock`인데, 서명된 시장점수 수집기는 `_WORKERS[<app>]`로 등록됨. NEW LEGACY(11.18)의 `score>0` 매수 래퍼가 잘못된 `<app>/stock` 키에서 조회해 `None`을 받았고, 점수가 실제 양수여도 `NL1118_MARKET_SCORE_NOT_POSITIVE`로 거부할 수 있었음.
- `at_market_score_gate_v107.py`: `_score_worker_root`로 주식 지갑 경로를 앱 최상위 원장 경로로 정규화함.
- `android_runtime_v078.py`: 시장점수 수집기를 원본 주식 엔진 시작 전에 준비하도록 래핑함. 잘못된 과거 점수를 가져오거나 가짜 체결을 만드는 경로가 아님.
- `reference_1118_runtime.py`: 시장점수 미수신/오래됨과 실제 점수 0 이하를 별도 매수거부 사유로 표시함. 주식 BUY 조건 `score>0`과 원본 공식 5분봉/호가/세션/자금/중복매수 검사는 그대로 유지함.
- 테스트 `test_v013_stock_at_pending_to_native_buy.py`: 유효한 양수 점수의 원본 D19 매수대기→PAPER 체결→native executions BUY→NEW LEGACY 포지션과 1·2차 PAPER 예약 매도 생성 검증. 0·음수·오래된 점수·점수 미수신은 체결 거부 검증.
- `test_v002_eight_virtual_paper_end_to_end.py`, `test_v004_abnormal_reconnect_six.py` 등 기존 테스트의 매수 점수 가짜 함수 우회 대신 원래의 시장점수 게시자 키를 사용하도록 보정함.

## 3. 기존 작업 유지
- `NEW LEGACY(11.18).xlsx` 기준은 기준봉(AT), MA, 시장추세, 코인 기준봉·MA·시장추세에만 적용함.
- 수급매매(GMO)와 종가베팅(CW)는 NEW LEGACY/구형 데이터 정리 예외임. 기존 원장·체결·매수매도 규칙 영향 금지.
- 기존 LEGACY PAPER 매수·매도·보유·손익 기록은 `legacy_purge_v012.py`에 의한 1회성 출처 검증 삭제·현금/실현손익 역산을 유지함. 출처 불확실, 실계좌 주문 가능성, 고아 체결은 강제로 지우지 않고 `PARTIAL`로 남김. 원본 계좌·GMO/CW 기록 삭제 금지.
- 보유종목 화면 왼쪽 배지는 `5분`, `30분`, `60분`, `일봉`으로 표기함. 종목명 옆 중복 봉 주기 표기는 제거함.
- 즉시 생성되는 30% TP1/35% TP2 예약 정보는 현 버전에서 PAPER SQLite 예약 상태임. 실거래 주문접수번호가 확인된 외부 거래소 예약매도 주문이 아님.
- 버전명은 `0.0.13`, versionCode `135`; 외부 APK 파일명은 `0.0.13.apk`, 릴리스 태그 `0.0.13`임. 파일명/태그에 FIX, TEST, 날짜, 문자 접미어 금지.

## 4. 검증 결과와 남은 확인
- GitHub Actions `44477866299`: 전체 SUCCESS. Java Android arm64 빌드, 기존 인증서 동일성, 원본 내장 모듈 SHA와 수정 이력, 의존성 계약, 8개 PAPER 매매 전략 및 매수/매도·재접속 회귀 테스트 성공.
- 패키지: `com.artitrade.unified.s21dex`; 인증서 SHA-256: `37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6`.
- 검증 경계: 실제 삼성 휴대전화의 KRX 정규 장중 매수대기→체결은 아직 실증되지 않음. 별도 사용자 기기 로그에서 실시간 서명 점수(양수), 공식 완성 5분봉, 실시간 같은 거래소 0B/0D, 호가량, 원본 자금 및 native BUY executions를 순서대로 증빙해야 함. 차단 사유가 관측되면 그 원인을 원본 소스·원장 수준으로 재검증할 것.
- 기존 로그에서 관측된 미검증 공식 5분봉 갭, DB 잠금 경합, 시세 지연·수량 부족은 추가 실패 요인일 수 있으나, 현재 빌드가 실제 기기에서 이를 모두 해결했다는 확인은 없음. 정당하지 않은 체결을 만들거나 오래된 가격으로 소급 체결하지 말 것.
- LIVE 브로커 주문은 비활성. PAPER 체결을 실계좌 실제 주문으로 설명하지 말 것.

## 5. 향후 사용자 지시 충돌 대응 의무
- 2026-11-18 NEW LEGACY(11.18) 원본과 2026-11-19 설정을 `docs/artitrade/USER_ACTIVE_POLICY.md` 및 `AGENTS.md`에서 우선 확인할 것.
- 신규 지시가 기존 조건·봉 주기·시장점수·매도 비율·GMO/CW 독립성·기존 원장 삭제·PAPER/REAL 구분·버전명과 충돌할 것으로 예상될 때, 수정 전에 사용자에게 다음을 먼저 답변할 것:
  `기존 기준 설정일 / 기존 기준(정확한 수치·조건) / 충돌하는 신규 지시 / 예상 결함(발생 전략·체결·손익 등) / 유지 또는 변경에 필요한 결정`.
- 사용자가 명시적으로 기존 기준 변경을 승인한 후에만 해당 부분을 개정하고, 기존 규칙 기록은 삭제하지 말고 변경 일자와 사유를 정책 문서 및 GitHub 이슈에 추가할 것.

## 6. 다음 담당자 작업 순서
1. 기존 앱 삭제 금지; 동일 패키지·동일 서명으로 업데이트 설치.
2. 장중 증빙: 기준봉 PENDING 원본 1건 선택 → 거래가능 장·유효한 완료 봉 확인 → 시장점수 >0 확인 → 유효한 거래소 0B·0D 및 자금 검증 → 원본 BUY execution/포지션/30%·35% PAPER 예약 생성 확인.
3. 미체결 시 반환된 정확한 `NL1118_MARKET_SCORE_UNAVAILABLE:...`, `NL1118_MARKET_SCORE_NOT_POSITIVE` 또는 native 실패코드로 구분. 점수 무시·허위 매수·가짜 호가 적용 금지.
4. GMO·CW 및 코인, MA, Trend 원장을 함께 회귀 검사.
5. 이슈 https://github.com/OHM1121/RESALE_ALPHA/issues/97 과 https://github.com/OHM1121/RESALE_ALPHA/issues/93 을 장중 증거가 확보될 때까지 `DEVICE_UNVERIFIED`로 유지.

## 7. 채팅 입력 유의사항
- 사용자 요청: 인수인계 내용은 채팅에서 직접 복사 가능해야 하며, 채팅 입력창 자동 입력 또는 자동 전송을 실행하지 말 것. 위 원문을 복사 가능한 문서형 메시지로 제공하면 됨.
