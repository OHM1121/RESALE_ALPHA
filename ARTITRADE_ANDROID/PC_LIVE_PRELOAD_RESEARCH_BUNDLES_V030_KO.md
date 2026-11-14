# ARTITRADE v0.7.30 — 코드 전용 작업 (EXE·APK 미제작)

기준: `artitrade-v029-research-swipe-code-only-20261114`.

## PC와 실시간연동 (별도 ON/OFF)
- 휴대전화 앱 설정(☰)에서 **PC와 실시간연동 OFF · 시작** 선택 시 시작함.
- 휴대전화 내부 SQLite/JSON 수집과 매매는 별도로 유지됨. PC 연동 OFF는 로컬 매매/저장을 멈추지 않음.
- ON 시 최초 화면/연구/체결 원장 JSON을 MSERVER에 게시하고, 이후에는 변경된 버킷만 갱신함. 기존의 4.5초마다 반복되는 자동 게시를 OFF 상태에서는 하지 않음.
- MSERVER `GET /v1/records/index`는 버킷별 revision만 전송하며, PC 수신기는 변경된 버킷만 `GET /v1/record`로 내려받음.
- 수신 대상은 PC 별도 출력 폴더의 JSON. 원래 Windows EXE SQLite를 직접 수정/덮어쓰지 않음. 매매 규칙/실거래 활성화는 전혀 연동되지 않음.
- **주의:** 실시간 수신은 화면/요약용 원장 JSON 버킷(기존 최근 3,000행 창 포함)이며, 과거 모든 원본 DB 행의 전체 복제는 아님. 모든 후보/원본 테이블은 통합 연구이력 ZIP으로 별도 추출해야 함.
- PC 프로그램 수신 도구: `ARTITRADE_ANDROID/pc_tools/artitrade_live_mirror.py`
  - Python 3.9+; 표준 라이브러리만 사용.
  - 예: `python artitrade_live_mirror.py --server http://100.100.100.100:8765 --output C:\\ARTITRADE_READONLY_MIRROR --interval 2` (Tailscale 주소는 자신의 것으로 교체)
  - 환경변수 `ARTITRADE_MSERVER_TOKEN` 또는 실행 시 암호 입력 필요.
  - `--once` 옵션으로 1회 수신 검증 가능.
  - 네트워크 연결은 기존 MSERVER와 동일하게 USB 루프백 또는 Tailscale 100.64.0.0/10만 허용.
- PC 원본은 절대 이 수신 폴더로 지정하지 않는 것을 권장함. 현재 PC EXE GUI에 수신기 자동내장은 별도로 진행하지 않음.

## 앱 사전로딩
- 앱 최초 화면에서 원장/엔진상태/6개 전략 집계를 준비한 후에만 메인 화면 생성.
- 화면 thread에서 브로커 요청/전체 DB 복사하지 않음. 백그라운드 데이터 파싱 및 화면 모델 재사용.
- 데이터 수신이 16초 넘게 지연될 때는 무한 로딩 대신 연결 대기 상태를 표시하고 대시보드 진입함. 실거래 연결 성공으로 가장하지 않음.

## 주식/코인/전체 연구이력 ZIP
- 연구 탭의 기존 **현재 ZIP** 유지.
- 추가 버튼: **주식 통합 ZIP**, **코인 통합 ZIP**, **프로그램 전체 ZIP**.
- Android 저장폴더 선택 후 모든 원본 SQLite/JSON을 WAL-safe 방식으로 스냅샷 생성.
- 각 ZIP은 최대 **500,000,000바이트 (500MB)**, 독립적인 ZIP 형식임.
- 큰 SQLite 원본은 여러 ZIP의 `fragments/` 파일로 무손실 분할됨. 모든 ZIP에 같은 `analysis/bundle_index.json`이 포함돼 원본 파일명/크기/SHA256/조각 순서를 확인할 수 있음.
- 소형 원본은 ZIP 안에서 곧바로 열 수 있음. 분할 원본은 모든 ZIP이 있어야 복구 가능함.
- PC 복원(기존 EXE 원장 덮어쓰기 금지): `python artitrade_research_restore.py --output C:\\ARTITRADE_RESEARCH_REASSEMBLED ...PART_001.zip ...PART_002.zip`
- 500MB가 사용 중인 ChatGPT 제품의 파일 업로드 상한을 항상 충족한다고 보장할 수 없음. `make_bundle`의 `max_part_bytes`는 더 작게 지정 가능.

## 검증 분리
- 코드 전용 GitHub 브랜치에 저장. 설치파일 생성/배포 없음.
- Python/JVM 단위검사 대상: 동기화 ON/OFF, PC 재수신·수정번호, 시작 화면 게이트, 범위 필터, 분할 제한/CRC/SHA/PC 재조립.
- Android 35 전체 컴파일, 삼성 S21+ 실제 시작/화면 전환, PC Tailscale 연동, 대용량 실데이터 전송은 별도 검증 대상임.
