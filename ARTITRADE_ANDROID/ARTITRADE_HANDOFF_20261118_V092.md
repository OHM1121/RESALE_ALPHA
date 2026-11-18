# ARTITRADE 개발 작업 인수인계서

작성일: 2026.11.18 (KST)  
인수인계 기준: v0.9.2 / VersionCode 118  
대상: 주식·코인 통합 Android PAPER 자동매매 앱

## 1. 프로젝트 및 최신 배포 현황

- GitHub: https://github.com/OHM1121/RESALE_ALPHA
- 구조: Android Java UI/Foreground Service + PC 원본 Python 82개 모듈 + 전략별 SQLite/WAL
- 주식 API: 키움증권. 전략: 기준봉(AT), MA, 종가베팅(CW), 수급매매(GMO), 마켓트렌드
- 코인 API: 업비트. 전략: 일반, MA, 마켓트렌드
- 거래 방식: 실제 거래소 시세를 사용하는 PAPER 가상매매. 실계좌 주문 기능 검증으로 해석 금지.
- 최종 배포: ARTITRADE v0.9.2 (VersionCode 118)
- 패키지: `com.artitrade.unified.s21dex`
- APK 직접 다운로드: https://github.com/OHM1121/RESALE_ALPHA/releases/download/ARTITRADE-v0.9.2-20261118/ARTITRADE_UNIFIED_v0.9.2_20261118.apk
- 배포 페이지: https://github.com/OHM1121/RESALE_ALPHA/releases/tag/ARTITRADE-v0.9.2-20261118
- APK SHA-256: `8545ddebc871ad11cdb4511e0fb74ebd0506813f622439de1db812adb98b2f27`
- APK 크기: 30,393,293바이트
- 인증서 SHA-256: `37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6`
- 실제 APK 소스 커밋: `ad68fcf674bcb2c149b3e2380d27d9c2dd7c3583`
- 개발 브랜치: `fix-artitrade-v092-unified-recovery-scenarios-ui-20261118`
- 소스 PR: https://github.com/OHM1121/RESALE_ALPHA/pull/46 (초안)
- 최종 성공 GitHub Actions: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44242964831
- 기존 앱 삭제·데이터 초기화 없이 덮어 설치할 것.

## 2. 주요 개발 경과

### v0.8.4 ~ v0.8.6
- 코인 MA 수동 강제매도 UUID·원본 SELL 조회·중복매도 방지, 검증된 업비트 공식 호가 보완.
- 주식 AT·MA·CW·GMO·마켓트렌드의 PAPER 강제매도 경로와 KRX 0D/키움 ka10004 호가 검증 보완.
- 실제 원본 SELL 기록 확인 후 성공 표시, 부분매도 및 중복 처리 방지.
- 단말 전체 보유종목 강제매도 성공 여부는 당시 미검증.

### v0.9.0
- 휴대전화 방향 잠금 무시 원인인 `fullSensor`를 `fullUser`로 변경. 세로·가로·역방향 가로 지원.
- 회전 시 Activity 재생성·사전로딩 초기화·앱 종료 위험을 줄이는 처리 추가.
- 오류이력 ZIP에 원래 SQLite 파일명을 보존하도록 개선하고, 불가피한 분할 ZIP에 무결성 복원 도구 포함.

### v0.9.1
- 기준봉 PC 원본은 신규 BUY에 `3LEG31` 정책을 저장하는데 실제 APK D19 매수 경로에는 해당 초기화가 누락되어 `LEGACY`·목표 0으로 저장되는 결함 확인.
- 신규 D19 BUY의 3단계 정책·1차 목표가를 원본 거래 트랜잭션에서 기록.
- 2026.11.13 이후 원본 BUY가 확인되고 한번도 매도하지 않은 D19 보유종목 중 잘못 초기화된 대상만 제한적으로 정책 복구·감사기록. 기존 매수가·수량·체결행은 보존.
- 원본 D19 경로에서 1차 30%·2차 35%·잔량 고점추적 PAPER 체결 시험 통과.
- UI의 1차 목표 조회를 실제 `exit_first_target`/기준봉 상단 자료로 보완.
- 세로모드 왼쪽 순손익 금액 잘림 문제에 대해 손익 표시 줄을 분리.

### v0.9.2 — 이번 작업
- 주식 5전략과 코인 3전략에 공통 장애 시나리오 판정 구조 도입.
- Android에서 실제 인터넷 유효성을 확인하여 네트워크 장애와 키움·업비트 WebSocket/구독 장애를 구분.
- 주식 시세 처리 큐에 보유종목 우선 처리를 추가. 일반 신규 후보보다 먼저 처리하되 기존 시세의 거래소 시간·수신시간·3초 유효성은 유지.
- 연결복구·재시작·시세 지연·기준봉/1분봉 누락 시 각 전략의 기존 원장과 정책을 재평가하도록 시나리오 분류.
- 별도 상태 파일 `recovery_scenarios_v092.json` 및 전략별 화면에 복구 상태 표시.
- 세로모드 좌측 상단 `ARTITRADE` 오른쪽에 설치 버전명 `v0.9.2` 표시.
- 공통 판정 모듈은 체결을 만들지 않음. 실제 BUY/SELL은 원래 각 전략 엔진과 원장만 처리함.

## 3. 주식·코인 공통 매수/매도 시나리오

