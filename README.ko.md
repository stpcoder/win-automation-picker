# Win Automation Picker 한글 매뉴얼

Win Automation Picker는 Windows UI Automation 기반의 녹화/실행 도구입니다.
실제 마우스 클릭 아래에 있는 Windows UIA 요소를 selector로 저장하고,
클릭/입력/대기/키 입력 step을 workflow로 쌓은 뒤 반복 실행하거나 Python
스크립트로 export할 수 있습니다.

영문 문서: [README.md](README.md)

## 다운로드

최신 Windows GUI 실행 파일:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe

터미널 기반 실장기 제어 CLI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe

코드 서명이 없는 실행 파일이므로 Windows SmartScreen 경고가 뜰 수 있습니다.

## Win Automation Picker가 하는 일

- 다음 마우스 클릭 위치의 Windows UI Automation 요소를 찾습니다.
- `AutomationId`, `Name`, `ControlType`, `ClassName`, sibling index, window
  metadata, XPath 비슷한 path를 selector JSON으로 저장합니다.
- 현재 selector에 대해 단발성 `Click`, `Type`을 실행할 수 있습니다.
- `Record click step`, `Record type step`, `Add wait`, `Add key`로 workflow를
  녹화합니다.
- `Monitor` 탭에서 녹화 상태, 마지막 window/control, 클릭 좌표, step 수,
  실행 진행 상황을 확인합니다.
- 각 control에 agent가 쓰기 좋은 `element_id`, role, notes를 붙입니다.
- 같은 프로그램 창이 여러 개 떠 있어도 `Window marker`로 `CH 1`, `CH 2`
  같은 구분자를 넣어 특정 창을 고를 수 있습니다.
- `Window Debug` 탭에서 top-level window와 nested popup/dialog 후보를 비교합니다.
- 팝업/다이얼로그 내부 버튼도 UIA `Window`로 노출되면 selector를 저장하고 실행할 수 있습니다.
- selector JSON과 workflow JSON을 저장/로드합니다.
- workflow를 실행 가능한 Python 스크립트로 export합니다.
- 녹화된 step을 위/아래로 이동하거나 잘못 녹화된 step을 삭제합니다.
- Excel/Google Sheets에서 복사한 행 데이터를 붙여넣고 row별 반복 실행합니다.
- `${col1}`, `${col2}`, `${name}`, `${message}`, `${row}` 같은 템플릿 변수를 입력값에 사용할 수 있습니다.

주의할 점: 이 도구는 native Windows UIA 대상으로 설계되어 있습니다. 게임,
canvas 기반 앱, 브라우저 DOM 내부 요소, 자체 렌더링 UI는 UIA metadata가 빈약할 수
있어서 별도 adapter가 필요할 수 있습니다.

## 기본 사용 흐름

1. `WinAutomationPicker.exe`를 실행합니다.
2. `Element name`에 agent가 부를 이름을 적습니다. 예: `search_button`.
3. `Role`을 고릅니다. 버튼이면 `button`, 입력칸이면 `input`입니다. 모르면 `auto`로 둡니다.
4. 같은 프로그램 창이 여러 개라면 `Window marker`에 구분 텍스트를 넣습니다. 예: `CH 1`.
5. `Record click step`을 누릅니다.
6. 대상 프로그램에서 실제로 누를 버튼을 클릭합니다.
7. step이 녹화되면 `Steps` 탭과 `Workflow JSON`이 즉시 갱신됩니다.
8. 입력칸에 값을 넣어야 한다면 `Text / template`에 입력값을 적습니다. 예: `${message}`.
9. `Element name`을 `message_input`처럼 바꿉니다.
10. `Record type step`을 누른 뒤 대상 입력칸을 클릭합니다.
11. 엔터나 탭이 필요하면 `Add Enter` 또는 `Add key`를 사용합니다.
12. 순서가 틀렸으면 `Steps` 탭에서 step을 선택하고 `Move up`, `Move down`,
    `Delete step`으로 수정합니다.
13. 반복 실행할 데이터가 있으면 `Data rows` 탭에 붙여넣습니다.
14. 한 번만 실행하려면 `Run once`, 행별 반복 실행은 `Run rows`를 누릅니다.
15. agent가 실행할 Python 파일이 필요하면 `Export Python`을 누릅니다.

## 녹화 모델

