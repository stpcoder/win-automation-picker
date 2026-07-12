# 버튼/메뉴 사전

## AE Workbench 상단

| 버튼 | 설명 |
| --- | --- |
| `연결 확인` | FTP 전용 root의 로그인·쓰기·읽기·삭제 권한 확인 |
| `Rig 설정` | Master 연결 설정 화면으로 이동 |
| `1 오늘 작업` | 일일 실행, 캠페인, PC·CH 상태 |
| `2 자동화 준비` | SEQ와 프로그램 매크로 제작·등록 |
| `3 Rig 설정` | Master, 원격 PC/CH, Agent와 고급 정책 |

## 오늘 작업

| 버튼/메뉴 | 설명 |
| --- | --- |
| `자동화 새로고침` | 서버의 FLOW와 SEQ 라이브러리 다시 읽기 |
| `SEQ 방식` | 선택 행 또는 전체 실행표를 `직접 COM`/`SK Commander`로 전환 |
| `Rig 대상 불러오기` | 설정한 PC/CH를 실행표 행으로 생성 |
| `행 편집` | 대상 추가, 행 복제, 선택 행 삭제 |
| `실행 시작` | 체크된 각 PC/CH 행을 별도 job으로 제출 |
| `모니터링` | `PC · CH 상태`로 이동 |
| `긴급 중단` | 대상 PC에 stop signal 전송 |
| `운영 도구 열기` | raw 패키지 등록과 단일 PC 고급 실행 표시 |
| `[MARGIN]` 패키지 실행 | exact PC/CH에서 nominal probe, DQ sweep, 물리 단위 acceptance를 한 작업으로 실행 |
| `마진 번들 만들기` | Windows controller, plan, PHY reference를 checksummed `*.drammargin.zip`으로 생성 |
| `Scratch 수정` | 선택한 서버 FLOW를 source project로 안전하게 복원 |

## 자동화 준비

| 버튼/메뉴 | 설명 |
| --- | --- |
| `파일` | `*.aework.json` 열기·저장 |
| `SEQ 편집` | 현재 recipe를 Test Sequence Generator에서 열기 |
| `검사 · 패키지 준비` | SEQ 검사 후 checksummed `*.rigseq.zip` 생성 |
| `SEQ 더보기` | 검사만 실행, recipe/package/tool 경로 선택 |
| `Scratch 편집` | 현재 프로그램 매크로를 블록 작업실에서 열기 |
| `검사 · Python 준비` | Scratch 구조 검사와 최신 FLOW export |
| `Scratch 더보기` | 새 매크로, 구성 검사만 실행, 다른 source 선택 |
| `값 편집` | 감지된 로컬 시험 변수를 입력·저장 |
| `시험` / `중지` | 현재 PC에서 실제 매크로 실행·정상 중단 |
| `버튼 관리` | 프로그램 매크로 등록, 이름/메모, 순서, 삭제 |
| `검사 상세 보기` | 기본 화면에서 숨긴 SEQ·매크로 검사 로그 펼치기 |
| `준비 상태 확인` | recipe/package와 source/FLOW hash gate 확인 |
| `서버 라이브러리 등록` | 검증된 FLOW와 SEQ 등록; 실행은 시작하지 않음 |
| `오늘 작업 열기` | 일일 실행 화면으로 이동 |

### 실장기 제어 · Binary

| 버튼 | 동작 |
| --- | --- |
| `선택 연결` | 이 Slave PC의 선택 COM을 지속 연결 |
| `COM 대조` | 이 PC의 COM/HWID를 물리 실장기 설정과 비교하고 고유한 이동만 변경 제안 |
| `전송` | 선택 CH에 출력 가능한 ASCII와 Enter 전송 |
| `제어 키` | Enter, Ctrl+C/0x03, Ctrl+V/0x16 또는 검증된 ASCII 붙여넣기 |
| `동시 실행` | 선택한 최대 4개 CH에서 `.seq`를 병렬 실행 |
| `Master 상태 공유` | 현장 직접 COM 실행의 Grid/결과를 Master heartbeat에 반영 |
| `정지` | 직접 SEQ의 다음 command 전송 중단 |
| `PC 환경` | 원격 Windows/PowerShell/pyserial 점검 요청 |
| `통신 점검` | 원격 COM/ADB 상태 점검 요청 |
| `전원` | CH별 ON/OFF/cycle 명령 요청 |
| `원격 사전점검` | Tool/XML hash/USB identity/Vendor gate 검사 |
| `Binary 업데이트 시작` | 사전점검을 다시 수행한 뒤 한 CH Downloader 실행 |

## Scratch 상단과 녹화