| 장애·운영 상태 | 매수 판단 | 매도 판단 및 복구 |
|---|---|---|
| 인터넷·거래소 연결·구독·시세 정상 | 원본 전략 조건과 실시간 근거가 일치할 때 실행 | 원본 손절→단계 익절→잔량 규칙을 원래 체결 엔진에서 처리 |
| 휴대전화 인터넷 끊김 | 새 체결 생성 금지 | 체결 근거가 없으면 SELL 생성 금지; 인터넷 복구 후 원장·현재 유효시세 재평가 |
| 거래소 WebSocket 장애 | 신규 진입 보류, 재로그인·재구독 | 보유종목 구독 우선 복원, 신규 0B/0D 또는 업비트 시세·호가 확인 |
| 개별 종목 REG·구독 누락 | 해당 종목 매수 보류 | 해당 보유종목 구독 재확인, 오래된 표시가격으로 체결 금지 |
| 시세 처리 큐 적체·3초 초과 | 지난 시세로 매수 금지 | 보유종목 신규 진짜 틱 우선 처리, 3초 넘은 틱으로 허위 체결 금지 |
| 앱 강제종료·휴대전화 전원 OFF | 비구동 중 신규 PAPER 체결 불가 | 재실행 후 원장·단계·미확정 주문 확인, 새 실시간 시세로 매도 조건 재평가 |
| 기준봉·1분봉 누락 | 해당 봉/지표 의존 신규 매수 보류 | 원본 정책상 해당 봉이 불필요한 보유 손절·익절은 현재 유효 시세로 우선 판단 |
| 원본 주문·체결 불확실 | 동일 UUID 중복매수 금지 | 원본 BUY/SELL·부분체결·잔량 확인 전 중복매도 금지 |
| 재연결 후 1·2차 목표 모두 충족 | 신규 매수보다 보유관리 우선 | 1차 원본 SELL 기록 확인→2차 SELL 기록 확인→잔량은 기존 3차 정책 유지 |
| 연결 장애 중에만 과거 목표 달성 | 지난 시점의 가상 매수 생성 금지 | 과거 봉 고저만으로 과거 체결 생성 금지. 복구 당시 현재가 기준 손절·익절 재판단 |
| 해당 거래소 휴장·장외 | 신규 진입 금지 | 거래 가능 시간의 신규 유효시세 대기; 코인은 별도 24시간 기준 |

공통 처리 순서: ① 원본 SQLite·BUY/SELL·매도단계 확인 → ② 인터넷·WS·종목구독·시장 시간 판정 → ③ 신선한 체결/호가·잔량 검증 → ④ 보유 손절·익절 우선 → ⑤ 원본 전략 BUY/SELL 실행 → ⑥ 실제 체결행·수량·실현손익 확인 → ⑦ 신규 진입·연구 평가.

주의: 원본 전략별로 필요한 기준봉, 1분봉, RSI 판정봉 등이 다름. 공통 복구 모듈의 시나리오 결정만으로 각 전략의 매매조건 충족 또는 체결 완료가 보장되는 것은 아님.

## 4. 코드·검증 위치

- `ARTITRADE_ANDROID/app/src/main/python/unified_recovery_v092.py` — 8전략 장애상태 분류, 보유 우선 시세 큐, 복구상태 저장
- `ARTITRADE_ANDROID/app/src/main/python/stock_at_policy_reconcile_v091.py` — 주식 D19 기준봉 신규 BUY 정책 및 검증된 기존 보유의 복구
- `ARTITRADE_ANDROID/app/src/main/python/android_runtime_v078.py` — 원본 엔진 연결과 Android 상태 전달
- `ARTITRADE_ANDROID/app/src/main/java/com/artitrade/mobile/UnifiedTradingService.java` — Foreground Service, 네트워크 유효성 수집
- `ARTITRADE_ANDROID/app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java` — Android↔Python 상태 전달
- `ARTITRADE_ANDROID/app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java` — 전략별 복구상태·세로 상단 버전·손익 표시
- `ARTITRADE_ANDROID/app/src/main/AndroidManifest.xml` — 회전 잠금 및 네트워크 상태 권한
- `ARTITRADE_ANDROID/ui_tests/test_v092_scenarios_8_wallets.py` — 장애 시나리오·큐·버전 표시 자동시험
- `ARTITRADE_ANDROID/ui_tests/test_v091_d19_original_at_reconnect_and_portrait.py` — 원본 D19 BUY/SELL 및 재연결 단계매도 시험
- `ARTITRADE_ANDROID/RECOVERY_EXECUTION_SCENARIOS_V092.md` — 시나리오 상세 기술문서

## 5. 검증 완료와 미검증 구분

검증 완료:
- GitHub Actions 44242964831 성공, 삼성 ARM64 APK 빌드 및 원본 인증서 확인.
- Android 15 에뮬레이터 실행·복귀·회전 시험 통과.
- 8전략 공통 장애 시나리오·보유종목 큐·표시 기능 자동시험 통과.
- 기존 주식·코인 전략 회귀시험 및 D19 원본 PAPER 체결 경로 시험 통과.

미검증 또는 구조상 제한:
- 사용자 삼성 휴대전화의 실제 보유종목 자동매도 성공은 확인되지 않음.
- 완전히 꺼진 휴대전화는 PAPER 엔진도 실행되지 않으므로 그 시간의 실시간 체결은 불가능함. 이를 보장하려면 상시 서버와 단일 주문권한/원장 동기화 구조가 별도 필요함.
- 지난 시점의 목표가 도달을 과거 실제 매도로 만들어내는 기능은 없음. 공식 과거 OHLCV는 호가 잔량 또는 당시 실행 증거가 아님.
- 과거 제공된 오류이력 ZIP 4개의 실제 SQLite·로그 분석은 완료되지 않음. ZIP 생성기 개선과 과거 장애 원인 규명은 별개임.
- 실기기 세로화면의 버전 표시·손익 줄바꿈·회전, 실제 API 단절 뒤 모든 전략의 복구 성공은 추가 검증 필요.

