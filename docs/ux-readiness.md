# UX 완성도와 지원 범위

이 문서는 실제 업무 흐름을 기준으로 현재 구현이 어디까지 동작하는지 구분합니다. 수치는 기능 항목 대비 구현 비율을 나타내는 점검값이며, 모든 사내 프로그램에서의 호환성을 보증하는 수치는 아닙니다.

## 현재 완성도

| 영역 | 점검값 | 현재 상태 |
| --- | ---: | --- |
| AE 통합 작업대 | 94% | 오늘 작업·자동화 준비·Rig 설정 분리, SEQ/Scratch 검사, hash gate와 서버 등록 지원 |
| 연속 이벤트 녹화 | 85% | 클릭, 입력 최종값, 주요 키, 동작 간 대기, 정지 클릭 제외 지원 |
| Scratch식 블록 작업실 | 90% | 46px 조밀 보기, 2px 결합 간격, drag/drop, 중첩, 버튼식 안팎 이동, 이름/색상, undo/redo 지원 |
| 조건과 모니터 보드 | 80% | 텍스트/색상, AND/OR, 자유 CH명, 보드 축/순서 구성 지원 |
| Python export | 90% | 중첩 흐름, 데이터 행, 런타임 변수 포함 실행 파일 생성 |
| FTP 원격 실행 | 90% | PC별 매크로/값 전송, 내장 workflow, 최대 4 CH 직접 COM batch와 긴급 중단 지원 |
| SEQ Generator 연동 | 92% | profile 검증 bundle, SHA-256 재검사, 직접 COM/SK 런처 선택, binary metadata 교환 지원 |
| 원격 세션 모니터링 | 90% | PC/CH 분리 보기, SoC/binary/자재/Test/SEQ/Grid, 결과 보드, 화면 요청, 2-sheet Excel 지원 |
| 초기 설정 UX | 90% | 일일 화면과 분리된 Master 연결, PC/변수/자유 CH 표 편집, PC별 `.info` 생성 지원 |

## 핵심 판단

- **업무 빈도에 맞춰 세 화면으로 나뉩니다.** 프로그램별 SEQ와 Scratch는 `자동화 준비`,
  FTP와 PC/CH inventory는 `Rig 설정`, 반복 실행과 판정은 첫 화면인 `오늘 작업`에서 처리합니다.
- **Scratch는 장식용 블록 목록이 아닙니다.** 실제 recipe tree를 drag/drop으로 바꾸며
  repeat/if/AND/OR 중첩과 Python export 결과도 같은 트리를 사용합니다.
- **현장 호환성이 가장 큰 잔여 위험입니다.** Mac에서는 UI와 데이터 흐름을 검증했지만
  사내 SK Commander selector, serial prompt/timing과 실제 성공 SEQ는 Windows 장비 증거가 필요합니다.
- **운영자는 JSON을 몰라도 됩니다.** 시험 변수, 공통 변수, PC/CH inventory와 실행표를
  GUI에서 편집합니다. JSON/고급 Python은 기본 흐름 밖에 둡니다.

## Mac 실제 조작 감사

문서용 데모가 아니라 실제 앱 메서드와 위젯 이벤트를 사용했습니다.

| 시나리오 | 결과 | 확인 내용 |
| --- | --- | --- |
| Scratch 팔레트 drag/drop | PASS | 놓는 순간 포인터 기준 재계산, 반복 블록의 부모 여백과 내부 슬롯 구분 |
| 블록 편집 | PASS | 이름, 위/아래, 안/밖, 복제, 삭제, undo/redo |
| 실행 제어 | PASS | wait 매크로 시작, 중지 버튼 활성화, 오류 팝업 없는 `Stopped` 종료 |
| Workbench gate | PASS | SEQ/package와 macro/export SHA-256 일치 시에만 업로드 활성화 |
| Sequence Generator | PASS | Conditions, Run Matrix, Preview와 Validate `5 blocks / 86 commands` |
| 1080×720 / 1320×820 / 1420×860 레이아웃 | PASS | 주요 6개 화면의 버튼·입력·표 경계와 텍스트 겹침 없음 |
| Scratch 조밀 보기 | PASS | 46px 블록, 2px 스택 간격, 14px 드롭 영역과 연결 홈 표시 |
| 캡처 격리 | PASS | ScreenCaptureKit이 현재 프로세스와 제목이 일치하는 독립 창만 캡처 |
| PC/CH 보드 | PASS | CH9→CH12 자연 정렬, running/pass/fail 색상, Grid 진행 |