이 도구는 계속 켜져서 모든 클릭을 기록하는 백그라운드 매크로 녹화기가 아닙니다.
각 녹화 버튼은 다음 외부 클릭 1개만 기다립니다.

- `Pick inspect`: 다음 클릭 위치의 selector만 가져옵니다. workflow step은 추가하지 않습니다.
- `Record click step`: 다음 클릭 위치의 UI 요소를 click step으로 저장하고 대기 상태를 종료합니다.
- `Record type step`: 다음 클릭 위치의 UI 요소를 type step으로 저장합니다. 녹화 중에 글자를 입력하지는 않습니다.
- `Add wait`: UI 요소 없이 지연 시간을 step으로 추가합니다.
- `Add Enter`: `{ENTER}` key step을 추가합니다.
- `Add key`: `{TAB}`, `^s`, `{ESC}` 같은 pywinauto key sequence를 직접 추가합니다.

여러 버튼을 녹화하려면 버튼마다 `Record click step`을 다시 눌러야 합니다.
클릭, 입력, 클릭, 입력 순서의 workflow라면 그 순서대로 녹화 버튼을 눌러 step을 쌓습니다.
녹화 대기 중 Win Automation Picker 내부를 클릭한 것은 무시됩니다.

## Type 이벤트 사용법

타이핑은 다음 흐름으로 사용합니다.

1. `Text / template`에 입력할 값을 적습니다.
2. 기존 값을 지우고 입력해야 하면 `Clear`를 체크합니다.
3. `Record type step`을 누릅니다.
4. 대상 프로그램의 입력칸을 클릭합니다.
5. workflow 실행 시 해당 selector를 찾아 focus를 잡고 텍스트를 넣습니다.

`Record type step`을 누른 직후 바로 글자가 입력되는 것이 아닙니다. 그 시점에는
"어느 입력칸에 입력할지"만 녹화합니다. 실제 입력은 `Run once`, `Run rows`, 또는
export된 Python script 실행 시 발생합니다.

기본 입력 방식은 clipboard paste입니다. 한글, 공백, 특수문자, 긴 문자열은 key를 하나씩
보내는 것보다 paste가 더 안정적입니다. 실행 중 clipboard 내용이 잠시 바뀔 수 있다는 점은
알아두어야 합니다.

템플릿 예시:

```text
고객명: ${name}
메시지: ${message}
행번호: ${row}
```

## 팝업/다이얼로그 버튼 인식

팝업 창 안의 버튼도 가능합니다. 단, 대상 프로그램이 그 팝업과 버튼을 Windows UI
Automation 요소로 노출해야 합니다.

권장 절차:

1. 대상 프로그램에서 팝업을 먼저 띄웁니다.
2. Win Automation Picker에서 `Pick inspect` 또는 `Record click step`을 누릅니다.
3. 팝업 안의 버튼을 클릭합니다.
4. `Monitor` 탭의 `Window`, `Target`, `Point`가 기대한 팝업/버튼인지 확인합니다.
5. `Window Debug`를 눌러 selector root 후보를 확인합니다.

`Window Debug`의 주요 컬럼:

- `SEL`: 실행 시 선택될 후보입니다.
- `ROOT`: selector의 root window 조건이 맞는지입니다.
- `MARK`: `Window marker` 조건이 맞는지입니다.
- `HINT`: 저장된 window handle이 해당 후보를 가리키는지입니다.
- `SCOPE=top`: 일반 desktop top-level window입니다.
- `SCOPE=nested`: 메인 프로그램 내부의 child popup/dialog window입니다.
- `DEPTH`: nested 후보가 메인 window 아래 몇 단계에 있는지입니다.

이번 버전부터 실행기는 일반 top-level window에서 root를 못 찾으면 각 window의 하위
UIA tree를 내려가 nested `Window` 후보도 찾습니다. 따라서 회사 전산망 프로그램 안에서
뜬 팝업이 `SCOPE=nested`로 보이더라도, 팝업이 열린 상태라면 그 안의 버튼을 실행할 수
있습니다.

그래도 안 되는 경우:

- 대상 앱이 관리자 권한으로 실행 중이면 Win Automation Picker도 관리자 권한으로 실행합니다.
- 팝업이 열려 있는 상태에서 녹화했는지 확인합니다.
- `Target`이 버튼이 아니라 상위 Pane/Document로 잡혔다면 버튼 중앙을 다시 클릭합니다.
- 같은 팝업이 여러 개라면 `Window marker`에 팝업 내부의 고유 텍스트를 넣습니다.
- 앱이 canvas/custom rendering이면 UIA metadata가 부족할 수 있습니다.