## 6. 후속 최우선 작업

1. 이전 첨부 오류 ZIP 4개의 SQLite/WAL·JSON·로그를 직접 열어 오류 발생시각 → WS/REG → 시세 큐 → 조건판정 → 원본 BUY/SELL을 시간순 대조할 것. 파일 문제와 분석 환경 장애를 구분할 것.
2. 실제 기준봉 보유 사례(매수가 2,600원·현재가 3,020원·76주)의 원본 `exit_policy`, `exit_first_target`, `exit_stage`, BUY/SELL을 실기기에서 대조할 것. 시험용 2,800원 목표를 실제 종목 목표로 단정 금지.
3. 8개 전략 각각 정상 연결, 인터넷 단절, WS/종목구독 누락, 3초 큐 지연, 앱 재시작, 기준봉/1분봉 누락, 부분체결, 복귀 후 다단계 목표 충족 상황을 실제 원본 체결 경로까지 검증할 것.
4. 종가베팅 CW 후보가 2026.11.13 이후 APK에서 0건인 현상을 PC EXE와 비교하고 종가 스캔 → 후보 → 매수대기 → 실제 원장 반영 순서로 조사할 것.
5. 주식·코인의 표시 현재가와 실시간 0B/0D/REST·업비트 WS 출처, 마지막 수신시간, API 호출제한을 조사할 것. 후속 고가추적·미매수 가상 목표달성 모니터가 과도한 REST를 유발하는지 확인할 것.
6. 마켓트렌드 RSI 판정봉이 미래 날짜로 표시되는 문제, 분봉 누락·GMO D/D+1 후보 만료, 수급매매 후보 실제 기록을 조사할 것.
7. 세로 손익 금액·상단 버전·오류 ZIP 500MB 분할·취소·원본 SQLite 복원·화면 회전 종료 여부를 삼성 실기기에서 검증할 것.
8. 비구동 중에도 매매를 원한다면, 기존 앱을 그대로 둔 채 별도 상시 실행 엔진 및 실행권한 단일화·동기화 방안을 설계할 것. 현 버전에서 완료됐다고 주장하지 말 것.

## 7. 개발 필수 준수사항

- 원본 SQLite/WAL, 보유종목, 매수가·수량, 체결이력, 실현손익의 임의 삭제·초기화·교체 금지.
- 실제 시세·호가·잔량 없이 체결 성공 표시 금지. 과거 봉 고가 도달만으로 허위 체결 생성 금지.
- 사용자 요청 없이 매수·매도 기준을 변경하지 말고 전략별 기존 정상 동작 유지.
- UUID·원본 SELL 기록으로 중복매도, 부분매도, 잔량 확인.
- 기존 패키지명·삼성 앱 서명 유지, APK 덮어 설치 가능 여부 확인.
- 자동화시험 통과와 실제 휴대전화 검증을 분리하고, 미확인 원인을 해결로 보고하지 말 것.
- 사용자에게 요청하지 않은 채팅 입력창 자동입력·자동 메시지 전송 금지.
- 다운로드 링크는 실제 Release 파일 존재 확인 후 제공.
- 최신 배포 소스는 위 기재한 커밋으로 고정해 추적할 것. 문서만 추가된 이후 커밋을 APK에 반영된 코드로 오인하지 말 것.

## 8. 인수인계 당시 최종 상태

- v0.9.2 APK 배포 및 CI 성공.
- 주식·코인 8전략 공통 장애판정·보유 우선 시세처리·복구 진단 추가.
- 실제 BUY/SELL의 권한과 원장 수량·손익은 기존 원본 엔진이 유지.
- 세로모드 상단 버전명 표시 소스 반영.
- 사용자 실기기 자동매도 재현 확인, 오류 ZIP 4개 분석, 전원 OFF 중 연속 자동매매는 미완료.
- 후속 개발자는 장애분류 기능의 존재만으로 매수·매도 성공을 단정하지 말고, 실제 데이터 수신부터 원장 SELL/BUY 반영까지 전략별로 검증할 것.


---

## 9. 상시 준수: 문제 설명은 반드시 해결에 필요한 자료와 확보 절차까지 기록할 것

적용 시작: 2026.11.18. 이후 ARTITRADE의 모든 개발 인수인계서, 오류 분석, PR, 릴리스 기록에 계속 적용하는 필수 규칙임.

- 단순히 "미완료", "확인 불가", "환경 장애", "해결됐다고 할 수 없음"으로 보고를 끝내지 말 것. **현상 → 확인된 증거 → 정확히 막힌 단계와 오류 → 필요한 자료·접근권한·실행환경 → 해당 자원을 실제로 얻는 방법(담당자와 순서 포함) → 확보 후 수행할 수정과 검증 → 통과기준 → GitHub 반영 경로**를 반드시 연결해 기재할 것.
- 이미 받은 ZIP·원장은 먼저 재사용하며 동일 파일의 재업로드를 쉽게 요구하지 말 것. 개발자는 우선 다른 접근 경로와 실행환경을 직접 시험하고, 사용자만 할 수 있는 작업이 남으면 해당 파일·버튼·위치·권한·입력순서를 명확히 설명할 것.
- 원본 SQLite/WAL, BUY/SELL, UUID, 보유수량, 매수가, 실현손익 보존. 소급 가상체결 금지. 사용자 승인 없이 전략 조건 변경 금지.
- 원본 오류 ZIP·DB에는 API 키, 계정 및 거래 정보가 포함될 수 있으므로 공개 GitHub 저장소·PR·Actions 로그·Release에 업로드 금지. 비공개 경유 저장소 사용은 접근 범위와 사용자 동의를 확인한 다음 처리. GitHub에는 비밀정보를 제거한 분석표와 수정 소스·테스트만 기록.
- 개발의 각 단계가 막히면 아래 표의 **필요자료/획득 절차/확보 후 실행 내용**을 최신 사실로 갱신할 것. 기존 인수인계서의 해당 절을 후속 버전에서도 유지할 것. 자동시험과 삼성 실기기 결과는 서로 바꿔 표현하지 말 것.