재현 스크립트는 `scripts/capture_manual_screenshots.py`이며, 생성된 실제 화면은 매뉴얼의
11개 PNG입니다. FTP 주소, 계정과 비밀번호 환경 변수는 모두 데모 값입니다.

## 권장 사용자 흐름

1. `2 자동화 준비`에서 SEQ recipe의 목표 Temp/VDD를 정하고 `검사 · 패키지 준비`를 수행합니다.
2. 같은 화면에서 프로그램 매크로를 녹화하고 `Scratch 편집`, 로컬 `시험`, `검사 · Python 준비`를 수행합니다.
3. `준비 상태 확인` 후 `서버 라이브러리 등록`을 누릅니다. 이 동작은 원격 실행을 시작하지 않습니다.
4. 최초 1회 `3 Rig 설정 > Master 연결`에서 FTP 주소와 전용 root를 저장하고 `연결 확인`을 누릅니다.
5. `원격 PC · CH`에서 PC, 공통 변수와 자유 CH를 등록하고 `서버 폴더 준비`를 누릅니다.
6. `Slave 설정 내보내기`로 PC별 `.info`를 만들고 각 PC에서 `이 PC Agent > Agent 시작`을 누릅니다.
7. 매일 `1 오늘 작업 > 실행`에서 자동화를 선택하고 `Rig 대상 불러오기`로 PC/CH별 값을 확인합니다.
8. `실행 시작`으로 체크된 행만 전송하고 `캠페인`과 `PC · CH 상태`에서 Grid와 결과를 확인합니다.

## SK Commander 4개 창 적용

한 PC에서 같은 프로그램 창이 네 개 떠 있는 경우 다음처럼 구성합니다.

1. 각 블록의 대상 selector에 프로그램 창 정보와 CH 식별용 window marker를 함께 저장합니다.
2. CH component는 `equals CH9`, `equals CH10`, `equals CH11`, `equals CH12`처럼 정확 비교합니다.
3. 상태 component에는 파랑 `RUNNING`, 초록 `PASS`, 빨강 `FAIL` 색상 규칙을 만듭니다.
4. `CH 식별 조건 AND 상태 조건`을 한 묶음으로 만듭니다.
5. `탭`, `장비 / CH`, `표시 상태`는 고정 스키마가 아닌 자유 문자열로 지정합니다.

CH가 없는 프로그램은 CH 조건을 만들지 않고 창 제목, 내부 고유 텍스트 또는 다른 component 조건만 사용합니다. `CH1`과 `CH11`이 겹치는 경우 `contains` 대신 `equals` 또는 경계가 있는 정규식을 사용합니다.

## 이번 점검에서 막은 위험