## Monitor 탭

녹화와 실행 중 반드시 같이 보는 탭입니다.

- `State`: `Idle`, `Armed`, `Running`, `Error` 상태입니다.
- `Mode`: 현재 모드입니다. 예: `Record next click step`.
- `Steps`: 녹화된 step 개수입니다.
- `Window`: 마지막으로 캡처한 root window입니다.
- `Target`: 마지막으로 캡처한 버튼, 입력칸, 메뉴 등 대상 control입니다.
- `Point`: 마지막 클릭 좌표입니다.
- 아래 리스트: 녹화 시작, step 추가, 실행 진행, stop 요청, error 로그입니다.

버튼을 클릭했는데 `Steps` 탭과 `Workflow JSON`이 변하지 않으면 녹화가 안 된 것입니다.
이 경우 `Monitor`와 에러 메시지를 먼저 확인합니다.

## Window marker와 여러 창 구분

같은 프로그램이 여러 개 떠 있거나, 같은 실장 관리 화면이 CH 1/CH 2/CH 3/CH 4처럼 여러
개 있으면 title/class만으로는 창을 구분하기 어렵습니다. 이때 `Window marker`를 씁니다.

예:

1. `Window marker`에 `CH 1`을 입력합니다.
2. `Record click step`으로 CH 1 창의 시작 버튼을 녹화합니다.
3. `Apply marker`를 누릅니다.
4. `Debug windows`를 눌러 `MARK=Y`, `SEL=*`가 맞는 창에 붙는지 확인합니다.
5. CH 2, CH 3, CH 4도 같은 방식으로 별도 element name을 붙입니다.

간단 UI의 `Window marker` 필드는 selector JSON의 `window_marker.name_contains`를 설정합니다.
고급 조건은 selector JSON을 직접 수정할 수 있습니다.

```json
"window_marker": {
  "name_contains": "CH 1",
  "automation_id": "channelLabel",
  "control_type": "Text",
  "class_name": "",
  "description": ""
}
```

비어 있지 않은 marker 조건은 같은 UIA component에서 모두 만족해야 합니다.

## Steps 탭

녹화된 workflow step을 사람이 읽기 쉬운 목록으로 보여줍니다.

- step 선택: selector와 metadata가 왼쪽 editor에 로드됩니다.
- `Move up`: 선택 step을 한 칸 위로 옮깁니다.
- `Move down`: 선택 step을 한 칸 아래로 옮깁니다.
- `Delete step`: 잘못 녹화된 step을 삭제합니다.
- 단축키: `Alt+Up`, `Alt+Down`, `Delete`.

step을 선택한 뒤 `Element name`, `Role`, `Notes`, `Window marker`를 바꾸고
`Apply to step`을 누르면 해당 step의 metadata가 갱신됩니다.

## Elements 탭

workflow에서 agent가 호출할 수 있는 reusable target 목록입니다.

각 줄은 대략 다음 정보를 보여줍니다.

```text
element_id | role | step 번호 | target metadata | marker
```

예:

```text
search_button | button | step 1 | Button | Name=Search | marker Name contains 'CH 1'
```

Python export 후 agent는 `click_element("search_button")`처럼 안정적인 이름으로 control을 호출할 수 있습니다.

## Data rows와 템플릿

`Data rows`에는 Excel/Google Sheets에서 복사한 TSV 형식 데이터를 붙여넣을 수 있습니다.

예:

```tsv
name	message
Alice	Hello Alice
Bob	Hello Bob
```

`First row headers`가 켜져 있으면 `${name}`, `${message}`를 사용할 수 있습니다.
항상 `${col1}`, `${col2}` 방식도 사용할 수 있습니다. header가 없는 데이터라면
`First row headers`를 끄고 `${col1}`, `${col2}`를 사용합니다.

`Row delay`는 각 행 실행 사이에 쉬는 시간입니다. 서버 응답이나 화면 전환이 느린 업무
프로그램에서는 0.5초에서 몇 초 정도를 넣는 것이 안정적입니다.

## Python export

