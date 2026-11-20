# ARTITRADE NEW LEGACY (11.18) / 0.0.2 — 작업 재개 및 장애 감사

기준: 2026-11-18, 저장소 OHM1121/RESALE_ALPHA, PR #51, 원본 작업기준 커밋 `57eb7e9`.

## 1. 요청 기준 및 배포 제약
- 사용자 제공 '두 번째 엑셀'을 **유일한 수치·정책 명세**로 삼아 모든 시트/숨김시트/표/주석/조건을 감사해야 함. 현재 이 브랜치에는 XLSX 원본이 커밋되어 있지 않음. 파일 원본 확보 전 전체 조건 이식 완료라고 표시 금지.
- 0.0.1 최초 PAPER 원장 초기화 이후 매수후보 미탐색, 주식/코인 시황 점수 미표시, 구동 흐름 이상 신고. 주식·코인 API 인증 연결만으로 점수 원천 REST/Yahoo/Upbit 및 전략별 후보 배치 성공이 증명되는 것은 아님.
- 사용자 의도는 원장 초기화이지 기능 삭제가 아님. 기존 8전략 감시/체결/시장점수/UI/AI/ZIP/PC 백업의 실행 흐름을 유지해야 함.
- LIVE 및 실제 거래 주문 절대 자동 활성화 금지. 추가 승인 전 서명·APK/Release 배포, 휴대전화의 두 번째 원장 삭제, 과거 SQLite 자동 복원 금지.

## 2. 소스에서 확인한 사실
1. `fresh_epoch_reset_v106.py::_history`는 `stock/` 및 `coin/` 아래의 .sqlite3/.db/.json/.jsonl/.zip 등 **보유·체결만이 아니라 후보/연구/운용 관련 데이터 파일 전반**을 삭제 대상으로 취급함. 루트의 `market_research_v033.sqlite3` 역시 `market_` 접두어로 삭제됨. 새 실행에서 이 DB와 이벤트를 다시 구성하지 못하면 후보/점수 내역이 빈 상태로 남음.
2. `market_research_v033._MarketWorker`는 원본 코드에서 종료 후 `_WORKERS` 레지스트리에 죽은 객체가 영구히 남는 결함 존재. 이 브랜치에 `ensure_worker`와 초기 빈 원장 부팅 경로 추가. 기존 점수 산식·진짜 API·LIVE 조건은 그대로 둠. 소스 전용 회귀검증.
3. `at_market_score_gate_v107`는 시장점수의 **음수**일 때만 기존 주식 AT/코인 일반 신규매수를 차단하는 0.0.1 이전 보호 수준임. NEW LEGACY의 `reference_1118_runtime` 최종 wrapper는 `validate_stock_score`에서 `score > 0`이 정확히 증명되지 않으면 신규 주식 AT/코인 일반 매수를 차단함. 후보가 없는 현상과 신규매수가 막힌 현상을 별도로 진단해야 함. 점수 미수신을 임의 양수로 치환 금지.
4. `market_trend_runtime_v043._refresh_rank`는 주식 ranks 50 미만 또는 코인 유효 ticker 50 미만에서 에러를 기록하고 장시간 실패 후 후보목록을 비움. 인증된 계좌 연결과 해당 조회의 성공은 별개임. `_last_fetch_error`와 `OPERATIONAL_RANK_...` 기록 및 원본 실제 스캔 DB 대조 필요.
5. `market_research_v033._fetch_stock`는 Yahoo/Naver 공개 지표, `_fetch_coin`는 Upbit 공개 ticker/BTC/ETH 확정봉을 사용함. 주식은 KRX 장외시간에 `display_snapshot`이 현행 점수를 `None`으로 표기하는 현행 정책임. 장외 '미표시'와 장중 '수집 실패'를 구별할 것.
6. 새로운 11.18 매도 단계는 1차 원수량 30%, 2차 원수량 35%, 최종 35%임. 기준봉·MA·Trend의 실제 SELL SQLite 체결 유무를 확인하고 상태를 전환하도록 소스 작성. 별도 synthetic 테스트는 존재하나 실기기 거래소 호가/매매/재부팅 실제 검증 아님.
7. `ARTITRADE_ZIP_CHECK.cmd/.ps1`은 PC의 개별 ZIP 컨테이너를 **로컬에서 부분적으로** 읽고 작은 DIAG TXT를 만드는 도구임. 전체 multipart SHA/연속성, 기기 SQLite 내용 또는 ChatGPT 업로드 `ClientError`의 네트워크·용량·권한 원인을 증명하는 도구가 아님. TXT `RESULT`와 `ERROR_TYPE`을 기반으로 ZIP 자체 손상 여부를 분리해야 함.

