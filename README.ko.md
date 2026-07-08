# Win Automation Picker 한글 매뉴얼

Win Automation Picker는 Windows UI Automation 기반의 녹화/실행 도구입니다.
실제 마우스 클릭 아래에 있는 Windows UIA 요소를 selector로 저장하고,
클릭/입력/대기/키 입력 step을 workflow로 쌓은 뒤 반복 실행하거나 Python
스크립트로 export할 수 있습니다.

영문 문서: [README.md](README.md)

GitBook형 온라인 매뉴얼:

https://stpcoder.github.io/win-automation-picker/

## 다운로드

최신 Windows GUI 실행 파일:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe

터미널 기반 실장기 제어 CLI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe

FTP 기반 master/slave orchestration GUI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe

FTP 기반 master/slave 고급 CLI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe

코드 서명이 없는 실행 파일이므로 Windows SmartScreen 경고가 뜰 수 있습니다.

## Win Automation Picker가 하는 일

- 다음 마우스 클릭 위치의 Windows UI Automation 요소를 찾습니다.
- `AutomationId`, `Name`, `ControlType`, `ClassName`, sibling index, window
  metadata, XPath 비슷한 path를 selector JSON으로 저장합니다.
- 현재 selector에 대해 단발성 `Click`, `Type`을 실행할 수 있습니다.
- `Click block`, `Type block`, `Wait`, `Custom key`로 workflow를
  녹화합니다.
- `Run Log` 탭에서 녹화 상태, 마지막 window/control, 클릭 좌표, step 수,
  실행 진행 상황을 확인합니다.
- 각 control에 agent가 쓰기 좋은 `element_id`, role, notes를 붙입니다.
- 같은 프로그램 창이 여러 개 떠 있어도 `Window identity`로 `CH 1`, `CH 2`
  같은 구분자를 넣어 특정 창을 고를 수 있습니다.
- `Windows` 탭에서 top-level window와 nested popup/dialog 후보를 비교합니다.
- 팝업/다이얼로그 내부 버튼도 UIA `Window`로 노출되면 selector를 저장하고 실행할 수 있습니다.
- selector JSON과 workflow JSON을 저장/로드합니다.
- workflow를 실행 가능한 Python 스크립트로 export합니다.
- `Build` 탭에서 팔레트, 작업공간, 선택 블록 편집 영역으로 녹화된 동작을
  Scratch 스타일의 컬러 블록처럼 보고 편집합니다.
- 블록 이름/색을 바꾸고, 이벤트 타입별 또는 창별 색상으로 볼 수 있으며, 반복/조건 블록을 만들 수 있습니다.
- `Capture Quality`로 헛 캡처, 약한 selector, action에 맞지 않는 target을 바로 확인합니다.
- 녹화된 step을 위/아래로 이동하거나 잘못 녹화된 step을 삭제합니다.
- `Deploy` 탭에서 현재 block workflow를 제목/노트가 있는 macro package로 FTP 서버에 업로드하고,
  서버의 macro list를 다시 불러올 수 있습니다.
- Excel/Google Sheets에서 복사한 행 데이터를 붙여넣고 row별 반복 실행합니다.
- `${col1}`, `${col2}`, `${name}`, `${message}`, `${row}` 같은 템플릿 변수를 입력값에 사용할 수 있습니다.
- 사내망에서 custom inbound port를 열 수 없을 때 `RigFtpCommander.exe`로 FTP 기반
  master/slave job 배포와 로그 수집을 수행합니다.

주의할 점: 이 도구는 native Windows UIA 대상으로 설계되어 있습니다. 게임,
canvas 기반 앱, 브라우저 DOM 내부 요소, 자체 렌더링 UI는 UIA metadata가 빈약할 수
있어서 별도 adapter가 필요할 수 있습니다.

## 기본 사용 흐름