`Export Python`은 현재 workflow를 실행 가능한 `.py` 파일로 저장합니다.

포함되는 내용:

- 녹화된 click/type/wait/key step
- agent용 `ELEMENTS` metadata
- selector JSON과 window marker
- 현재 `Data rows`
- `First row headers` 설정
- `Row delay`
- helper 함수: `list_elements()`, `click_element(id)`, `type_into(id, text)`, `press_key(keys)`

Windows에서 이 패키지가 설치된 환경에서 실행합니다.

```powershell
python -m pip install -e .
python .\exported_workflow.py
```

agent 사용 예:

```python
print(list_elements())
click_element("search_button")
type_into("message_input", "안녕하세요", clear=True)
press_key("{ENTER}")
```

## Selector-only 사용

workflow 녹화 없이 selector만 확인하고 싶을 때 사용합니다.

1. `Pick inspect`를 누릅니다.
2. 대상 control을 클릭합니다.
3. `Selector JSON`과 `XPath-like path`를 확인합니다.
4. `Click`, `Type`, `Copy selector`, `Save selector` 중 필요한 동작을 실행합니다.

## Selector JSON 구조

예시:

```json
{
  "backend": "uia",
  "root": {
    "control_type": "Window",
    "name": "Untitled - Notepad",
    "automation_id": "",
    "class_name": "Notepad",
    "index": 0
  },
  "path": [
    {
      "control_type": "Edit",
      "name": "Text Editor",
      "automation_id": "15",
      "class_name": "Edit",
      "index": 0
    }
  ],
  "root_handle": 123456,
  "process_id": 1234,
  "rect": {
    "left": 10,
    "top": 10,
    "right": 400,
    "bottom": 60
  },
  "picked_point": [100, 30],
  "window_marker": {
    "name_contains": "CH 1",
    "automation_id": "",
    "control_type": "Text",
    "class_name": "",
    "description": ""
  }
}
```

실행 시 handle은 빠른 hint로만 사용합니다. handle이 바뀌거나 틀리면 title/class/path와
window marker를 기준으로 다시 찾습니다.