| 항목 | 처리 |
| --- | --- |
| 자동 새로고침이 매크로를 반복 전송 | 상태 읽기 전용 loop로 교체 |
| 원격 상태 갱신 때문에 클릭/입력까지 재실행 | `monitor` job이 monitor 블록만 추출해 평가 |
| slave에 Python이 없으면 생성 매크로 실패 | export workflow를 slave EXE 내장 엔진으로 실행 |
| 오래된 화면을 새 요청 결과처럼 표시 | 요청 job 전용 label과 정확히 일치하는 PNG만 표시 |
| FTP 일시 오류로 agent 종료 | 재연결 상태와 최대 60초 backoff 추가 |
| 오래된 heartbeat가 정상처럼 표시 | 등록 PC 병합 및 stale/offline 판정 추가 |
| 결과를 로그에서만 확인 | PASS/FAIL 실행 이력 표와 상세 창 추가 |
| JSON 직접 편집 중 설정 오류 | 공통 변수와 slave PC를 표 기반 편집으로 교체 |
| broadcast 파일 무한 증가 | 모든 등록 slave 처리 후 pending 원본 정리 |
| 손상되거나 검증을 건너뛴 SEQ 실행 | generator validation과 SEQ/Recipe SHA-256을 Slave에서 재검사 |
| 수정한 recipe에 예전 package를 업로드 | 원본 recipe source SHA-256을 manifest에 기록하고 Workbench에서 stale 판정 |
| 수정한 Scratch에 예전 Python을 업로드 | project SHA-256과 마지막 export SHA-256이 같을 때만 통합 업로드 활성화 |
| 중첩 블록이 부모 여백으로 나오지 못함 | 깊이 점수 조정과 직접 `컨테이너 밖으로` 버튼 추가 |
| 블록이 커서 한눈에 흐름을 보기 어려움 | 기본 46px 조밀 보기와 폭 상한, 2px 스택 간격 적용 |
| 블록 사이가 떨어져 연결 여부가 불명확 | 결합 홈을 맞붙이고 드래그 중 14px 하늘색 결합 영역 표시 |
| 빠른 드롭 때 마지막 이동 위치에 들어감 | 버튼을 놓는 순간의 포인터 좌표로 삽입 위치 재계산 |
| 일일 화면에 설정·고급 버튼이 과다 노출 | 오늘 작업·자동화 준비·Rig 설정 분리와 메뉴/접힌 영역 적용 |
| `중지`가 오류 팝업으로 보임 | 사용자 중단을 정상 `Stopped` 상태와 실행 기록으로 처리 |
| macOS 다크 테마에서 설명/로그가 검은 박스로 표시 | 모든 Tk Text에 명시적 밝은 표면과 본문색 적용 |
| CH10이 CH9보다 먼저 보임 | 자유 이름을 유지하는 숫자 자연 정렬 적용 |
| 로컬 시험값에 JSON 문법 필요 | 감지된 변수의 표 기반 `값 편집`과 작업별 저장으로 교체 |
| 빠른 매크로 버튼을 다시 만들지 않으면 수정 불가 | 이름/메모 수정과 좌우 순서 변경 추가 |
| 요청/가설/판정과 실행 결과가 분리 | AE campaign snapshot, preflight, PC/CH/attempt 보드로 연결 |
| 실패 원인 메모가 원본 로그를 덮어씀 | 별도 triage sidecar에 분류, 조치, 담당자, 근거 저장 |
| Slave 작업 폴더에 SEQ 무한 누적 | 같은 SHA 폴더 재사용, Rig 소유 형태의 최근 50개만 정리 |

## FTP 부하 제어

- FTP 연결은 작업마다 짧게 열고 닫으며 장시간 점유하지 않습니다.
- slave polling은 기본 간격과 jitter를 사용합니다.
- 실행 중 stop 신호 확인은 최대 2초 간격으로 제한합니다.
- 화면 요청은 master와 slave 양쪽에서 최소 간격을 적용합니다.
- 결과, 로그, archive, 화면 파일은 보관 개수를 넘으면 오래된 순서로 삭제합니다.
- 상태 자동 조회의 최소 간격은 10초입니다.

따라서 FTP를 socket이나 실시간 영상 채널처럼 사용하지 않습니다. 전체 화면은 사용자가 요청할 때만 원본 PNG 한 장을 전송합니다.

## 아직 남은 범위

### 구현 우선순위