1. `WinAutomationPicker.exe`를 실행합니다.
2. `Target Setup`의 `Name`에 agent가 부를 이름을 적습니다. 예: `search_button`.
3. `Type`을 고릅니다. 버튼이면 `button`, 입력칸이면 `input`입니다. 모르면 `auto`로 둡니다.
4. 같은 프로그램 창이 여러 개라면 `Window match`에 구분 텍스트를 넣습니다. 예: `CH 1`.
5. `Click block`을 누릅니다.
6. 대상 프로그램에서 실제로 누를 버튼을 클릭합니다.
7. step이 녹화되면 `Sequence` 탭과 `Workflow JSON`이 즉시 갱신됩니다.
8. 입력칸에 값을 넣어야 한다면 `Input` 영역의 `Text`에 입력값을 적습니다. 예: `${message}`.
9. `Name`을 `message_input`처럼 바꿉니다.
10. `Type block`을 누른 뒤 대상 입력칸을 클릭합니다.
11. 엔터나 탭이 필요하면 `Build > Action` 또는 `More > Actions`에서 `Press Enter`나 `Custom key`를 사용합니다.
12. `Build` 탭에서 블록 이름/색을 바꾸거나 선택 블록을 반복/조건 블록으로 감쌉니다.
13. 순서가 틀렸으면 `Sequence` 탭에서 step을 선택하고 `Move up`, `Move down`,
    `Delete step`으로 수정합니다.
14. 반복 실행할 데이터가 있으면 `Data Rows` 탭에 붙여넣습니다.
15. 한 번만 실행하려면 `Run once`, 행별 반복 실행은 `Run rows`를 누릅니다.
16. agent가 실행할 Python 파일이 필요하면 `Export Python`을 누릅니다.

## 녹화 모델

이 도구는 계속 켜져서 모든 클릭을 기록하는 백그라운드 매크로 녹화기가 아닙니다.
각 녹화 버튼은 다음 외부 클릭 1개만 기다립니다.

- `Inspect`: 다음 클릭 위치의 selector만 가져옵니다. workflow step은 추가하지 않습니다.
- `Click block`: 다음 클릭 위치의 UI 요소를 click step으로 저장하고 대기 상태를 종료합니다.
- `Type block`: 다음 클릭 위치의 UI 요소를 type step으로 저장합니다. 녹화 중에 글자를 입력하지는 않습니다.
- `Wait`: UI 요소 없이 지연 시간을 step으로 추가합니다.
- `Press Enter`: `{ENTER}` key step을 추가합니다.
- `Custom key`: `{TAB}`, `^s`, `{ESC}` 같은 pywinauto key sequence를 직접 추가합니다.

여러 버튼을 녹화하려면 버튼마다 `Click block`을 다시 눌러야 합니다.
클릭, 입력, 클릭, 입력 순서의 workflow라면 그 순서대로 녹화 버튼을 눌러 step을 쌓습니다.
녹화 대기 중 Win Automation Picker 내부를 클릭한 것은 무시됩니다.

## Type 이벤트 사용법

타이핑은 다음 흐름으로 사용합니다.

1. `Input` 영역의 `Text`에 입력할 값을 적습니다.
2. 기존 값을 지우고 입력해야 하면 `Clear`를 켭니다. 기본값은 켜짐입니다.
3. 기본 `Method`는 `paste`입니다. 대상 프로그램이 붙여넣기를 막으면 `keys`로 바꿉니다.
4. `Type block`을 누릅니다.
5. 대상 프로그램의 입력칸을 클릭합니다.
6. workflow 실행 시 해당 selector를 찾아 focus를 잡고 텍스트를 넣습니다.

`Type block`을 누른 직후 바로 글자가 입력되는 것이 아닙니다. 그 시점에는
"어느 입력칸에 입력할지"만 녹화합니다. 실제 입력은 `Run once`, `Run rows`, 또는
export된 Python script 실행 시 발생합니다.

기본 입력 방식은 clipboard paste입니다. 한글, 공백, 특수문자, 긴 문자열은 key를 하나씩
보내는 것보다 paste가 더 안정적입니다. 실행 중 clipboard 내용이 잠시 바뀔 수 있다는 점은
알아두어야 합니다. 반대로 대상 프로그램이 Ctrl+V 붙여넣기를 막는다면 `Method`를
`keys`로 바꿔서 키 입력 방식으로 실행합니다.

녹화할 때 Windows UI Automation이 `Button`이나 `Edit` 안쪽의 `Text` 자식을 잡는 경우가
있습니다. 이 경우 click step은 실제 `Button`으로, type step은 실제 `Edit`으로 자동 보정해서
재생 안정성을 높입니다.