## 소스에서 설치

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
win-automation-picker
rig-commander --help
```

## Rig Commander CLI

`RigCommander.exe`는 이 repository의 두 번째 프로그램입니다. 여러 Windows rig PC에
연결된 Android 실장기나 전원 제어 장치를 터미널에서 통제하기 위한 CLI입니다.
원격 데스크톱으로 접속해 SKCOMMANDER 같은 GUI를 여는 대신, PowerShell Remoting/WinRM과
COM port serial command를 이용해 명령을 보냅니다.

더블클릭하면 바로 닫히지 않고 interactive shell이 열립니다.

```text
rig>
```

사용 예:

```text
help
help firmware flash
exit
```

PowerShell에서 직접 command를 넘겨도 됩니다.

기본 구조:

- 업무 PC에서 `RigCommander.exe`를 실행합니다.
- 각 실장기 PC는 `rig-commander.config.json`에 등록합니다.
- 원격 PC는 PowerShell Remoting / WinRM으로 접속합니다.
- COM port 명령은 원격 PC에서 `.NET SerialPort`로 실행합니다.
- `power_on`, `power_off`, `reset`, `status` 같은 명령 문자열은 config에 저장합니다.

초기 config 생성:

```powershell
.\RigCommander.exe init-config -o rig-commander.config.json
```

config 예시:

```json
{
  "default_timeout_seconds": 12,
  "hosts": [
    {
      "id": "rig-pc-01",
      "address": "RIG-PC-01",
      "transport": "powershell",
      "tags": ["line-a"],
      "ports": [
        {
          "id": "ch1",
          "port": "COM3",
          "baud": 115200,
          "commands": {
            "status": "STATUS",
            "power_on": "POWER ON",
            "power_off": "POWER OFF",
            "reset": "RESET"
          }
        }
      ]
    }
  ]
}
```

자주 쓰는 명령:

```powershell
.\RigCommander.exe -c .\rig-commander.config.json list
.\RigCommander.exe -c .\rig-commander.config.json check --target all
.\RigCommander.exe -c .\rig-commander.config.json ports --target rig-pc-01
.\RigCommander.exe -c .\rig-commander.config.json exec --target tag:line-a --script "hostname"
.\RigCommander.exe -c .\rig-commander.config.json run status --target rig-pc-01:ch1
.\RigCommander.exe -c .\rig-commander.config.json run power_on --target tag:line-a --parallel
.\RigCommander.exe -c .\rig-commander.config.json send --target rig-pc-01:ch1 --command "STATUS"
.\RigCommander.exe -c .\rig-commander.config.json monitor --target all --name status --interval 5
```

target selector:

- `all`: 모든 enabled host/channel
- `rig-pc-01`: 특정 host의 모든 channel
- `rig-pc-01:ch1`: 특정 host의 특정 channel
- `tag:line-a`: 해당 tag가 붙은 모든 host

원격 host를 쓰려면 각 rig PC에서 WinRM이 활성화되어 있어야 하고, 회사 네트워크 정책상
허용되어야 합니다.

```powershell
Enable-PSRemoting -Force
```

SKCOMMANDER가 보내는 실제 serial command 문자열을 알아야 이 CLI에 넣을 수 있습니다.
SKCOMMANDER가 serial command가 아니라 DLL이나 별도 proprietary API만 사용한다면,
serial runner가 아니라 전용 adapter를 추가해야 합니다.

## Firmware download

RigCommander는 vendor firmware downloader 실행 파일을 config에 등록해서 firmware download
작업도 orchestration할 수 있습니다. GUI 도구가 XML manifest를 열고 image 목록을 로드한 뒤
`Download Only` 또는 `Format All + Download`를 수행하는 흐름을 CLI에서 감싸는 방식입니다.

config 예시:

```json
"firmware": {
  "executable": "C:\\Tools\\FirmwareDownloader\\FirmwareDownload.exe",
  "working_dir": "C:\\Tools\\FirmwareDownloader",
  "arguments": ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"],
  "mode_values": {
    "download-only": "download_only",
    "format-all-download": "format_all_download"
  },
  "timeout_seconds": 1800,
  "success_exit_codes": [0],
  "success_markers": ["Download OK"],
  "failure_markers": ["FAIL", "ERROR"]
}
```

명령 예:

```powershell
.\RigCommander.exe firmware inspect --xml C:\fw\firmware.xml
.\RigCommander.exe -c .\rig-commander.config.json firmware flash `
  --target rig-pc-01:ch1 `
  --xml C:\fw\firmware.xml `
  --mode download-only
.\RigCommander.exe -c .\rig-commander.config.json firmware flash `
  --target tag:line-a `
  --xml \\fileserver\firmware\build123\firmware.xml `
  --mode format-all-download `
  --parallel
```

`--xml` 경로는 rig PC에서 보이는 경로여야 합니다. 원격 flashing이면 원격 PC의 local path나
공유 UNC path를 사용합니다.

메모리 트레이닝 이후처럼 특정 boot-stage marker가 나온 뒤에만 firmware upload가 가능한
경우, downloader 실행 전에 serial status command를 polling할 수 있습니다.

```powershell
.\RigCommander.exe -c .\rig-commander.config.json firmware flash `
  --target rig-pc-01:ch1 `
  --xml \\fileserver\firmware\build123\firmware.xml `
  --mode download-only `
  --ready-command status `
  --ready-marker "MEMORY TRAINING PASS" `
  --ready-timeout 180
```

실제로 실행하기 전에 `--dry-run`으로 생성되는 remote PowerShell을 먼저 확인하는 것이 좋습니다.

## 빌드 파이프라인

GitHub Actions는 Linux에서 test를 돌리고, `windows-latest`에서 PyInstaller로
아이콘이 적용된 `WinAutomationPicker.exe`, `RigCommander.exe`를 빌드합니다.
빌드 결과는 artifact로 업로드되고 `latest` GitHub Release asset이 갱신됩니다.

## 제한 사항과 운영 팁

- 대상 app이 관리자 권한이면 이 도구도 관리자 권한으로 실행해야 합니다.
- UIA handle은 실행할 때마다 바뀔 수 있으므로 selector는 stable metadata를 우선 사용합니다.
- 한글 입력은 기본적으로 clipboard paste 방식이 안정적입니다.
- 팝업 버튼을 실행하려면 실행 시점에도 팝업이 열려 있어야 합니다.
- 화면 전환이 느린 업무 프로그램은 `Add wait` 또는 `Row delay`를 적극적으로 넣는 편이 안정적입니다.
- Windows 전용 도구입니다.