| 우선순위 | 기능 | 이유 |
| --- | --- | --- |
| P0 현장 검증 | 실제 SK Commander 네 창의 selector 녹화/재실행 증거 | 전체 자동화 성공 여부를 결정하는 마지막 경계 |
| P0 현장 검증 | 실제 성공 SEQ, Grid log, prompt/context와 package 비교 | 현재 compatibility는 정적 규칙이며 timing을 보증하지 않음 |
| P0 운영 | 사내 전용 FTP 계정과 별도 root에서 10~50대 soak test | polling, screenshot, retention 부하를 실환경에서 계측해야 함 |
| P1 | selector 변경 감지와 재연결 일괄 점검 | SK Commander 업데이트 때 조용히 잘못 누르는 위험 감소 |
| P1 | Windows 시작 시 Agent 자동 실행/상태 tray | 재부팅 후 사람이 Agent 시작을 빼먹는 위험 감소 |
| P1 | `else`, `repeat until`, `wait until`, reporter 변수 | 더 복잡한 복구·대기 흐름을 블록만으로 표현 |
| P1 | Qualcomm/MTK downloader adapter 현장 검증 | 물리 switch/preloader 조건을 자동화 전에 안전하게 gate |
| P2 | 매크로/SEQ 템플릿 카탈로그와 승인 이력 | 반복 업무 재사용과 변경 추적 향상 |
| 제외 | FTP 기반 실시간 영상/마우스 원격조작 | FTP 부하와 사내 보안 경계를 벗어남 |

### Scratch 언어

- `else` 분기
- `repeat until`, `wait until`
- 변수/비교/산술 reporter 블록
- 블록 검색, 컨테이너 접기, 추가 축소 단계
- 여러 블록 동시 선택
- GUI에서 임의 Python 비교 함수를 plugin처럼 등록하는 기능

현재 작업실은 위 기능이 없는 제한된 Scratch식 자동화 편집기입니다. drag/drop과 중첩 자체는 실제 recipe tree를 수정합니다.

### 원격 운영

- 실시간 화면 스트리밍과 마우스 원격 조작은 지원하지 않습니다.
- Windows 로그인 전 service session에서 UI 자동화하는 기능은 지원하지 않습니다.
- Agent Windows 시작 프로그램 등록은 아직 수동입니다.
- 일반 Python 패키지는 고급 기능이며 slave에 외부 Python과 필요한 모듈이 있어야 합니다.
- 같은 PC의 `직접 COM` 행은 고유 COM을 확인하고 최대 네 개를 병렬 실행합니다. SK Commander 행은 UI 충돌을 막기 위해 순차 실행하며 장시간 상태 추적은 별도 monitor workflow로 구성합니다.
- Campaign 보드는 계획/실행/판정/triage를 연결하지만 자재 또는 제품 root cause를 자동 확정하지 않습니다. AE가 evidence를 검토해 분류해야 합니다.

### 호환성과 검증

- native Windows UI Automation 정보를 거의 노출하지 않는 custom canvas는 selector를 안정적으로 만들 수 없습니다.
- 관리자 권한 프로그램은 Picker와 slave도 같은 권한 수준으로 실행해야 합니다.
- 자동 테스트는 recipe, export, FTP spool 계약을 검증하고 Windows CI에서 GUI 생성과 EXE 빌드를 확인합니다. 실제 사내 SK Commander 화면에 대한 최종 selector 시험은 대상 PC에서 수행해야 합니다.
- 기본 `.rigseq.zip`은 `SK Commander Mobile SoC` profile 검사를 통과합니다. 그러나 실제 성공 SEQ, SK Commander log, raw serial log가 없으므로 command timing, live prompt/context, 장비 결과까지 field verified된 상태는 아닙니다.

## 배포 전 확인표

- [ ] FTP 전용 계정이 지정 root에만 접근한다.
- [ ] 모든 Node ID가 중복되지 않는다.
- [ ] 등록 PC가 상태표에서 `online` 또는 `offline`으로 모두 보인다.
- [ ] 테스트용 PC 한 대에서 클릭, 입력, 중단을 먼저 검증했다.
- [ ] 입력값 중 PC별 값은 변수로 바꿨다.
- [ ] CH 식별 조건과 상태 조건을 한 창에서 시험했다.
- [ ] 화면 요청 최소 간격과 파일 보관 개수를 확인했다.
- [ ] 전체 전송 전 실행표의 대상 PC와 매크로를 다시 확인했다.
- [ ] SEQ 행마다 직접 COM/런처 방식, 슬롯, CH, COM과 baud를 확인했다.
- [ ] 패키지 상세의 Compatibility와 Field Verified 상태를 확인했다.
