# ARTITRADE 갤럭시 S21+ 자체 서버 — 시험판

## 현재 구현 범위

이 프로젝트는 스마트폰을 **데이터 서버**로 쓰기 위한 초기 동작 버전입니다. 기존 Windows EXE의 주문 로직과 Android 매매 엔진은 연결되지 않았습니다. 다운로드한 APK만 설치해서 **실거래가 자동으로 실행되지는 않습니다.**

- **폰 서버 (Hub APK)**: Android Keystore AES-GCM 암호화 SQLite, 주식/코인 설정·장부·연구·오류 자료 저장, 데이터 버전 충돌 감지, PC/휴대폰 매매기기 선택과 30초 실행 권한 임대
- **주식 APK**: 기존 클라우드 접속 시험 UI + 같은 휴대폰에 설치한 폰 서버와 페어링, AT/CW 동기화 데이터 **조회**
- **코인 APK**: 기존 클라우드 접속 시험 UI + 폰 서버 페어링, 일반/MA 동기화 데이터 **조회**
- **PC helper**: `pc_tools/artitrade_hub_client.py` — Tailscale 주소를 통해 데이터 조회·수정/버전검사 기능. 기존 EXE 자동 동기화와 주문 차단은 미연결.

## 설치

GitHub Actions 빌드 `Build ARTITRADE S21 Plus Android clients`에서 내려받은 압축파일에 **3개 APK**가 포함됩니다.

1. S21+에 `app-hub-debug.apk` 설치 후 **ARTITRADE 폰 서버** 앱 실행
2. **서버 시작** 버튼 누르기. 알림창에 실행 중 표시가 생기는지 확인
3. 같은 휴대폰에 `app-stock-debug.apk`, `app-coin-debug.apk` 설치
4. 폰 서버에서 **PC 연결용 토큰 보기** 눌러 64자리 연결 토큰 확인
5. 주식·코인 앱의 **갤럭시 데이터 서버 연결 토큰 입력**에 위 토큰을 각각 등록
6. 두 앱에서 **갤럭시 데이터 서버 상태 조회** 버튼으로 로컬 접속 확인
7. 폰 서버에서 주식/코인별 실행 기기 선택 가능. 이 선택은 **기존 거래 엔진에 아직 적용되지 않는 시험 설정**임

## PC에서 원격 접속

공유기 포트를 외부에 열지 마십시오. 앱의 HTTP는 TLS를 자체 구현하지 않았으며 **오직 Tailscale VPN을 통해서만** PC 연결하도록 의도했습니다.

1. Windows PC와 갤럭시 S21+에 Tailscale을 설치하고 **같은 Tailnet**에 로그인
2. 폰 서버 앱에서 `Tailscale 접속 켜기` 선택하고 서버 실행
3. Tailscale 앱에서 S21+에 할당된 `100.x.y.z` 주소 확인
4. Windows PC Python 3.9+에서 다음 명령 실행:
   `python artitrade_hub_client.py --url http://100.x.y.z:8765 status`
5. 연결 토큰을 물으면 폰 서버에서 표시한 토큰을 입력 (입력값은 화면에 표시되지 않음)
6. 예시: `python artitrade_hub_client.py --url http://100.x.y.z:8765 get stock at settings`
7. 쓰기 예시: `python artitrade_hub_client.py --url http://100.x.y.z:8765 put coin general settings settings.json --expected-revision 0`

**중요:** 휴대폰 Tailscale이 Android에서 별도 TUN 인터페이스에 100.64/10 주소를 제공하지 않으면 서버가 VPN 주소에 바인딩하지 못해 PC와 연결되지 않을 수 있습니다. S21+ 실기기 검증 전에는 보장할 수 없습니다. 사용 중 연결 실패하면 앱의 단순 재시작 및 Tailscale 활성화 상태를 우선 확인하십시오.

## 매매 실행 기기 전환 — 향후 실제 엔진 연결 시 필요

- 폰 서버는 주식/코인별 `PC 실행 / 폰 실행 / 전체 정지` 선택 정보를 저장
- 기존 실행 기기가 30초 유효 권한을 보유하고 있으면 새 기기의 임대가 거부됨
- 기존 기기가 `release`를 하고 실제 미체결 상태를 확인한 뒤 새 기기가 `claim` 수행
- 기존 실행 기기 소실 후 임대 만료 시에도 자동 승계하지 않고 수동 주문 대조를 요구
- 거래 엔진은 주문 직전 반드시 `check`, 실행 중 정기적 `renew`, 종료 시 `release` 구현 필요
- **기존 EXE는 위 API를 아직 호출하지 않으므로 권한 제어가 실거래를 차단하지 못함**

## 기기 사용 시 제약

- 서버 역할을 하는 S21+가 꺼지거나 재부팅·앱 강제 종료·VPN 연결 끊김 시 데이터 동기화 중단
- Android 15 이상은 `dataSync` 포그라운드 서비스에 24시간 6시간 사용 제한이 있으므로 `specialUse` 유형을 선택했으나, 장시간 실행/절전/삼성 펌웨어 동작은 **실제 테스트 필요**
- 앱은 사용자가 직접 시작해야 함. 자동 부팅 복원 미구현
- 배터리 설정에서 폰 서버 앱을 절전 예외로 둘 수 있지만 연속 가동을 보장하지는 않음
- 서버는 localhost + VPN 주소에만 바인딩하도록 설계. **공유기 포트포워딩 금지**
- 기존 폰에 있던 개인 데이터는 자동 가져오지 않음. `settings`, `ledger`, `research`, `errors` 버킷을 각각 명시적으로 이전해야 함
- 안드로이드 앱 삭제·데이터 초기화 시 내부 DB가 지워질 수 있으므로 정기 백업 필요. 복원·자동 백업 아직 없음
- 브로커/거래소 API Key, Secret Key 등은 GitHub와 APK 안에 넣지 않음

## 테스트/상태 표기

GitHub Actions에서 Gradle 컴파일과 APK 패키징은 확인 가능함. Android 실기기에서 서버가 외부 PC 접속을 받는지, 백그라운드에서 살아 있는지, 원본 EXE의 실매매와 연계되는지는 별도로 검증해야 함.
