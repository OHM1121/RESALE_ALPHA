# ARTITRADE · 신규 PAPER 원장 완전 초기화 개발 기준 (2026-11-18)

작업 상태: PR #51 소스 수정 / APK 미제작 / 실제 휴대전화 초기화 미실행

## 1. 사용자 확정 결정 (최우선)
- 기존 주식 5개·코인 3개, 총 8개 전략의 PAPER 보유·주문·매수·매도·손익·가상·시장지표·연구·오류·화면캐시·과거 PC 이력은 **전량 폐기**함.
- 기존 원장 보유종목의 매도 보호, 원장 승계, 일부 과거 연구 복구는 **개발 목적에서 제외**함.
- 새 APK의 **최초 휴대전화 실행에 한 번** 새로운 PAPER 원장을 시작함. 예전 기록 유지를 선택하는 우회 버튼은 없음.
- **신규 원장을 만드는 것은 코드의 개발·수정이 불가능해진다는 뜻이 아님.** 다만 과거 체결 증거는 초기화 후 유실되므로 구형 호환성 코드 및 새 체결 스키마를 초기화 전에 확정하는 것이 타당함.
- LIVE 거래/계좌·API 인증정보·운용금액·최대보유 설정·외부 별도 백업 ZIP은 초기화 대상에서 제외함. 실제 LIVE 주문 증거 발견 시에는 안전하게 원장 삭제를 거부함.

## 2. 새 앱이 휴대전화에서 실행하는 순서
1. UnifiedVisualActivity.onCreate에서 FreshEpochGateV106.mandatoryFreshStartPending 확인
2. 이전 마커가 없거나 PREPARING이면 일반 UI와 UnifiedTradingService 시작 금지
3. 전용 화면에서 기존 8개 PAPER 원장 모두 새로 시작함을 표시
4. 이전 서비스/Python 엔진이 살아 있으면 삭제 시도 금지. 완전 종료 후 재실행 요구
5. Chaquopy로 fresh_epoch_reset_v106.reset_once_json 호출. 삭제 가능 경로 전체 삭제, 외부 ZIP 및 키/운용설정 보존
6. ARTITRADE_FRESH_EPOCH_V106.json의 COMMITTED 원자적 기록 확인
7. 과거 로컬 UI·수동매도·PC 승계 캐시 삭제, PC 실시간 동기화 중지
8. Activity 재구성 후 8개 원본 엔진 신규 생성 및 정상 실시간 데이터 수신 시작
9. COMMITTED를 다시 실행하면 ALREADY_COMMITTED를 반환하고 새 원장 기록은 삭제하지 않음
10. 중간 실패·불완전 마커·실거래 기록 발견 시 fail-closed: 과거 PAPER 복구 또는 신규 주문 개시 금지

## 3. 과거 코드 삭제 완료
- LegacyLedgerMigration.java: 신형 원장에 구 PC ZIP 승계가 필요 없고 서비스의 모든 해당 실행경로를 FreshEpochGateV106으로 대체함에 따라 파일 삭제
- legacy_ma_exit_watch_v094.py: 과거 v061/v062 보유종목의 매도 감시만 수행하므로 설치 코드와 파일 삭제
- stock_manual_v086.py: 과거 MA 장부 탐색 경로 삭제하고 **현재** MA 원장만 수동매도 대상으로 한정
- UI: 과거 PC 승계 재검증 버튼/진행표시 제거
- 주식 AT, MA, CW, GMO, Trend 및 코인 General, MA, Trend의 **현행 원본 매매 엔진·매도정책은 유지**. 이름에 LEGACY가 포함되더라도 현행 D19 및 MA 정책이 참조하는 코드까지 임의 삭제하지 않음

