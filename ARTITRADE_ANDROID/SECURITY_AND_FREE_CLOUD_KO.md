# ARTITRADE 자격증명 및 민감정보 보관 규칙

## 중요한 구분

RESALE_ALPHA는 **공개** 저장소입니다. 공개 저장소 내 특정 폴더만 비공개로 전환하는 GitHub 기능은 없습니다.

**절대로 Git에 올리지 말 것**
- 주식/코인 거래소 및 증권사 API Key, Secret Key, 토큰, 인증서, 패스워드
- 개인 연락처·계좌정보·실제 매매 장부·가상매매 이력 원본·백업
- `.env`, `.dev.vars`, 개인설정 JSON, DB 파일 및 ZIP 원본
- 키가 하드코딩된 EXE 또는 APK 빌드 산출물

`.gitignore`는 *새 파일의 실수로 인한 Git 추가를 완화*할 뿐입니다. 이미 Git에 추적 중인 파일, 이미 게시한 커밋, GitHub 웹에서 수동 업로드한 파일, 다른 사람이 복제한 데이터는 숨기거나 삭제하지 못합니다.

## 관리 권장안

1. 공개 RESALE_ALPHA에는 비밀값이 없는 모바일 클라이언트 코드 및 빌드 설정만 보관합니다.
2. 자동매매 전략 소스는 **별도 Private repository**에서 작업할 것을 권장합니다.
3. GitHub Actions가 배포에 사용할 토큰만 GitHub **Settings > Secrets and variables > Actions > New repository secret**에 입력합니다.
4. 실제 운영 중 매매 API 키는 GitHub가 아닌 **클라우드 서비스의 런타임 Secrets**에 저장합니다. 주식/코인 각각 권한을 분리합니다.
5. 사용자가 직접 설정할 변수 이름 예시(값 아님):
   - `ARTITRADE_STOCK_API_KEY`
   - `ARTITRADE_STOCK_API_SECRET`
   - `ARTITRADE_COIN_API_KEY`
   - `ARTITRADE_COIN_API_SECRET`
6. 클라우드 서비스에서는 같은 이름을 Runtime Secret으로 설정하고, 해당 엔진 컨테이너만 읽게 제한합니다.
7. APK는 로그인·조회·설정 변경 클라이언트로만 사용하며, 거래소 비밀키를 APK에 포함하지 않습니다.
8. 서버는 단일 주문 실행자로 구성하고, 주문 중복 방지 키·계좌 잔고 재조정·API 요청속도 제한·실패 시 중단 동작을 갖춥니다.

## 무료 클라우드 옵션

- **Northflank Sandbox:** 공식 요금표상 2 services, 2 jobs, 1 database, 최대 $20/월 상당 자원, 'Always on, no sleeping'. 제한된 무료 요금제이며 **실거래 운영용 보장 아님**.
- **Cloudflare Workers + D1:** 주식/코인 장부·설정 동기화 API에 적합한 영구 무료 사용량 한도. 지속 실행하는 Python 매매 엔진 대체 아님.
- **Render Free:** 15분 비활성 시 잠자기; 무료 Postgres는 30일 후 만료. 무중단 매매 서비스로 부적합.
- **Google Compute Engine Always Free:** e2-micro 한 대 무료 가능하지만 외부 IPv4 주소 등으로 월 추가 과금 가능. 무조건 0원 보장 어려움.

Northflank 가입: https://northflank.com/pricing
Cloudflare 무료: https://developers.cloudflare.com/workers/platform/limits/

## Northflank 배포 준비 순서

1. Northflank 계정 생성 후 요금제 선택에서 **Sandbox Free**를 선택하고 과금 조건을 확인합니다.
2. GitHub와 연결하고, 가능하면 별도의 **Private ARTITRADE 거래 엔진 저장소**만 연결합니다.
3. 한 프로젝트에 매매 엔진 서비스 1개, 보조 동기화/API 서비스 1개, DB 1개를 만들되 무료 자원 소모를 확인합니다.
4. Project > Secrets > Secret groups에 API 키를 넣고 Runtime 적용 및 서비스별 접근 제한을 설정합니다.
5. 인가된 HTTPS API만 PC EXE와 Android 클라이언트에서 연결하게 합니다.
6. 먼저 종목 조회·모의 매매·장부 업데이트·종료/재시작 복구·중복 주문 방지와 백업을 점검합니다.
7. 실제 자동매매 사용 전 무료 플랜 가용성, 서버 재시작, 로그 영속성, 외부 고정 IP 요구 여부를 확인합니다.

> 2026-11-12 기준 확인. 서비스 요금/제한은 언제든 변경될 수 있으므로 실제 가입 UI에서 다시 확인할 것.

## 무료 요금제와 API IP 허용목록 주의

Northflank Sandbox Free는 별도 유료 고정 출발지 IP(Egress IP/Gateway)를 제공하지 않는 것으로 공식 요금표에 표시됩니다. 관리형 클라우드의 기본 아웃바운드 IP는 공유되고 예측 불가할 수 있습니다. 증권사/거래소가 API 키당 고정 IP 등록을 요구하면 Sandbox Free만으로는 연동이 불가능할 수 있습니다. 유료 Egress Gateway는 공식 요금표상 $15/월입니다.

Northflank 요금표에는 "No card needed"라고 쓰여 있으나, 별도 청구 문서에는 자원 생성 시 결제수단이 필요하다고 되어 있습니다. 실제 가입 화면에서 확인해야 하며, 무료 요금제가 실매매의 SLA를 제공하지 않습니다. 2026-11-12 기준.

참고: https://northflank.com/pricing / https://northflank.com/docs/v1/application/network/configure-egress-ips / https://northflank.com/docs/v1/application/billing/pricing-on-northflank
