# Master 세팅

## 1. 연결 설정 입력

`RigFtpCommander.exe`를 실행하고 `연결 설정` 탭을 엽니다.

| 필드 | 예시 | 설명 |
| --- | --- | --- |
| `FTP 주소` | `192.168.0.10` | FTP 서버 주소 |
| `포트` | `21` | FTP 포트 |
| `아이디` | `macro_user` | FTP 계정 |
| `비밀번호` | `******` | FTP 비밀번호 |
| `비밀번호 환경 변수` | `RIG_FTP_PASSWORD` | 평문 저장 대신 읽을 환경 변수 이름 |
| `서버 폴더` | `/win_automation_macros` | spool root |
| `이 PC Node ID` | `master-pc` | master 자신의 식별값 |
| `조회 간격(초)` | `5` | slave polling 기본값 |
| `작업 폴더` | `rig-ftp-work` | slave 작업 폴더 |

입력 후 상단 `연결 확인`을 누릅니다. 전용 root 아래에 임시 확인 파일을 만들고 읽고 삭제해 권한까지 검사합니다. 성공 메시지를 확인한 다음 `저장`을 누르면 `rig-ftp.info`가 저장됩니다.

`비밀번호 환경 변수`가 입력되어 있으면 실제 비밀번호는 `.info`에 저장하지 않습니다. 해당 환경 변수는 master와 각 slave PC에서 각각 설정되어 있어야 합니다.

## 2. slave 목록 입력

1. `Slave PC 목록 > PC 추가`를 누릅니다.
2. `Node ID`, 별명, IP를 입력합니다.
3. PC별 값은 `channel=CH11; line=A`처럼 세미콜론으로 구분합니다.
4. CH가 없는 PC는 `channel`을 입력하지 않아도 됩니다.

별명은 `PC04`처럼 사람이 보는 이름이고 대상 입력에도 사용할 수 있습니다. Node ID는 PC마다 고유해야 합니다.

## 3. FTP 폴더 초기화

1. `모니터 및 실행 > 실행 및 배포`를 엽니다.
2. `대상 PC`에 별명 또는 Node ID를 입력합니다. 예: `PC04 PC05 PC06`.
3. `서버 폴더 초기화`를 누릅니다.

## 4. slave .info 만들기

1. `서버 초기 설정 > 더보기`를 엽니다.
2. `Slave .info 내보내기`를 누릅니다.
3. 출력 폴더를 선택합니다.
4. 생성된 각 폴더의 `rig-ftp.info`를 해당 slave PC에 복사합니다.

## 5. macro 업로드

이미 export한 Python macro가 있다면:

1. `매크로 업로드 > 파일`에서 `.py` 파일을 선택합니다.
2. 파일명, 제목, 설명을 입력합니다.
3. `매크로 업로드`를 누릅니다.
4. `매크로 목록`에 표시되는지 확인합니다.

선택 매크로 정보의 Runner가 `내장 워크플로 엔진`이면 slave에 Python을 설치할 필요가 없습니다. `외부 Python`으로 표시되는 일반 스크립트는 고급 실행 환경이 필요합니다.

Win Automation Picker에서 현재 블록 workflow를 바로 업로드하려면 `WinAutomationPicker.exe > Deploy` 탭을 사용합니다.

## 6. PC별 macro와 입력값 실행

1. `매크로 목록`에서 macro를 선택합니다.
2. `PC별 매크로 실행표 > 설정 PC 불러오기`를 누릅니다.
3. package metadata에 저장된 입력 변수 열이 자동으로 생겼는지 확인합니다.
4. 각 행을 더블클릭해 PC별 매크로와 값을 바꿉니다. 예: `PC01 = Seq 1`, `PC02 = Seq 2`.
5. 실행하지 않을 PC는 첫 `실행` 셀을 클릭해 체크를 끕니다.
6. `실행표 전송`을 누릅니다. Master는 PC마다 별도 job과 variables를 생성합니다.

상단 `저장`을 누르면 실행표도 master의 `rig-ftp.info`에 저장되고 다음 실행 때 복원됩니다. 비밀번호나 token처럼 파일에 남기면 안 되는 값은 저장 전에 비워 두고 실행 직전에 입력하십시오.

한 PC만 빠르게 실행할 때는 `빠른 실행`의 `대상 PC`, `입력값`, `선택 매크로 전송`을 사용합니다. 입력값 형식은 `channel=CH11 sequence="Seq 2"`입니다.

Win Automation Picker의 `배포 > PC별 실행표`에서도 같은 흐름을 사용할 수 있습니다. 연속 녹화 직후에는 현재 workflow의 변수 열과 녹화 기본값이 이미 준비되어 있습니다.