템플릿 예시:

```text
고객명: ${name}
메시지: ${message}
행번호: ${row}
```

## Build 탭

`Build` 탭은 시각적인 sequence 편집기입니다. 작은 블록 코딩 작업공간처럼 구성되어 있습니다.

- `Add Blocks`: click/type capture, wait, Enter, custom key, repeat,
  condition, monitor group 같은 블록 생성 작업을 한곳에서 실행합니다.
- `Macro Workspace`: 녹화된 click/type/wait/key step이 번호가 붙은 컬러 블록으로 보입니다.
  선택된 블록은 노란 테두리로 표시됩니다.
- `Selected Block`: 선택한 블록의 이름, 고정 색, `Color by`, repeat count를 바로 바꿉니다.
- `Color by=event`: click, type, key, wait, repeat 이벤트 타입별로 색을 다르게 봅니다.
- `Color by=window`: selector의 root window나 `Window identity` 기준으로 창별 색을 다르게 봅니다.
- `Repeat selected`: 선택 블록을 반복 컨테이너로 감쌉니다.
- `If selected exists`: 선택 블록을 조건 컨테이너로 감쌉니다. 실행 시 해당 target이
  찾아질 때만 내부 블록을 실행합니다.
- `If selected text`: 선택 블록을 target text 조건으로 감쌉니다. 예: 상태 칸 text가
  `PASS`를 포함할 때만 다음 블록 실행.
- `If selected color`: 선택 블록을 화면 색상 조건으로 감쌉니다. 클릭 당시 component의
  상대 위치를 기준으로 현재 화면 색상을 읽고 `#RRGGBB`와 tolerance로 비교합니다.
- `Monitor text`, `Monitor color`: 실행 흐름을 바꾸지 않고 상태만 OK/FAIL로 기록하는
  모니터링 블록입니다.
- `Group AND`, `Group OR`: `Sequence` 탭에서 여러 조건/모니터 블록을 선택한 뒤 하나의
  모니터링 그룹 블록으로 묶습니다. AND는 모든 조건이 맞아야 OK, OR은 하나 이상 맞으면 OK입니다.
- `Monitor Mapping`의 `Board`, `CH`, `State` 필드: monitor dashboard에서 이 블록을 어느 보드,
  어느 채널, 어떤 상태명으로 보여줄지 지정하는 metadata입니다.
- `Dashboard` 탭: 현재 workflow 안의 monitor/condition/group 블록을 tab, CH, state,
  logic 기준으로 미리 보여줍니다.
- `Board Channels`: `CH9, CH10, CH11, CH12`처럼 장비별 채널 label을
  직접 넣고 선택한 monitor block에 순서대로 적용합니다. CH 개념이 없는 장비는 `Clear CH`로
  채널 metadata를 비워둘 수 있습니다.
- `Board Layout`: 모니터링 화면 자체를 커스터마이즈합니다. 행 축을
  `channel`, 열 축을 `state`로 두면 Excel처럼 `CH x 상태` 표가 되고, 반대로 행을 `state`,
  열을 `channel`로 바꾸면 상태 중심 표가 됩니다. `Tab order`, `State order`, `CH list`로
  표시 순서도 직접 조정할 수 있습니다.
- `Unwrap`: 반복/조건 컨테이너를 풀고 내부 블록은 유지합니다.
- `Run once`, `Run rows`: 상단 `Run` 영역에서 실행합니다.
- `Export Python`: `More > Actions`에서 실행 가능한 Python script로 내보냅니다.

새로 녹화하거나 추가한 블록은 자동 선택되므로 곧바로 이름과 색을 붙일 수 있습니다.
반복 블록은 workflow JSON에서 `kind: "repeat"`과 `children`으로 저장됩니다.
조건 블록은 `kind: "if_exists"`, `kind: "if_text"`, `kind: "if_color"`와 `children`으로
저장됩니다. 모니터링 블록은 `kind: "monitor_text"`, `kind: "monitor_color"`로 저장됩니다.
따라서 기존 JSON 저장/로드, 실행, Python export 흐름과 같이 사용할 수 있습니다.