### 의무 보고 서식

| 항목 | 기록할 구체 내용 |
|---|---|
| 현상·영향범위 | 시장·전략·종목·발생시각·매수/매도/시세 구간 |
| 이미 확보한 근거 | 파일명·해시·로그 시간·원본 SQLite 테이블/키·소스 커밋 |
| 막힌 정확한 위치 | 수행한 명령/도구·실제 오류코드·용량/권한 제약 |
| 해결에 필요한 것 | 원본 자료, 계측, 실행환경, 권한, 단말 시험 중 무엇인지 |
| 필요한 것을 얻는 방법 | 개발자가 먼저 시도할 순서, 대체 경로, 필요하면 사용자에게 요청할 최소 행동 |
| 확보 후 개발·검증 | 원인 대조 → 최소 코드 수정 → 원장 보존 시험 → 단말에서 확인할 항목 |
| 통과 기준과 링크 | 실제 BUY/SELL 행, UUID·잔량·실현손익, PR/CI/배포 기록(배포한 경우만) |

## 10. 2026.11.18 후속 현안별 필요한 자료·확보 방법·실행계획

### 10.1 현재 첨부 오류 ZIP — 분석 경로 확보 방법

현재 채팅에 다음 자료가 제공되어 있음.
- PART_001_OF_002.zip: 494,459,745바이트
- PART_002_OF_002.zip: 175,089,401바이트
- 합계: 669,549,146바이트
- 원본 이름 공통 부분: ARTITRADE_ALL_ERROR_HISTORY_20261118_135428_28f9e7
- 과거 별도 오류 ZIP 4건과 이번 2개 분할의 포함관계·중복 여부는 내부 manifest와 해시를 실제 대조해서 판정할 것.

확인한 접근 제약: ChatGPT 파일 materialize의 파일 1개당 제한은 104,857,600바이트(100MiB)이며 두 ZIP 모두 초과함. Python 및 별도 컨테이너의 파일 접근 이전 단순 실행도 ClientError를 반환했음. 파일 목록/용량은 확인되나 현재 경로로 ZIP CRC·내부 SQLite·JSON·로그를 읽는 단계에 도달하지 못했음. 이 사실은 파일 손상 판정이 아님.

**필요한 것:** 첨부 ZIP의 실제 바이트에 접근 가능한 정상 코드 실행 환경, 압축해제용 저장공간, ZIP 인덱스/CRC, analysis/bundle_index.json(있다면), 원본 파일별 SHA-256, SQLite와 관련 WAL/SHM, JSON/로그.

**획득 순서:**
1. 개발자: 원본 채팅 첨부를 새로운 정상 컨테이너에서 우선 자동 마운트하고 해당 정확한 /mnt/data 파일 2개를 열어 파일 시그니처·목록·CRC와 크기를 확인. 사용자가 보낸 파일을 우선 재사용.
2. 개발자: ZIP이 열리면 경로 이탈/비정상 폭증 압축을 방지하면서 별도 디렉터리로 스트리밍 복원. 각 소스 파일 해시, SQLite/WAL 짝, 무결성을 검증하고 원본은 수정하지 않음.
3. 컨테이너 접근이 계속 차단될 때: 첨부파일과 외부 실행환경 사이에 원본 바이트를 넘길 수 있는 **접근 제한된 비공개 전송 경로**를 확보. 비공개 Drive·비공개 GitHub 저장소·전용 작업공간 중 사용 가능한 것을 개발자가 조사하고 사용 전 권한을 확인. GitHub Actions는 ChatGPT 채팅에 올라온 첨부 ZIP을 직접 내려받을 수 없으므로 **전송 경로가 확보된 뒤** Actions로 분석 실행. 사용자 조치가 반드시 필요하면 정확한 업로드 위치/접근권한/방법만 요청.
4. 최후 수단: 파일을 읽을 수 있는 별도 환경에서 각각 **95MiB 이하**로 분할해 비공개로 전달하고 조각 순서·원본 SHA-256을 검증하여 재조립. 파일 바이트를 읽지 못하는 현재 환경에서 개발자가 원본 ZIP을 분할했다고 주장하지 말 것.
5. 복원 후: SQLite·JSON·로그의 거래소시각/수신시각을 KST 기준으로 정렬하고 종목·전략·UUID를 기준으로 WS 인증/0B/0D/호가·구독 → 틱 큐 → 3초 시세 검증 → 매매 조건 → 실제 원본 BUY/SELL → 잔량/손익 순서의 오류 발생시간표 작성.
6. 결과물: 원인별 재현 증거, 필요한 최소 코드 변경, 회귀시험, GitHub PR. 원본 개인 ZIP을 공개 저장소에 올리지 말 것.

### 10.2 중복 제거한 후속 업무별 필요 증거와 확보·수정 방법

