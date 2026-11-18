# ARTITRADE 0.0.1 초기화 이후 엔진 장애 — 복구 작업 기록

## 버전 순서 (절대 혼동 금지)
- v0.9.5: 원장 초기화 전 최신 정상판. Tag: ARTITRADE-v0.9.5-20261118.
- v0.0.1: 신규 PAPER 원장 초기화 및 새로운 소스 오버레이가 함께 배포되며 사용자 신고 장애 발생. Tag: ARTITRADE-0.0.1.
- v0.0.2 NEW LEGACY(11.18): 장애 발생 이후 사용자 작성 Excel 신규 매매정책. 이전 0.0.1 장애 원인이 될 수 없음.

## 직접 비교해 확인한 문제
- 시장점수/매매/보유·체결 데이터와 연구자료 SQLite·JSON이 광범위하게 초기화되었음. 매매 코드 파일은 별도 디렉터리여서 삭제 대상은 아니었음.
- 시장점수 worker는 오류로 종료되어도 이전 객체가 남아 재시작되지 않는 잠재 결함이 있었음. 초기화로 비어진 점수 DB가 다시 채워지지 않을 수 있음.
- 시장점수와 무관한 마켓트렌드의 preclose candidate 및 진입 판단 함수가 v0.0.1에서 대규모 교체되었음.
- v0.0.1에서는 원본 v0.9.5 주식·코인 preplan worker가 비활성화되어 후보 준비 경로가 달라졌음.
- 기준봉 AT 및 코인 일반 엔진에는 v0.9.5에 없던 score gate 및 audit wrapper가 설치됨.

## 복구 내용
- 기존 ARTITRADE-0.0.1 태그 기반의 별도 복구 브랜치에 패치 적용. 기존 초기화 마커/API 인증/운용설정 및 신설된 사용자 원장은 그대로 보존.
- market_trend_runtime_v043.py, market_trend_preplan_v099.py를 v0.9.5와 byte-for-byte 동일하게 복원.
- android_runtime_v078.py에서 v0.0.1에 새로 생긴 기준봉·코인 일반 BUY gate 및 audit hook 설치 해제; 0.0.2 Excel 정책과 별도.
- market_research_v033.py에 실제 worker 종료 시 재시작 복구 추가. 가격·시장점수·주문 데이터 생성 없음.
- 원장 display aggregation 이전에 시장점수 worker 부팅을 호출하도록 변경.
- 원본 v081 테스트의 8초 예상값이 v0.9.5/v0.0.1 양측 실제 사전로딩 22초와 불일치하여 테스트 기대값만 22초로 교정. 앱 실행 코드는 미변경.

## 검증 완료
- GitHub Actions workflow: artitrade-001-reset-runtime-recovery-no-apk.yml.
- 원본 v0.9.5 내장 EXE asset과 네이티브 trend 코드 동일성 검증 성공. SHA256: b014a00fd2cc29984650cb7ea24e7ea9672d6480fd68e91a6a1ebbe72754dfbb.
- 주식·코인 후보/MA/AT/CW/GMO/Trend/체결 및 새 빈 원장 점수 재생성 관련 synthetic test 119개 통과 (기본 36 + 추가 83).
- 실제 휴대전화의 Kiwoom/Upbit live socket과 체결 기능의 정상 작동을 검증한 것은 아님.
- 새로운 APK, 서명, GitHub Release 배포, 실거래 주문, 휴대전화 SQLite 초기화 또는 구형 데이터 자동복원은 수행하지 않음.

## 사용자가 제시한 ZIP CMD 캡처에 관하여
- 캡처에 표시된 DIAGNOSTIC_SAVED=...DIAG.txt는 파일이 생성된 경로이며 검사 결과 본문이 아님.
- 최근 붙여넣은 내용은 ARTITRADE_ZIP_CHECK.ps1 프로그램 소스 자체로, 실제 DIAG 결과의 RESULT=, ERROR_TYPE=, UNREADABLE_MEMBERS= 행을 포함하지 않음. ClientError 원인 미확정.

## 후속 통합 기준
- NEW LEGACY 0.0.2 Excel 요구와 0.0.1 원장 초기화 직후 장애를 엄격히 분리.
- 이 복구 브랜치의 구형 v0.9.5 trend 진입 로직을 0.0.2에서 무조건 덮어쓰지 말 것. 0.0.2의 의도적인 새 매매 정책 overlay와 조정하여 병합.
- 실제 단말 시험 시 원장/후보/매수·매도 DB가 새 이벤트로 증가하는지, 시장점수의 진짜 외부 시황 수신과 화면 게시가 되는지 별도 검증.
- 새 원장을 재초기화하지 않으며 사용자 승인 없는 LIVE 주문을 금지.

복구 브랜치: fix-artitrade-001-post-reset-engine-recovery-20261118