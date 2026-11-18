# ARTITRADE 0.0.2 개발 소스 · 원장 역행 방지 / ZIP 진단 (2026-11-18)

## 원장 재발방지 정책
현재 출시 APK 0.0.1에는 아래 신규 0.0.2 방어코드가 포함되지 않음. 0.0.2 APK 조립·배포는 사용자 별도 지시 전에는 수행하지 않음.

1. **최초 0.0.1 PAPER 초기화는 정확히 1회:** `ARTITRADE_FRESH_EPOCH_V106.json`이 COMMITTED이면 향후 매수·매도 기록 유지. 이후 마커가 사라지거나 다른 epoch_id로 교체되어도 *재초기화를 수행하지 않고 차단*함. 별도 Android SharedPreferences에 완료된 epoch_id를 유지.
2. **과거 PC 원장 재유입 경로 제거:** 서비스의 `APPLY_PC_MIGRATION` 분기, `checkPendingPCArchive()` 폴링, 15초 주기 검사·엔진 재시작 경로 삭제. 더는 구 PC ZIP을 찾거나 원장을 재승계하지 않음.
3. **원장 외부 독립 고수위점:** 앱 내부 `files/ARTITRADE_LEDGER_GUARD_V002.json`에 현재 epoch_id 및 주식·코인 8전략 원본 SQLite `executions/fills`의 건수·최대 rowid·최초/마지막 체결 식별정보를 fsync+원자 교체로 보관. 이 파일은 `files/ARTITRADE_PHONE_MAIN_V070` 폴더 바깥에 있어 구 원장 폴더 교체가 체크포인트를 덮어쓰지 못함.
4. **읽기 전용 검증:** SQLite를 `mode=ro`로 열어 WAL에 반영된 확정 체결을 검사함. 거래 내역 변경, 파일 복구, 삭제, 구 에포크 체결시각 재등장, 체크포인트 삭제·변조·역행은 모두 신뢰할 수 없는 상태로 간주함.
5. **거래 우선 차단:** 서비스는 전략엔진 시작 *전에* 체크하고 실행 중 약 30초 간격으로 별도 모니터 스레드에서 검사함. 역행 감지 시 모든 전략의 신규 실행을 중지하고 차단 원인만 로컬 기록. 자동 복원·자동 삭제·가짜 체결을 절대 수행하지 않음.
6. **안전 경계:** SQLite의 정상적인 새 체결은 허용되며 영속 고수위점이 증가함. 실거래 체결·브로커 주문·키·투자 설정을 조작하지 않음. 어떤 검사도 앱 데이터 전체 삭제/이전 백업 완전 덮어쓰기까지 막는 것은 아님. 기기 외부 별도 백업은 계속 보관해야 함.

## CLIENTERROR를 재현하려고 대용량 ZIP을 다시 올리지 않음
- 이전 ChatGPT 클라이언트에서 ZIP 업로드가 CLIENTERROR로 실패했다면, **그 ZIP을 다시 올려 달라는 안내는 부적절함**. 업로드가 되지 않는 파일을 이 채팅 안에서 읽을 수 없음.
- ARTITRADE 0.0.1 리서치·오류이력 내보내기에는 `*_ANALYSIS_READY.json`라는 작은 별도 진단 파일이 생성될 수 있음. 이 파일이 있으면 ZIP이 아닌 이 JSON의 내용만 제공하면 됨.
- JSON이 없거나 ZIP 자체가 손상됐다면 Windows 컴퓨터에서 GitHub의 `ARTITRADE_ANDROID/tools/ARTITRADE_ZIP_CHECK.ps1` 및 `ARTITRADE_ZIP_CHECK.cmd`를 같은 폴더에 내려받은 후 ZIP을 CMD 위로 끌어 놓기.
- ZIP 옆에 `원래파일명.zip.DIAG.txt`가 생성됨. 원본 ZIP은 읽기만 하며, 파일 내용을 공개 GitHub에 올리거나 원본 SQLite를 복사·수정하지 않음. 텍스트는 로컬 해시, 압축파일 목록 통계, ZIP 열기 실패 유형, 부분검사 여부만 담음.
- 이 작은 DIAG.txt 내용을 채팅에 **붙여 넣는 것으로도 분석 시작 가능**. ChatGPT의 ZIP 첨부 업로드를 다시 시도할 필요 없음. 간단한 로컬 진단은 ZIP central directory 및 최대 512MiB 스트림을 검사하며, 모든 데이터의 CRC/SQLite 무결성까지 보증하는 것은 아님.
- CLIENTERROR가 ChatGPT 서비스·브라우저·네트워크에서 발생하는 경우 로컬 ZIP이 정상이더라도 오류가 발생할 수 있으므로, 위 도구는 업로드 자체를 고치는 것이 아니라 *업로드를 우회*하는 수단임.

## 검증
- `test_v002_ledger_rollback_guard.py`: 8전략 기준, 정상 신규 매매, DB 되돌리기, 동일 건수 체결 교체, DB 삭제, 체크포인트 실종, 구 거래일 복원, 원장 비변경 검증
- GitHub Actions: `artitrade-v002-rollback-guard-no-apk.yml` (Python/Android Java 컴파일·기존 초기화 회귀시험, APK 미제작)
- `artitrade-v002-local-zip-check-no-upload.yml`: 정상 ZIP/손상 ZIP의 로컬 진단 결과 비교 (실제 사용자 ZIP 사용하지 않음)
- 삼성 휴대전화 실제 rollback 재현 시험은 수행하지 않음.