색상/텍스트 모니터링 macro 예:

1. `Click block` 또는 `Inspect`로 상태 component를 잡습니다.
2. `Monitor color`를 눌러 기대 색상을 입력합니다. 파랑이면 `#0000FF`, 빨강이면 `#FF0000`
   같은 식으로 입력하고 tolerance를 조절합니다.
3. 필요하면 `Monitor text`로 상태 칸 text가 `PASS`, `FAIL`, `READY`인지도 체크합니다.
4. `Sequence` 탭에서 CH 식별 조건과 상태 조건을 여러 개 선택하고 `Group AND` 또는 `Group OR`로
   하나의 그룹 블록으로 묶습니다.
5. 선택 블록의 `Tab`, `CH`, `State`를 예를 들어 `SK Commander`, `CH1`, `RUNNING`처럼 지정합니다.
6. `Dashboard`에서 `Board Layout`을 조정해 `Board Preview`가 원하는 표 구조인지 확인합니다.
7. 이 workflow를 Python으로 export하거나 `Deploy` 탭에서 바로 업로드합니다.
8. slave에서 실행되면 stdout/result log에 `MONITOR OK` 또는 `MONITOR FAIL`이 남습니다.

SK Commander처럼 같은 exe가 4개 떠 있고 창 내부에 `CH1`, `CH2`, `CH3`, `CH4` 또는
`CH9`, `CH10`, `CH11`, `CH12` 표시가 있는 경우에는 각 selector에 `Window identity`를 적용한 뒤,
해당 창 안의 색상/텍스트 component를 monitor block으로 만들면 됩니다. `Group AND`를 쓰면
“이 창은 CH11이고 상태 색이 파랑” 같은 식으로 창 식별 조건과 상태 조건을 하나의 블록으로
묶을 수 있습니다.

`Window identity` mode는 다음처럼 고릅니다.

- `contains`: `Device CH11 Ready`처럼 긴 문자열 안에 포함된 값을 빠르게 찾을 때 사용합니다.
- `equals`: `CH1`이 `CH11`에 잘못 붙으면 안 되는 경우 사용합니다.
- `regex`: `CH 11`, `Ch11`, `ch   11`처럼 공백/대소문자가 섞일 수 있으면
  `\bch\s*11\b` 같은 정규식을 사용합니다.

## Capture Quality

`Capture Quality`는 Build 작업공간과 `Capture` 탭에 같이 표시됩니다. 복잡한 사내 전산망
화면에서 헛 캡처를 빨리 구분하기 위한 패널입니다.

- `Good capture`: 클릭 좌표가 UIA rectangle 안에 있고, action에 맞는 control type이며,
  target 식별 metadata가 충분합니다.
- `Check`: 실행될 수는 있지만 `Window identity` 없음, root metadata 약함, click 대상 type이
  일반적이지 않음 같은 주의점이 있습니다.
- `Needs review`: type step인데 input이 아니거나, 클릭 좌표가 UIA rectangle 밖이거나,
  target의 AutomationId/Name/ClassName이 부족해 헛 캡처일 가능성이 큽니다.

녹화 중 UIA가 `Button`이나 `Edit` 안쪽의 `Text` child를 잡으면, replay가 안정적인
상위 control로 보정했다는 내용도 여기에서 확인할 수 있습니다.

## 팝업/다이얼로그 버튼 인식

팝업 창 안의 버튼도 가능합니다. 단, 대상 프로그램이 그 팝업과 버튼을 Windows UI
Automation 요소로 노출해야 합니다.

권장 절차:

1. 대상 프로그램에서 팝업을 먼저 띄웁니다.
2. Win Automation Picker에서 `Inspect` 또는 `Click block`을 누릅니다.
3. 팝업 안의 버튼을 클릭합니다.
4. `Run Log` 탭의 `Window`, `Target`, `Point`가 기대한 팝업/버튼인지 확인합니다.
5. `Windows`를 눌러 selector root 후보를 확인합니다.

`Windows`의 주요 컬럼:

