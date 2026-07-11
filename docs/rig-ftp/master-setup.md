# Master 세팅

## 1. Connection Setup 입력

`RigFtpCommander.exe`를 실행하고 `Connection Setup` 탭을 엽니다.

| 필드 | 예시 | 설명 |
| --- | --- | --- |
| `FTP host` | `192.168.0.10` | FTP 서버 주소 |
| `Port` | `21` | FTP 포트 |
| `Username` | `macro_user` | FTP 계정 |
| `Password` | `******` | FTP 비밀번호 |
| `Root dir` | `/win_automation_macros` | spool root |
| `Node ID` | `master-pc` | master 자신의 식별값 |
| `Poll sec` | `5` | slave polling 기본값 |
| `Work dir` | `rig-ftp-work` | slave 작업 폴더 |

입력 후 상단 `Save`를 누르면 `rig-ftp.info`가 저장됩니다.

## 2. slave 목록 입력

`Slave roster JSON`에 slave 정보를 넣습니다.

```json
[
  {
    "node_id": "rig-pc-04",
    "alias": "PC04",
    "host": "192.168.0.104",
    "port": 0,
    "notes": "Line A channel 4",
    "variables": {
      "channel": "CH4"
    }
  }
]
```

`alias`는 사람이 보는 이름이고 target 입력에도 사용할 수 있습니다.

## 3. FTP 폴더 초기화

1. `Monitor & Run` 탭을 엽니다.
2. `Known slave nodes`에 slave node를 입력합니다. 예: `PC04 PC05 PC06`.
3. `Init folders`를 누릅니다.

## 4. slave .info 만들기

1. `Monitor & Run > Server Setup > More`를 엽니다.
2. `Export slave .info`를 누릅니다.
3. 출력 폴더를 선택합니다.
4. 생성된 각 폴더의 `rig-ftp.info`를 해당 slave PC에 복사합니다.

## 5. macro 업로드

이미 export한 Python macro가 있다면:

1. `Macro Upload > File`에서 `.py` 파일을 선택합니다.
2. `Package name`, `Title`, `Notes`를 입력합니다.
3. `Upload macro`를 누릅니다.
4. `Macro Library`에 표시되는지 확인합니다.

Win Automation Picker에서 현재 블록 workflow를 바로 업로드하려면 `WinAutomationPicker.exe > Deploy` 탭을 사용합니다.

## 6. PC별 macro와 입력값 실행

1. `Macro Library`에서 macro를 선택합니다.
2. `PC별 매크로 실행표 > 설정 PC 불러오기`를 누릅니다.
3. package metadata에 저장된 입력 변수 열이 자동으로 생겼는지 확인합니다.
4. 각 행을 더블클릭해 PC별 매크로와 값을 바꿉니다. 예: `PC01 = Seq 1`, `PC02 = Seq 2`.
5. 실행하지 않을 PC는 첫 `실행` 셀을 클릭해 체크를 끕니다.
6. `실행표 전송`을 누릅니다. Master는 PC마다 별도 job과 variables를 생성합니다.

상단 `Save`를 누르면 실행표도 master의 `rig-ftp.info`에 저장되고 다음 실행 때 복원됩니다. 비밀번호나 token처럼 파일에 남기면 안 되는 값은 저장 전에 비워 두고 실행 직전에 입력하십시오.

한 PC만 빠르게 실행할 때는 기존 `Run on Slaves` 영역의 `Target`, `Args`, `Vars`, `Submit macro`를 사용할 수 있습니다. `Vars` 형식은 `channel=CH11 sequence="Seq 2"`입니다.

Win Automation Picker의 `배포 > PC별 실행표`에서도 같은 흐름을 사용할 수 있습니다. 연속 녹화 직후에는 현재 workflow의 변수 열과 녹화 기본값이 이미 준비되어 있습니다.