## 3. 새로 실제 반영한 코드/검증
- `market_research_v033.ensure_worker`: 멈춘 주식/코인 점수 스레드가 레지스트리에 고정되는 결함 수정. 필요 시 독립 작업자 재기동.
- `android_runtime_v078._hub_records_v078`: 무거운 기존 원장 스냅샷을 읽기 전에 점수 작업자 초기화/회복. 시세 점수나 거래 체결은 생성/변조하지 않음.
- `test_v1118_fresh_score_bootstrap.py`: 완전히 빈 SQLite에서 점수 재기록, 스레드 종료/재기동, API 점수 미확인 시 매수 차단, 스냅샷 호출 순서 검사.
- `test_new_legacy_1118_ai_basis.py`: 11.18 MA/Trend 매수·매도 사유가 원본 종목/포지션/체결 ID/체결시각과 일치하는지 가상 SQLite 테스트.
- GitHub Actions `artitrade-1118-workbook-policy-test.yml`에 두 시험 추가, APK 없이 컴파일/소스 회귀검증.

## 4. 이어서 대조할 실물 증거/필수 완료 기준
- Excel: 원본 파일(모든 시트·수식·숨김 셀 포함)을 셀 주소별 명세로 변환하여 주식 5/코인 3전략의 후보·매수·추적·매도·손절·시장점수·보유시간·분할수량에 1:1 교차표 작성. 문서에만 적혀 있는 규칙과 실제 원본 전략 핸들러가 다른 경우를 모두 실패로 표시.
- Reset 전후: v0.9.5 원본과 0.0.1 적용 후의 8개 runner 시작, 최초/반복 조회, 실시간 WebSocket 0B/0D/Upbit, 후보 적재, market-score 점수 이벤트, UI 노출/원장 SQLite 경로의 주기적 생성을 비교. 구 원장을 다시 가져오는 방식으로 문제를 감추지 않음.
- 실제 기기: 부팅 이후 신규 `stock/`, `coin/` 매매 DB가 재생성되는지, `market_research_v033.sqlite3` 이벤트가 쌓이는지, 후보가 0건이면 원천 universe/세션/시세/전략 탈락 사유가 기록되는지 확인.
- ZIP: 휴대전화 분할 ZIP 각각과 bundle_index 해시, 복원 검증, 디바이스 내보내기 SAF, PC 로컬 ZIP 진단, ChatGPT 수신 오류 분리. `ClientError`를 ZIP 손상으로 단정하지 않음.
- 8개 엔진 PAPER 독립 슬롯/원장 정상 흐름 및 Live 거부, 원장 반복초기화 없음, 모든 관련 CI 성공을 검증하기 전 결과를 완성본으로 배포하지 않음.

## 5. 검증 범위 주의
- 이번 브랜치 조치는 실행 소스와 GitHub Actions 회귀시험으로 제한됨.
- 실제 삼성 단말의 0.0.1 설치본, 이미 삭제된 레거시 원장, 사용자의 원본 엑셀, 로컬 ZIP 결과 파일은 이 커밋에서 임의 변경되지 않음.