- `SEL`: 실행 시 선택될 후보입니다.
- `ROOT`: selector의 root window 조건이 맞는지입니다.
- `MARK`: `Window identity` 조건이 맞는지입니다.
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
- 같은 팝업이 여러 개라면 `Window match`에 팝업 내부의 고유 텍스트를 넣습니다.
- 앱이 canvas/custom rendering이면 UIA metadata가 부족할 수 있습니다.

## Run Log 탭

녹화와 실행 중 반드시 같이 보는 탭입니다.

- `State`: `Idle`, `Armed`, `Running`, `Error` 상태입니다.
- `Mode`: 현재 모드입니다. 예: `Record next click step`.
- `Steps`: 녹화된 step 개수입니다. 화면에서는 `Sequence` 탭에서 확인합니다.
- `Window`: 마지막으로 캡처한 root window입니다.
- `Target`: 마지막으로 캡처한 버튼, 입력칸, 메뉴 등 대상 control입니다.
- `Point`: 마지막 클릭 좌표입니다.
- 아래 리스트: 녹화 시작, step 추가, 실행 진행, stop 요청, error 로그입니다.

버튼을 클릭했는데 `Sequence` 탭과 `Workflow JSON`이 변하지 않으면 녹화가 안 된 것입니다.
이 경우 `Run Log`와 에러 메시지를 먼저 확인합니다.

## Window identity와 여러 창 구분

같은 프로그램이 여러 개 떠 있거나, 같은 실장 관리 화면이 CH 1/CH 2/CH 3/CH 4처럼 여러
개 있으면 title/class만으로는 창을 구분하기 어렵습니다. 이때 `Window identity`를 씁니다.

예:

1. `Window match`에 `CH 1`을 입력합니다.
2. `Click block`으로 CH 1 창의 시작 버튼을 녹화합니다.
3. `Apply match`를 누릅니다.
4. `Test windows`를 눌러 `MARK=Y`, `SEL=*`가 맞는 창에 붙는지 확인합니다.
5. CH 2, CH 3, CH 4도 같은 방식으로 별도 element name을 붙입니다.

`Window identity`는 mode에 따라 selector JSON의 `window_marker.name_contains`,
`window_marker.name_equals`, `window_marker.name_regex` 중 하나를 설정합니다.
고급 조건은 selector JSON을 직접 수정할 수 있습니다.

```json
"window_marker": {
  "name_regex": "\\bch\\s*11\\b",
  "automation_id": "channelLabel",
  "control_type": "Text",
  "class_name": "",
  "description": ""
}
```

비어 있지 않은 marker 조건은 같은 UIA component에서 모두 만족해야 합니다.

## Sequence 탭

녹화된 workflow step을 사람이 읽기 쉬운 목록으로 보여줍니다.

- step 선택: selector와 metadata가 왼쪽 editor에 로드됩니다.
- `Move up`: 선택 step을 한 칸 위로 옮깁니다.
- `Move down`: 선택 step을 한 칸 아래로 옮깁니다.
- `Delete step`: 잘못 녹화된 step을 삭제합니다.
- 단축키: `Alt+Up`, `Alt+Down`, `Delete`.

step을 선택한 뒤 `Name`, `Type`, `Note`, `Window match`를 바꾸고
`Apply`를 누르면 해당 step의 metadata가 갱신됩니다.

## Targets 탭

workflow에서 agent가 호출할 수 있는 reusable target 목록입니다.

각 줄은 대략 다음 정보를 보여줍니다.

```text
element_id | role | step 번호 | target metadata | marker
```

예:

```text
search_button | button | step 1 | Button | Name=Search | marker Name regex '\\bch\\s*1\\b'
```

Python export 후 agent는 `click_element("search_button")`처럼 안정적인 이름으로 control을 호출할 수 있습니다.

## Data Rows와 템플릿

`Data Rows`에는 Excel/Google Sheets에서 복사한 TSV 형식 데이터를 붙여넣을 수 있습니다.

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

- 녹화된 click/type/wait/key/repeat/condition step
- agent용 `ELEMENTS` metadata
- selector JSON과 window marker
- 현재 `Data Rows`
- `First row headers` 설정
- `Row delay`
- helper 함수: `list_elements()`, `click_element(id)`, `type_into(id, text, method="paste")`, `element_exists(id)`, `press_key(keys)`

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
type_into("message_input", "안녕하세요", clear=True, method="keys")
if element_exists("optional_popup_ok"):
    click_element("optional_popup_ok")
