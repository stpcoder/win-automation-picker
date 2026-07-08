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

## 6. macro 실행

1. `Macro Library`에서 macro를 선택합니다.
2. `Run on Slaves > Target`에 `all`, `PC04`, `rig-pc-04` 등을 입력합니다.
3. 필요한 인자가 있으면 `Args`에 입력합니다.
4. 변수 override가 필요하면 `Vars`에 입력합니다. 예: `channel=CH11`.
5. `Submit macro`를 누릅니다.