## 4. 주식 미매수 오류 확인·수정
- MA: 기존 v067 상승전환 워커는 매수대기 후보 등록 직후 `seen.add(key)`를 실행했기 때문에 첫 원본 on_tick이 미체결 0건을 반환하면 10~2초간 새 유효 호가가 도착해도 같은 봉의 매수를 재시도하지 못했음. **원본 BUY 체결 성공 시에만 seen에 등록**하도록 수정. 기존 KRX 0B/0D, 가용 잔량, 상한가격·수수료·자금·최대보유 검사 자체는 유지
- Trend: 기존 RSI20 최근 봉 데이터 프리패치가 완성 21봉 이후 최대 45초 갱신 간격이어서 새 확정봉/다음 봉 시작 10초 규칙과 충돌했음. 신규 10초 동안 기존 KRX 검증 로컬 SQLite 또는 native Upbit 확정봉 캐시에서만 제한적 재조회하도록 보완
- Trend: KRX 60분봉 마지막 30분 구간의 시작시각을 finish-3600으로 역산하던 오류 수정. 실제 KRX 세션 시작으로부터 봉 시작 계산
- Trend: 새로 검증된 호가로 원본 PAPER BUY가 미체결인 경우에만 10초 남은 시간 동안 재평가함. 거래소 LIVE 미확인/위험 상태는 중복 주문 금지
- Trend UI: 진행봉 RSI<=35 'WATCH'는 매수후보가 아니므로 '사전관측(매수후보 아님)'으로 명확히 표기. 실매수 기준은 최근 10개 확정봉 중 최초 30미만 + 다음 봉 시작 후 10초 이내

## 5. 새 원장 데이터 및 증거
- execution_entry_plans_v107.sqlite3: 8개 전략의 확인된 매수근거 및 후보 목표가 기록. 즉시 확정 불가능한 매도목표가는 NULL
- 현행 원본 지갑 SQLite: 실제 거래소 시세 및 유효 호가/잔량에 기초한 PAPER BUY/SELL만 기록
- 오류/연구 ZIP: 기존 연구/오류 기록은 무손실 DEFLATE로 분할 추출 가능하며 새 원장 초기화 이후에는 새 기록부터 누적됨
- AI 분석 유지, 연구 전용 하단 탭과 불필요한 사전계산 제거
- 과거 PC ZIP 자동 재유입, 외부 백업 ZIP 자동 삭제, 과거 원장 수동 복원 코드는 실행하지 않음

## 6. GitHub ZIP ↔ ChatGPT 읽기 기능
- .github/workflows/artitrade-v108-gpt-zip-bridge.yml 추가
- GitHub Actions에서 release_tag+release_asset_zip 또는 repo_zip_path 선택 시 기존 ZIP을 읽어 `SOURCE_MAP.md` 및 `SOURCE_EXCERPTS.txt` 생성
- 파일목록·소스 발췌를 Actions 로그와 `ARTITRADE_GPT_ZIP_SOURCE_MAP` 아티팩트로 게시하여 GitHub 연결 상태에서 GPT가 텍스트로 검사 가능
- 보안 관련 파일명/키 입력 라인 발췌 제외, 압축 폭탄/과도한 파일 차단, 실행파일·APK 빌드 없음
- 자체 ZIP 샘플 2개 텍스트 파일 변환 및 GitHub artifact 업로드 성공. 실제 대형 ZIP과 파일별 접근은 별도 실물 테스트 필요

## 7. 검증 수준·남은 불확실성
- 신형 PAPER 초기화 임시 SQLite 시험 통과: 8개 PAPER 원장 전량 삭제, 운영설정/외부 ZIP 유지, LIVE 차단, 새 원장 중복 삭제 방지
- Android Java 컴파일 전용 Gradle 검사 성공: `compileUnifiedDebugJavaWithJavac`, APK 파일 생성 없음
- MA 재시도/Trend 10초 봉/압축 파일 소스 회귀시험 통과
- **실제 삼성 휴대전화의 신규 초기화, 8개 전략의 거래소 호가 수신, MA·Trend 신규 체결, 자동매도, CW 0건 원인, 키움 0B/0D 지연, QUID 과거 RSI 정확성은 확인 전임.** 소스 시험을 실기기 검증으로 과장하지 않음

## 8. 배포 금지 상태
- 사용자 명시적 추가 허가 전 APK 조립·서명·Release 발행 금지
- 원래 v0.9.5 휴대전화 설치본과 원장은 현재 변경되지 않았음
- PR #51 Draft 상태 유지. 신규 APK 제작 요청이 있으면 먼저 새 원장 필수 변경사항 및 Java 컴파일·소스 테스트 전체를 확인하고 사용자 승인에 따라서만 진행