press_key("{ENTER}")
```

## Selector-only 사용

workflow 녹화 없이 selector만 확인하고 싶을 때 사용합니다.

1. `Inspect`를 누릅니다.
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
    "name_regex": "\\bch\\s*1\\b",
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
rig-ftp --help
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

## FTP Master/Slave Orchestration

`RigFtpCommander.exe`는 이 repository의 세 번째 프로그램입니다. 각 rig/slave PC가
사내 FTP 서버에는 접근할 수 있지만 RPC, socket, agent용 inbound port를 새로 열 수 없을 때
사용합니다.

기본 사용은 CLI가 아니라 GUI입니다. `RigFtpCommander.exe`를 그냥 실행하면
`Monitor & Run`, `This PC Agent`, `Connection Setup` 탭이 있는 창이 열립니다.
PowerShell 명령은 `RigFtpCli.exe`를 사용하는 고급 자동화나 Task Scheduler용으로
남아 있습니다.

FTP 서버는 공유 spool처럼 사용합니다.

- `packages/`: master가 올리는 exported macro script나 helper script
- `commands/{node}/pending/`: 특정 slave에게만 전달되는 job
- `commands/all/pending/`: 모든 slave가 보는 broadcast job
- `status/`: slave별 heartbeat JSON
- `results/{node}/`: 완료된 job result JSON
- `logs/{node}/`: 완료된 job의 stdout/stderr log
- `archive/{node}/`: slave가 소비한 command JSON

권장 GUI 구성:

1. master PC에서 `RigFtpCommander.exe`를 실행합니다.
2. `Connection Setup` 탭에서 FTP host, username, password, root dir을 넣고 `Save`합니다.
3. `Monitor & Run` 탭에서 slave node id들을 입력하고 `Init folders`를 한 번 누릅니다.
4. `WinAutomationPicker.exe`의 `Deploy` 탭에서 현재 block workflow를 제목/노트와 함께 업로드하거나,
   `RigFtpCommander.exe`의 `Monitor & Run` 탭에서 Python macro file을 업로드합니다.
5. `Monitor & Run` 탭의 `Server Setup > More > Export slave .info`로 slave별 `rig-ftp.info`를 만듭니다.
6. 각 slave PC에는 `RigFtpCommander.exe`와 해당 PC용 `rig-ftp.info`를 같은 폴더에 둡니다.
   이 파일 안의 `Node ID`만 slave마다 고유하면 됩니다.
7. 각 slave PC에서 `This PC Agent` 탭의 `Start agent`를 누르면 해당 PC가 FTP 명령을 polling합니다.
8. master PC의 `Monitor & Run` 탭에서 macro list를 새로고침하고, target을 고른 뒤 `Submit macro`로
   실행합니다. `Refresh status`, `Refresh results`, `View screenshot`, `Emergency stop`으로 모니터링합니다.

GUI에서는 `Connection Setup` 탭에서 값을 채우고 `Save`를 누르면 됩니다. 빈 config가
필요하면 상단 `Connection Profile`의 `Example`을 누릅니다. 같은 작업을 PowerShell에서 하려면:

```powershell
.\RigFtpCli.exe init-config -o rig-ftp.info
```

FTP 주소, 계정, root folder, slave별 `node_id`를 수정합니다. `slaves`는 master가 보기 위한
roster입니다. `alias`는 `PC04`처럼 사람이 보는 이름이고, target 입력에도 사용할 수 있습니다.
`host`/`port`는 식별/문서화용 metadata이며 slave에 직접 socket 연결을 여는 값이 아닙니다.
실제 통신은 계속 FTP spool을 통해서만 이뤄집니다.

```json
{
  "ftp": {
    "host": "192.168.0.10",
    "port": 21,
    "username": "macro_user",
    "password": "change-me",
    "password_env": "",
    "root_dir": "/win_automation_macros",
    "tls": false,
    "passive": true
  },
  "runtime": {
    "node_id": "rig-pc-01",
    "poll_interval_seconds": 5,
    "poll_jitter_seconds": 3,
    "min_screenshot_interval_seconds": 30,
    "work_dir": "rig-ftp-work",
    "python_executable": "python",
    "capture_on_error": true,
    "max_output_chars": 200000,
    "max_result_files": 200,
    "max_log_files": 200,
    "max_archive_files": 500,
    "max_screenshot_files": 20
  },
  "variables": {
    "channel": "ch1",
    "line": "line-a"
  },
  "slaves": [
    {
      "node_id": "rig-pc-04",
      "alias": "PC04",
      "host": "192.168.0.104",
      "port": 0,
      "notes": "Line A channel 4",
      "variables": {
        "channel": "ch4"
      }
    }
  ]
}
```

`-c`를 넘기지 않으면 CLI와 GUI는 현재 folder와 exe가 있는 folder에서 `rig-ftp.info`를
먼저 찾고, 없으면 기존 `rig-ftp.config.json`도 찾습니다. 따라서 slave PC에서는
`RigFtpCommander.exe`와 `rig-ftp.info`를 같은 folder에 두고 실행하면 설정이 바로 로드됩니다.
고급 CLI 모드에서는 `RigFtpCli.exe`도 같은 규칙을 사용합니다.

```powershell
.\RigFtpCli.exe slave
```

GUI에서는 `Monitor & Run` 탭의 `Init folders`를 사용합니다. PowerShell에서 초기화하려면:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info init-server --node rig-pc-01 --node rig-pc-02
```

