# ARTITRADE v031 Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task by task with failing tests.
**Goal:** 더블탭 점수 원장 팝업 + 가로 공간 활용 개선
**Architecture:** 순수 Java의 좌표/점수 필터 도우미 → Android Canvas 뷰 GestureDetector → 앱 원장 기반 읽기전용 모달; 원본 SQLite 조회 adapter 추가 필드; 4개 텍스트형 이력 탭의 가중치 열 재배치.
**Tech Stack:** Android Java 17, Python 3.11, SQLite, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-11-14-artitrade-research-band-responsive-design.md

## Global Constraints
- 매매·주문 상태머신, 보유 원장, 체결·가상 판정 변경 금지.
- 자료가 없으면 미기록. 목표 관측은 매도 체결이 아님.
- EXE·APK 만들지 않음. Java compile-only CI.
## Review Focus
- 두번 터치 vs 한 번/스크롤, 작은 화면 좌표, 왼쪽 프레임별 구간 분리, 중복/누락 원장, GMO D+N 거래일과 실제 매도 구분.

### Task 1: 입력·필터·팝업
**Files:** ResearchScoreMatrixView.java, ResearchBandSelection.java, UnifiedVisualActivity.java, ui_tests/ResearchBandSelectionTest.java
**Interfaces:** ResearchScoreMatrixView.setOnBandDoubleTapListener((frame,band)->void); ResearchBandSelection.matches(frame,score,selectedFrame,selectedBand)
- [ ] Add failing JVM band/geometry test and verify red.
- [ ] Add pure Java selection + hit geometry, verify green.
- [ ] Attach doubleTap gesture and Android dialog with weighted columns, scroll/close and matching research histories.
- [ ] Verify Android Java compile-only and old UI tests.

### Task 2: 원본 실제·관측 근거 보강
**Files:** android_runtime_v078.py, ui_tests/test_v031_research_popup_evidence.py
- [ ] Add failing synthetic SQLite tests on actual first-target fills and unselected-only observation.
- [ ] Add read-only price+time fields for AT/MA/CLOSE/coin/GMO where source contains them, preserve absent.
- [ ] Verify adapter tests plus existing EXE parity tests.

### Task 3: 가로 정보밀도 개선
**Files:** UnifiedVisualActivity.java, ui_tests/test_v031_research_layout.py
- [ ] Add failing structure test for research/scores/errors/policy/operation 2~4 column distribution.
- [ ] Implement weighted horizontal rows on landscape and no-overlap fallback.
- [ ] Verify Java compilation/test, plus all existing UI regression.

### Task 4: 최종 검증
**Files:** .github/workflows/artitrade-v031-code-only-tests.yml, docs/ARTITRADE_V031_CODE_ONLY_NOTES.md
- [ ] Tests for Java band selection, Python evidence, unchanged trading rules.
- [ ] Compile unified Android Java code, assert no APK build outputs, inspect workflow result.
- [ ] GitHub branch and exact validated commit; no download link unless file exists.