| 현안 | 필요한 자료 | 얻는 방법 및 그다음 실행 |
|---|---|---|
| 강제매도 반복 실패(주식 5전략·코인 3전략) | 사용자가 누른 요청 UUID, Service의 QUEUED/RUNNING/완료 기록, UI 오류코드, 당시 호가·잔량·시각, 해당 전략 원본 SELL/positions | ZIP의 artitrade_manual_sale_v085 결과·로그와 각 원장을 UUID/시간으로 결합. UI→Service→Python→전략 소유 SELL→원장 확정 중 최초 차단 위치를 찾아 수정. 차단 사유가 일반 ValueError로 표시되던 문제는 PR #47 수정 코드로 진단 가능하도록 개선. |
| 주식 기준봉 1·2·3차 자동매도와 BUY 시점 목표값 | D19 positions, v19_members, executions의 원본 BUY/SELL, exit_policy, exit_first_target, exit_stage, ref_low/ref_high/ref_bodytop, 당시 유효 0B/0D | 원본 DB 복제본에서 정책·수량·체결행 확인. 신규 D19 BUY의 1차 목표와 2차 기준(1차 목표×1.03)을 원본 감사기록에서 추적. 자동매도는 1차 SELL 확인 → 2차 SELL 확인 → 잔량 고점추적을 따로 검증. 이미 발생한 체결을 소급 제조하지 않음. |
| 키움 0B·0D 구독 누락, 큐 적체·3초 폐기, 주식/코인 현재가 지연 | WS 로그인/REG 상태, 종목 구독목록, 틱 거래소·수신·소비시각, 큐 길이·drop, UI 캐시 갱신시각 | 오류 ZIP 수신로그/복구상태 JSON에서 종목별 타임라인 작성. 필요한 항목이 없으면 읽기 전용 계측을 추가하여 3초 유효기간 내 처리 여부와 화면 표시 지연을 별도 분석. |
| 종가베팅(CW) APK 전환 후 2026.11.13 이후 후보·매수 0 | PC EXE 연구/후보/신호 기록, APK 스캔 시각·필터 탈락 사유·pending/BUY, 동일 날짜 원본 분봉 | 저장된 PC 원본 엔진과 APK 연결·스케줄 차이 대조. SCAN→CANDIDATE→PENDING→BUY의 최초 불일치 단계 결정. 기존 자료에서 PC 기록을 찾지 못할 때만 대상 파일/날짜를 특정해 추가 요청. |
| 주식 MA·마켓트렌드 매수 0 | 전략 시작 상태, 후보/탈락 카운트, MA 분봉·가격 증거, RSI20 판정봉, 잔고·원장·호가 | ZIP 연구 DB·진단 카운터에서 SIGNAL→ENTRY GATE→ORDER→BUY 흐름을 정량화하여 차단 이유 확인. 모호하다고 매수기준을 완화하지 말 것. |
| 고점추적·미매수 가상감시의 API 제한 가능성 | REST endpoint별 호출 수/상태코드(429 등), WS 구독 수, 후보 수·폴링 간격 | 기존 로그 합산, 자료가 없으면 호출 카운터와 시간별 수집 계측 추가. 잦은 조회가 보유매도 경로를 지연시키는지 검증 후 호출 최적화. |
| 마켓트렌드 RSI 미래 날짜, 기준봉/1분봉 복구 | 거래소 공식 확정봉 시각, UTC/KST 변환 경로, RSI 판정 레코드, backfill 인덱스 | 잘못 표시된 레코드를 실제 날짜로 재현 후 타임존/종료 경계 수정. 공식 봉 복구가 실제 각 전략 진입·매도 판단으로 연결되는지 회귀시험. |
| 삼성 실기기 화면 회전·세로 손익·버전·ZIP 추출·원장 복원 | 실기기 모델/Android 버전, 화면/추출 상태, ZIP manifest·SHA, 설치 전후 원장 행수 | 기존 서명 APK 덮어 설치(앱 삭제·데이터 초기화 금지), 재현 단계별 상태 수집. 복원은 원장 복제본에서 점검하고 원본 수량·매수가·손익·체결내역을 비교. |
| 전원 OFF 중 연속 자동매매 | 24시간 호스트, 단일 실행권한(lease), 원장 동기화·재시작/분할 장애 정책 | 서버·실행권한 담당을 먼저 정하고 별도 설계 PR. 폰 전원 OFF 기간에 과거 봉만으로 PAPER 체결 생성 금지. |

### 10.3 다음 작업 순서와 갱신 책임

1. ZIP 실바이트 접근 가능한 경로 마련 및 원본 해시 검증.
2. 실제 오류기록의 SQLite·JSON·로그 시간순 통합. 강제매도/자동매도 원인 우선 확인.
3. CW·주식 MA·마켓트렌드 매수 0 원인 분리, 이후 데이터 수신·봉 복구·UI 지연·API 호출 문제 순서로 수정.
4. 원본 임시 DB 회귀시험 → 전체 8전략 시험 → 삼성 실기기 증거 대조 → 실제 배포가 필요한 경우에만 기존 서명·패키지 유지한 APK 생성.
5. **작업할 때마다 이 문서의 현안별 필요자료, 얻는 방법, 확보 후 실행, 커밋/PR/Actions 링크를 업데이트**할 것. 인수인계 이후에도 이 절을 삭제하지 말 것.

### 10.4 2026.11.18 GitHub 수정·검증 기록