GUI에서는 `WinAutomationPicker.exe`의 `Deploy` 탭에서 `Upload current macro`를 누르거나,
`RigFtpCommander.exe`의 `Monitor & Run` 탭에서 file/title/notes를 채우고 `Upload macro`를 누릅니다.
PowerShell에서 export한 macro script를 업로드하려면:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info deploy `
  --file .\exported_workflow.py `
  --name smoke.py `
  --title "Boot smoke" `
  --notes "Power on, login, and basic status check"

.\RigFtpCli.exe -c .\rig-ftp.info packages
```

각 slave PC에서는 GUI의 `This PC Agent` 탭에서 `Start agent`를 누르는 것이 기본입니다.
PowerShell에서 polling 실행:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info slave
```

master PC에서 job 제출:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info submit-python `
  --target rig-pc-01 `
  --package smoke.py `
  --var case=boot_smoke `
  --arg "[case]" `
  --arg "[channel]"

.\RigFtpCli.exe -c .\rig-ftp.info submit-shell `
  --target all `
  --command "hostname"

.\RigFtpCli.exe -c .\rig-ftp.info submit-rig `
  --target rig-pc-01 `
  -- -c .\rig-commander.config.json run status --target local-rig:ch1
```

job command와 arg에는 `[name]`, `{name}` placeholder를 사용할 수 있습니다. 값은 slave
config의 `variables`, 자동으로 들어가는 `node_id`, job 제출 시 넣는 `--var KEY=VALUE`에서
옵니다.

비밀번호는 config의 `password`에 직접 넣거나, `password_env`에 `RIG_FTP_PASSWORD` 같은
환경변수 이름을 넣어서 분리할 수 있습니다.

master PC에서 모니터링:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info status
.\RigFtpCli.exe -c .\rig-ftp.info monitor --interval 5
.\RigFtpCli.exe -c .\rig-ftp.info results --node-id rig-pc-01
```

GUI에서는 `Monitor & Run` 탭에서 monitor 전용 macro를 선택하고 `Monitor sec`를 정한 뒤
`Start monitor loop`를 누르면 됩니다. master가 지정 interval마다 해당 macro를 submit하고,
각 slave는 `Monitor color/text` 블록 결과를 stdout/result log로 FTP에 올립니다.
`Monitor sec`는 최소 10초로 제한됩니다. FTP를 실시간 socket처럼 쓰지 않고 저속 spool로 쓰기
위한 안전장치입니다.

`Refresh status`를 누르면 slave 상태표가 갱신되고, GUI에 최신 status 로드 시각과 row 수가
표시됩니다. `Refresh results`는 선택한 node의 result log를 수동으로 읽고 최신 result 로드
시각을 표시합니다. `More > Export Excel`은 현재 상태표를 `.xlsx`로 저장합니다. 상태표에서
slave row를 더블클릭하거나 `View screenshot`을 누르면 master가 screenshot job을 FTP에
남기고, 해당 slave가 전체 화면을 원본 PNG 크기로 캡처해서 `screenshots/{node}/`에 업로드합니다.
master는 새 PNG를 읽어서 창으로 표시합니다.

긴급 중단과 화면 모니터링:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info stop --target rig-pc-01 --reason "wrong sequence"
.\RigFtpCli.exe -c .\rig-ftp.info clear-stop --target rig-pc-01
.\RigFtpCli.exe -c .\rig-ftp.info screenshot --target rig-pc-01 --label before_debug
.\RigFtpCli.exe -c .\rig-ftp.info screenshots --node-id rig-pc-01
```

