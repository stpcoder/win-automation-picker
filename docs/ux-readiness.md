# UX 완성도와 지원 범위

이 문서는 실제 업무 흐름을 기준으로 현재 구현이 어디까지 동작하는지 구분합니다. 수치는 기능 항목 대비 구현 비율을 나타내는 점검값이며, 모든 사내 프로그램에서의 호환성을 보증하는 수치는 아닙니다.

## 현재 완성도

| 영역 | 점검값 | 현재 상태 |
| --- | ---: | --- |
| AE 통합 작업대 | 90% | SEQ 편집기 연결, 무창 검사/빌드, Scratch 직접 편집/시험, hash gate, 동시 업로드 지원 |
| 연속 이벤트 녹화 | 85% | 클릭, 입력 최종값, 주요 키, 동작 간 대기, 정지 클릭 제외 지원 |
| Scratch식 블록 작업실 | 75% | 팔레트 drag/drop, 중첩, 안팎 이동, 이름/색상, undo/redo 지원 |
| 조건과 모니터 보드 | 80% | 텍스트/색상, AND/OR, 자유 CH명, 보드 축/순서 구성 지원 |
| Python export | 90% | 중첩 흐름, 데이터 행, 런타임 변수 포함 실행 파일 생성 |
| FTP 원격 실행 | 85% | PC별 매크로/값 전송, 내장 workflow 실행, 긴급 중단 지원 |
| SEQ Generator 연동 | 88% | profile 검증 bundle, SHA-256 재검사, CH별 SEQ/SK Commander 런처, binary metadata 교환 지원 |
| 원격 세션 모니터링 | 90% | PC/CH 분리 보기, SoC/binary/자재/Test/SEQ/Grid, 결과 보드, 화면 요청, 2-sheet Excel 지원 |
| 초기 설정 UX | 80% | 연결 확인, PC/변수 표 편집, PC별 `.info` 생성 지원 |

## 권장 사용자 흐름

1. `AEWorkbench > 연결 설정`에서 FTP 주소를 입력하고 `연결 확인`을 누릅니다.
2. `공통 변수`와 `Slave PC 목록`을 표에서 추가합니다. CH가 없는 PC도 가능하며 변수 이름은 자유입니다.
3. `배포 · 모니터 > 실행 및 배포 > 서버 폴더 초기화`로 전용 root 아래 폴더를 만듭니다.
4. `Slave .info 내보내기`로 PC별 설정을 만들고 각 PC에 `RigFtpCommander.exe`와 함께 둡니다.
5. 각 slave에서 `이 PC Agent > Agent 시작`을 누릅니다.
6. `AE 작업대`에서 SEQ recipe를 열어 목표 Temp/VDD를 설정하고 오류 검사/Rig package 빌드를 수행합니다.
7. 같은 화면에서 `새 매크로`를 누르고 녹화, Scratch 편집, 로컬 시험과 Python export를 수행합니다.
8. `전체 사전 점검 > SEQ + 매크로 업로드`를 누릅니다.
9. 열린 `PC / 슬롯 / CH별 실행표`에서 PC마다 SEQ, 런처, CH와 값을 입력한 뒤 전송합니다.
10. `상태 모니터링`에서 상태, 현재 단계, 결과 이력, 탭별 보드와 요청한 전체 화면을 확인합니다.

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

### Scratch 언어

- `else` 분기
- `repeat until`, `wait until`
- 변수/비교/산술 reporter 블록
- 블록 검색, 축소/확대, 접기
- 여러 블록 동시 선택
- GUI에서 임의 Python 비교 함수를 plugin처럼 등록하는 기능

현재 작업실은 위 기능이 없는 제한된 Scratch식 자동화 편집기입니다. drag/drop과 중첩 자체는 실제 recipe tree를 수정합니다.

### 원격 운영

- 실시간 화면 스트리밍과 마우스 원격 조작은 지원하지 않습니다.
- Windows 로그인 전 service session에서 UI 자동화하는 기능은 지원하지 않습니다.
- Agent Windows 시작 프로그램 등록은 아직 수동입니다.
- 일반 Python 패키지는 고급 기능이며 slave에 외부 Python과 필요한 모듈이 있어야 합니다.
- 같은 PC의 여러 실행표 행은 UI 충돌을 막기 위해 순차적으로 런처를 실행합니다. 런처는 각 SK Commander 테스트를 시작한 뒤 종료하고, 장시간 상태 추적은 별도 monitor workflow로 구성해야 합니다.
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
- [ ] SEQ 행마다 SK Commander 런처, 슬롯, 필요한 CH를 지정했다.
- [ ] 패키지 상세의 Compatibility와 Field Verified 상태를 확인했다.