- 배포 기준: v0.9.2 VersionCode 118, 원본 소스 ad68fcf674bcb2c149b3e2380d27d9c2dd7c3583. 현재 배포 APK와 후속 소스 변경은 구분함.
- 개발 PR #47 (초안): https://github.com/OHM1121/RESALE_ALPHA/pull/47
- 변경: android_manual_paper_sell_v085.py에서 원본 차단코드 전달. stock_at_policy_reconcile_v091.py에서 D19 신규 BUY 정책 초기화 시 1차 목표·2차 기준을 원본 감사이력에 보존. 실제 체결/조건·UUID 처리 방식은 변경하지 않음.
- GitHub Actions 회귀시험: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44251287333 (성공). 임시 D19 원장에서 수동 SELL·원장 손익·동일 UUID 2차 실행 차단 시험, 기존 3단계 매도 시험 통과.
- 후속 실기기 실패원인 특정에 필요한 바로 다음 행동: 위 10.1절대로 원본 ZIP 파일 바이트를 실행환경에 연결하고 UUID·호가·원장을 실제 시간순 대조.


## 11. v0.9.3 ZIP95 / 분석 JSON 우회 경로 — 후속 담당자 필수 인계

개선 요구: 오류·연구 ZIP이 ChatGPT 도구의 100MiB 파일복사 한도를 초과한 점과, 100MiB 미만에서도 Python/컨테이너 실행이 ClientError로 실패할 수 있는 점을 별도 장애로 처리할 것. APK가 원격 ChatGPT 실행 서비스의 ClientError를 제거한다고 주장하지 말 것.

실행 가능한 해결 구성:
1. APK의 통합 오류이력·주식 오류이력·코인 오류이력·통합 연구이력·기존 전략별 연구/오류 추출 버튼에서 동일한 95,000,000바이트 이하 파트 생성기를 사용하도록 일원화. 단일 원본 SQLite가 크면 그 원본을 잃지 않고 파트 인덱스대로 자동 분할.
2. 각 ZIP의 source SHA-256, ZIP CRC, ZIP SHA-256, 동일 bundle_index.json 및 analysis/restore_parts.py 유지. 저장된 Android SAF 문서를 재열어 바이트 수·해시를 확인하며 검사 실패 시 신규 파일을 정리.
3. 같은 저장 폴더에 2,000,000바이트 이하 독립 JSON인 ARTITRADE_SCOPE_..._ANALYSIS_READY.json 생성. 원본 파일명·크기·해시·SQLite 테이블별 건수·앱 네이티브 런타임 장애 및 각 ZIP 파트의 해시를 포함. 이 JSON은 파일 분석 코드 실행 없이 텍스트로 읽어 초기 진단할 수 있음. 원본 BUY/SELL·세부 로그의 전체 내용은 분할 ZIP에 남아 있음.
4. 기존 494,459,745바이트 + 175,089,401바이트 업로드 자료는 새 APK 설치만으로 바뀌지 않음. **해당 오류이력 분석을 진행하려면** 기존 앱 데이터를 삭제하지 말고 같은 패키지·기존 서명의 새 APK를 덮어 설치한 다음 앱의 '통합 오류 추출'에서 새 폴더를 선택하여 ANALYSIS_READY.json과 모든 PART ZIP을 다시 추출해야 함. 우선 JSON만 별도 첨부해 네이티브 오류·테이블 현황을 파악하고, 나머지 ZIP 파트를 순서대로 첨부하면 원본 SQLite/JSONL 전체를 복원할 수 있음. 기존 대용량 파일은 훼손·삭제하지 말 것.
5. 파일 크기를 줄여도 ChatGPT 실행 환경이 계속 ClientError를 내면 파일 자체 손상으로 단정하지 말고, 먼저 단독 JSON을 파일 서비스의 텍스트 읽기 경로로 열어 분석. 원본 로그나 행 단위 검증이 필요하면 정상 실행 컨테이너 또는 제한된 비공개 파일전달 경로를 확보해 분할 ZIP을 복원. 문제 보고에는 항상 필요한 증거 및 획득 절차를 구체적으로 붙일 것.
6. signed APK GitHub Actions에서 반드시 기존 서명 37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6과 package com.artitrade.unified.s21dex, versionCode 119를 확인해야 함. GitHub 기본 브랜치 cache 키가 동일해도 **29db005a...**의 다른 키를 복원할 수 있으므로 초기 인증서 검증에서 차단하도록 유지. v0.9.2 릴리스가 실제 성공한 GitHub Actions의 원래 서명 캐시 브랜치는 artitrade-phone-main-v070-live-orders-20261112임. 다르게 서명된 APK 배포 금지.

코드: ARTITRADE_ANDROID/app/src/main/python/android_research_bundle.py, app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java.
테스트: ARTITRADE_ANDROID/ui_tests/test_v101_zip95_analysis_ready.py 및 기존 v088/v090 원본 복원 회귀시험.
PR: https://github.com/OHM1121/RESALE_ALPHA/pull/48
서명·빌드 검증: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44254864596 (최종 상태·릴리스 자산은 실제 확인 후 기재/보고).
인수인계 시 이 절의 '필요한 자료 → 얻는 방법 → 확보 후 수행' 정책을 계속 유지할 것.


### 11.1 검증된 APK 릴리스 및 ZIP 재추출 순서