| 버튼 | 설명 |
| --- | --- |
| `불러오기` / `저장` | 매크로 source project 열기·저장 |
| `Python 내보내기` | 단독 실행 가능한 Python FLOW 생성 |
| `실행` / `데이터 실행` | 기본값 1회 또는 데이터 행별 실행 |
| `중지` | 실행 중단 요청 |
| `연속 녹화 시작` / `녹화 정지` | 외부 프로그램 클릭·입력·키 세션 기록 |
| `대상 확인` | 블록을 만들지 않고 component 조사 |
| `클릭 녹화` / `입력 녹화` | 한 component의 클릭·입력 블록 생성 |
| `대상 고급 설정` | element 역할과 동일 프로그램 창 구분 조건 |

## Scratch 블록 작업실

| 버튼/값 | 설명 |
| --- | --- |
| `보기=작게` | 46px 블록과 2px 간격의 기본 조밀 보기 |
| `보기=보통` | 58px 블록과 5px 간격 |
| `되돌리기` / `다시 실행` | 편집 undo/redo |
| `변경 적용` | 선택 블록 설정 저장 |
| `위로` / `아래로` | 같은 컨테이너 안에서 이동 |
| `앞 블록 안으로` | 바로 앞 반복·조건·AND/OR 안으로 이동 |
| `컨테이너 밖으로` | 부모 컨테이너 바로 다음으로 이동 |
| `복제` / `풀기` / `삭제` | 블록 tree 복제, container 제거, 삭제 |
| `대상 다시 선택` | 선택 블록의 UIA selector 교체 |
| `현재 값 읽기` | 텍스트·색상 조건값 다시 읽기 |
| `선택 블록 시험` | 선택 블록만 실행·판정 |
| `Rig 버튼으로 등록` | 현재 source를 프로그램 매크로 목록에 등록 |

## Rig 설정

| 버튼 | 동작 |
| --- | --- |
| `연결 구조 > 구성 검사` | Master/FTP/PC/실장기 4계층의 누락·중복·소유권 검사 |
| `연결 구조 > 선택 수정` | 선택한 계층의 안정 ID와 실제 위치 수정 |
| `연결 구조 > 이 PC COM 대조` | 선택 PC가 이 Windows에 물리적으로 연결된 경우 COM/HWID 대조 |
| `장치 도구 > 도구 추가` | MTK/QC 외부 Downloader CLI와 결과 규칙 등록 |
| `Slave 설정 내보내기` | PC별 FTP 및 장치 설정 파일 두 개 생성 |

| 위치 | 버튼/메뉴 | 설명 |
| --- | --- | --- |
| `Master · FTP` | `파일` | config 선택·불러오기·예제 생성 |
| `Master · FTP` | `저장` | 현재 `rig-ftp.info` 저장 |
| `Master · FTP` | `연결 확인` | FTP 권한 확인 |
| `실장기 연결 PC` | `연결 PC 추가` | 별명, Node ID, 자산 ID, Windows 이름, IP, 위치와 PC별 변수 등록 |
| `실장기 연결 PC` | `실장기 관리` | 물리 ID/Serial/위치, 자유 CH, COM/HWID, SoC, Binary, 자재 등록 |
| `연결 PC 편집` | `CSV 가져오기/내보내기` | 여러 PC와 실장기를 Node+CH 기준으로 일괄 병합·내보내기 |
| `실장기 연결 PC` | `서버 폴더 준비` | PC별 FTP spool 폴더 생성 |
| `실장기 연결 PC` | `Slave 설정 내보내기` | 각 PC용 `rig-ftp.info`와 `rig-commander.config.json` 생성 |
| `이 PC Agent` | `Agent 시작` / `Agent 중지` | slave polling 시작·중지 |
| `이 PC Agent` | `현장 SK 감시 시작/중지` | 현장에서 직접 시작한 SK Commander를 읽기 전용 workflow로 감시 |
| `Agent 더보기` | `한 번 확인` | pending job 1회 확인 |
| `Agent 더보기` | `중단 신호 해제` | 이 PC stop signal 제거 |

## 모니터링

| 버튼/메뉴 | 설명 |
| --- | --- |
| `새로고침` | 상태와 선택 PC 결과를 함께 읽기 |
| `전체 화면 보기` | 이번 요청과 일치하는 최신 screenshot 요청·표시 |
| `모니터 보드` | workflow의 구조화된 텍스트·색상 판정 표시 |
| `더보기 > 선택 작업 긴급 중단` | 선택 PC의 현재 job만 중단 |
| `더보기 > 선택 결과 분류` | failure class, 조치, 담당자와 근거 기록 |
| `더보기 > 선택 결과 증거 ZIP 저장` | 직접 COM manifest, Grid log와 console 증거 다운로드 |
| `더보기 > Excel 내보내기` | PC State와 CH Inventory workbook 생성 |
| `자동 상태 조회 시작/중지` | heartbeat와 결과 파일 주기 조회 |