`stop`은 실행 중인 `submit-shell`, `submit-python` child process를 종료합니다.
`submit-rig` job은 현재 process 안에서 실행되므로, 멈출 수 없는 rig command가 있다면
짧은 `--timeout`을 같이 설정하는 편이 안전합니다.

스크린샷은 PowerShell로 Windows virtual desktop 전체를 캡처해서 `screenshots/{node}/`에
업로드합니다. slave process가 사용자의 interactive desktop session에서 실행 중이어야 하며,
session 0의 Windows service에서는 실제 사용자 화면 캡처가 안 될 수 있습니다.

운영 부하 기준:

- FTP 방식은 초 단위 실시간 통신이 아니라 10-60초 단위 command/result spool에 적합합니다.
- 상태 refresh, result 조회, screenshot은 필요한 시점에만 수행하는 것이 좋습니다.
- screenshot은 파일 크기가 크므로 on-demand로 요청하고, `max_screenshot_files` retention을 낮게 둡니다.
  `min_screenshot_interval_seconds`는 같은 target에 대한 반복 screenshot 요청을 제한합니다.
- slave idle loop는 기본적으로 FTP list/read/write 위주라 CPU 부하는 낮지만, 너무 짧은
  `poll_interval_seconds`나 많은 monitor job은 FTP 서버와 slave Python 실행 부하를 올립니다.
  `poll_jitter_seconds`는 slave polling을 0-N초 사이로 흩어서 동시 접속 spike를 줄입니다.
- `max_result_files`, `max_log_files`, `max_archive_files`, `max_screenshot_files`,
  `max_output_chars`가 FTP folder와 log 크기 증가를 제한합니다.

보관 파일 정리:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info cleanup --node-id rig-pc-01
```

slave는 job 하나를 처리한 뒤에도 자동으로 cleanup을 수행합니다. `runtime`의 retention
필드가 result/log/archive/screenshot 개수를 제한하므로 FTP folder가 무한정 커지지 않습니다.
경로는 spool 내부 상대경로만 허용하고 local test backend는 `..` 경로를 거부하므로, cleanup은
정해진 spool folder만 대상으로 합니다.

FTP 서버 없이 실험하려면 `--local-root .\spool`을 붙이면 같은 folder layout을 local
directory에서 사용할 수 있습니다.

## 빌드 파이프라인

GitHub Actions는 Linux에서 test를 돌리고, `windows-latest`에서 PyInstaller로
아이콘이 적용된 `WinAutomationPicker.exe`, `RigCommander.exe`, `RigFtpCommander.exe`,
`RigFtpCli.exe`를 빌드합니다.
빌드 결과는 artifact로 업로드되고 `latest` GitHub Release asset이 갱신됩니다.

## 제한 사항과 운영 팁

- 대상 app이 관리자 권한이면 이 도구도 관리자 권한으로 실행해야 합니다.
- UIA handle은 실행할 때마다 바뀔 수 있으므로 selector는 stable metadata를 우선 사용합니다.
- 한글 입력은 기본적으로 clipboard paste 방식이 안정적입니다.
- 팝업 버튼을 실행하려면 실행 시점에도 팝업이 열려 있어야 합니다.
- 화면 전환이 느린 업무 프로그램은 `Add wait` 또는 `Row delay`를 적극적으로 넣는 편이 안정적입니다.
- Windows 전용 도구입니다.