- 발행 리포트(성공): https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44254864596
- 출시 ZIP95 테스트 APK: https://github.com/OHM1121/RESALE_ALPHA/releases/tag/ARTITRADE-v0.9.3-ZIP95-20261118
- 직접 APK: https://github.com/OHM1121/RESALE_ALPHA/releases/download/ARTITRADE-v0.9.3-ZIP95-20261118/ARTITRADE_UNIFIED_v0.9.3_ZIP95_20261118.apk
- APK 30,393,465바이트, SHA-256 59f53fcc613fa878038f76c138f9a30cf9267c78b8209a259fcf38219e00095b
- 패키지 com.artitrade.unified.s21dex, versionCode 119, versionName 0.9.3-ZIP95-ANALYSIS-READY, 기존 원본 서명 SHA-256 37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6
- 기존 v0.9.2와 같은 앱을 **삭제하지 않고 덮어 설치**. 앱에서 통합 오류이력 추출 → 폴더 선택 → 생성된 모든 95MB 이하 PART ZIP + 작은 ANALYSIS_READY.json 가운데 JSON부터 진단에 이용.
- 검증: GitHub Actions 전체 완료, 새 ZIP95/독립 JSON, 기존 손상 없는 SQLite 분할 복원 및 원본 전략 67개 단위시험 통과, 서명과 APK 패키지 검증 통과. 정상 휴대전화의 실제 PAPER 거래 체결을 시험한 것으로 오인 금지.
- **ZIP 원본 분석에 필요한 다음 구체 행동:** 새 버전 APK에서 통합 오류이력을 다시 추출하고 작은 ANALYSIS_READY.json을 먼저 분석에 제공. 원본 BUY/SELL 시간대는 SHA 검증된 모든 PART ZIP을 사용해 조사. 외부 ChatGPT ClientError 발생 시에도 별도 JSON 텍스트 내용으로 초기 장애 진단 후 ZIP 분석 환경을 복구할 것.


## 12. ZIP 데이터 분석 접근 실패 재발 시 책임이행 규정 (2026.11.18)

핵심: GPT에서 ZIP 바이트를 열고, 원본 SQLite/JSON/로그를 분석하고, 원인별 수정 코드를 GitHub로 넘기는 방식은 기술적으로 가능함. 과거 실제 제한은 파일 1개당 materialize 100MiB와 독립적 Python/container 실행환경 ClientError였음. 95MB 이하 ZIP 분할은 전자의 대응책이고, ChatGPT 외부 코드 실행환경 자체의 ClientError를 APK가 수정하지 못함. 별도 ANALYSIS_READY.json 파일은 현재 소스 목록/해시/SQLite 테이블 건수/일부 Android 오류만 포함하므로 전체 거래 상세 로그와 동일하지 않음.

재발 시 "못 읽음"만 보고하고 종료하지 말고 아래 단계의 실제 결과물을 남길 것.

1. 실패지점 재현·분리: 업로드 원본 SHA·크기, Files materialize 거부, ZIP CRC/형식 오류, Python/container ClientError 중 무엇인지 원문 오류와 시도 절차를 한 번에 기록. ZIP 손상과 실행환경 고장을 혼동하지 말 것.
2. 우회 경로 즉시 선택: 95MB ZIP의 raw_file 진입을 시험. 코드 실행환경이 단순 출력에서도 장애이면 동일 명령을 계속 반복하지 말고 별도의 텍스트(JSON/JSONL/CSV) 읽기 경로 사용. 현행 ANALYSIS_READY.json만으로 원본 전 행 분석이 불충분하면, 앱에서 원본 오류·전략 판단·체결 증빙을 읽기 전용으로 조회해 수 MB 단위의 독립 평문 JSONL로 자동 분할 출력하는 기능을 개발하여 GitHub PR/CI/서명 APK로 검증. 개인·거래·API 인증정보 공개 전에 필드 단위 마스킹과 경로 분리. 원본 DB나 거래체결 변경 금지.
3. 실제 분석 결과 확보: ZIP CRC, 모든 원본 SHA 및 SQLite quick_check, WAL 포함 여부, 발생시각(KST/UTC), 전략/종목/UUID, 0B/0D·호가/큐/신호/BUY/SELL/수량을 대조해 구체적인 첫 차단 지점 작성. 증거 누락 시 필요한 추가 계측 소스와 추출 방법까지 지정.
4. 코드 수정·시험: 확인된 최초 실패 위치의 최소 변경, 동일 원장 복제본 회귀시험, 기존 전체 전략 시험, GitHub PR/CI/배포 링크를 기록. 실기기 미검증 상황이면 필요한 단말 검증 절차를 제공하되 모의시험을 실거래 증명으로 쓰지 말 것.
5. 의무 보고: 현상/확인근거/실제 원인/필요자료/얻는 방법/해당 차단을 제거한 코드/시험 결과/추가 검증 절차를 하나의 보고로 전달하고 최신 인수인계에 누적할 것.
6. 책임 범위: 기술적인 분석/코드 수정/재발방지 시험/명확한 증빙 제공에 대한 책임은 수행. 플랫폼 실행환경 복구·금전배상·구독료 환불 권한이 없으면 그 권한이 있다고 주장하지 말 것. 비동기 작업을 자동 완료한다고 약속하지 말 것.

향후 인수인계 버전에서 이 절을 유지하며, 새로 확인된 제한/우회수단과 PR/CI/릴리스 증빙을 계속 갱신할 것.

## 13. 앱 버전명·화면 표시 고정 규칙

- 사용자 지시: 버전명 외 추가 문구를 표기하지 말 것. 설치 APK의 Android versionName은 숫자만 사용 (이번 수정: 0.9.3). 예: 0.9.3-ZIP95-ANALYSIS-READY 형식 금지.
- 앱 상단 표시 문자열은 v0.9.3 단독. 시장명(통합/주식/코인), 기능명, ZIP/분석상태 및 기타 부연 문구를 같은 버전 라벨에 추가하지 말 것.
- 버전Code는 기존 앱 덮어설치를 위해 내부적으로 증가 가능하지만 화면에 노출하지 말 것.
- 회귀시험 test_v102_plain_version_label.py 및 Android aapt badging에서 versionName 정확히 0.9.3 확인 후 기존 서명으로만 릴리스. 이후 버전에도 접미어 없이 버전번호만 유지할 것.
- 관련 검증 워크플로: .github/workflows/artitrade-v102-version-only-original-signer.yml (기존 서명 권한 브랜치에서 실행).


## 14. 2026.11.18 원본 ANALYSIS_READY.json 실제 읽기와 v0.9.4 OOM 대응

- 업로드된 ARTITRADE_ALL_ERROR_HISTORY_20261118_160809_5d193d_ANALYSIS_READY.json (107,955 bytes)은 **Files 텍스트 경로에서 직접 열어 분석 성공**. 이전 90,947,994바이트 PART_001 파일도 Files raw_file materialize는 성공했으나 코드를 실행하는 /bin/true 명령 자체가 caas.internal.errors.ClientError로 실패. 이는 ZIP CRC/복잡도/파일 크기 문제로 증명되지 않으며 ChatGPT 도구호스트 내부 진단로그 없이는 실행환경 ClientError의 최종 인프라 원인 특정 불가. 앱의 Java OutOfMemoryError와 혼동 금지.
- JSON에 8개 ZIP 파트, 40개 원본 소스파일, SQLite/JSON 총 3,291,157,900바이트 및 앱 Native runtime_faults 30건 확인. 자료에 로그가 존재하면 ZIP 내부를 읽지 않고도 부분적인 실제 코드 결함은 확인 가능.
- 가장 구체적인 native 오류: UnifiedTradingService.scheduleUiRecordsIfDue의 JSONObject.toString()에서 75,497,480바이트 추가 할당 실패 (Java 힙 한도 256MiB), 기존 크기 제한 검사는 문자열 생성 이후. UnifiedVisualActivity.refresh에서 과대 JSON 파싱 중 OOM도 보고됨. 이 상태가 앱 강제 종료 및 시세/주문 판단 지연에 영향을 줄 수 있지만 개별 체결 인과관계를 원장 없이 단정 금지.
- 실기기 기록에는 manual_paper_sell_rejected에서 D버전 계좌/주문 API 차단, FRESH_STOCK_TRADE_MISSING, FRESH_COIN_ORDERBOOK_MISSING, COIN_MANUAL_FILL_NOT_RECORDED이 서로 다른 시점의 버전에 걸쳐 보고됨. 임의로 시세 3초 안전조건 해제하거나 가상체결 생성 금지.
- 이전 combined_error_export에는 upbit_paper.sqlite3 SQLITE_BACKUP_STALLED 두 건. 이번 8개 ZIP이 손상되었다는 증거가 아니므로 ZIP 분할/원장 보존을 유지.
- 수정 PR #49: https://github.com/OHM1121/RESALE_ALPHA/pull/49
- 실제 오류기록 근거 분석: https://github.com/OHM1121/RESALE_ALPHA/blob/fix-artitrade-v104-json-memory-original-data-20261118/ARTITRADE_ANDROID/FORENSIC_20261118_RUNTIME_MEMORY_AND_EXPORT.md
- 배포 v0.9.4: https://github.com/OHM1121/RESALE_ALPHA/releases/tag/ARTITRADE-v0.9.4-20261118
- GitHub Actions 성공: https://github.com/OHM1121/RESALE_ALPHA/actions/runs/44260046811
- APK (원본 서명 SHA-256 37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6, 패키지 com.artitrade.unified.s21dex, versionCode 121, 사용자 버전명 정확히 0.9.4): https://github.com/OHM1121/RESALE_ALPHA/releases/download/ARTITRADE-v0.9.4-20261118/ARTITRADE_UNIFIED_v0.9.4_20261118.apk
- 근본적인 UI 메모리대책: 화면 갱신 전 Python 단계에서 read-only view 목록/문자열을 제한해 Java 전송. 첫 앱 시작 시 full hub_records JSON을 SharedPreferences XML에 통째로 저장하는 경로 삭제. 이전 대형 화면 캐시를 파싱 전에 거절. 원본 source SQLite/WAL, 연구이력 ZIP, 실제 전략 정책, 가상매매 체결행은 변경하지 않음. PC 전송 시 화면용 일부 데이터를 완전한 원장으로 위장하여 전송하지 말 것.
- **실기기 설치 후 확인:** 기존 앱/DB 삭제 금지. 인스톨 업데이트 후 UI 새로고침 반복 시 Java heap/OOM/앱종료 여부를 관찰. PC 전송 기능 사용 중 OOM 발생 시 별도의 full sync 크기 검증/청크 스트리밍 과제가 필요. 모든 전략의 실제 진입·자동매도 실패 원인을 확정하려면 원본 SQLite 행의 오류시간 타임라인과 비교해야 하며, 현재 FILES 텍스트 분석은 메타/네이티브 오류자료임.
- **시간 손실/보상에 대한 사용자 요청:** ChatGPT 도구호스트 ClientError와 반복적인 분석 약속은 서비스 장애와 불완전한 안내의 독립 항목으로 기록. 기술적 대응은 코드 수정/검증/실패 근거 공개로 수행하되 모델이 금전 환불을 직접 실행할 권한이 있다고 주장 금지. 환불·피해보상 심사는 OpenAI 고객지원으로 접수; 사용자 시간을 임의 금액으로 산정하거나 환불 보장 금지.

이 절을 후속 개발·인수인계에서도 계속 보존하고 "오류 근거 → 필요한 자료 → 획득 경로 → 소스 패치 → CI/실기기 검증" 보고 형식을 유지할 것.
