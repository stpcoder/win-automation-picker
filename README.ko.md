# Win Automation Picker

Windows UI Automation 기반 매크로 제작기와 FTP master/slave 운영 도구입니다. 사내 Windows 프로그램의 버튼, 입력칸, 상태 component를 캡처해 실제 Scratch식 블록으로 조합하고, 실행 가능한 Python 또는 원격 PC용 작업으로 배포합니다.

- 온라인 한글 매뉴얼: https://stpcoder.github.io/win-automation-picker/
- 영문 README: [README.md](README.md)
- 최신 Release: https://github.com/stpcoder/win-automation-picker/releases/tag/latest

## 다운로드

| 파일 | 용도 |
| --- | --- |
| [WinAutomationPicker.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe) | 블록 매크로 제작, 실행, Python export |
| [RigFtpCommander.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe) | FTP master/slave GUI 운영 |
| [RigFtpCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe) | FTP 고급 CLI |
| [RigCommander.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe) | COM/PowerShell/SSH rig 제어 CLI |

코드 서명이 없는 실행 파일이므로 Windows SmartScreen 경고가 표시될 수 있습니다.

## 주요 기능

- `연속 녹화 시작/정지` 사이 외부 프로그램의 클릭, 입력, 주요 키를 한 번에 기록합니다.
- 입력칸의 최종 UIA 값을 읽어 한글 IME와 붙여넣기를 하나의 입력 블록으로 묶습니다.
- 비밀번호 component의 값은 저장하지 않고 빈 필수 변수로 생성합니다.
- click/type/wait/key 블록을 팔레트에서 작업실로 끌어 놓습니다.
- 블록을 자유롭게 재배치하고 반복/if/AND/OR 컨테이너 안팎으로 이동합니다.
- 중첩된 자식 블록도 개별 선택, 이름 변경, 복제, 삭제할 수 있습니다.
- `Ctrl+Z`, `Ctrl+Y`, `Ctrl+D`, Delete 편집을 지원합니다.
- 캡처 직후 블록 이름 입력과 selector 품질 진단으로 이어집니다.
- 동일 exe 창 여러 개를 창 내부 텍스트/정규식으로 구분합니다.
- 텍스트와 색상을 조건 또는 모니터 상태로 읽습니다.
- 사용자 지정 보드, CH/장비명, 상태, 행/열 순서로 모니터 화면을 구성합니다.
- 모니터 규칙을 1회 또는 지정 주기로 평가하고 최근 값과 시간을 표시합니다.
- Excel/Google Sheets에서 복사한 행과 `${name}`, `${col1}`, `${row}` 변수를 사용합니다.
- 녹화한 입력을 `${변수}`로 전환하고 PC별 실행표에서 `PC01=Seq 1`, `PC02=Seq 2`처럼 지정합니다.
- 전체 workflow를 실행 가능한 Python으로 export합니다.
- export workflow는 slave EXE의 내장 엔진으로 실행되어 별도 Python 설치가 필요 없습니다.
- FTP spool을 통해 여러 slave PC에 작업을 배포하고 상태, 결과, screenshot을 회수합니다.

## 매크로 빠른 시작

1. `WinAutomationPicker.exe`를 실행합니다.
2. `입력값을 PC별 변수로`를 켜고 `연속 녹화 시작`을 누릅니다.
3. 대상 프로그램에서 평소처럼 입력칸에 값을 넣고 버튼을 클릭합니다.
4. Picker로 돌아와 `녹화 정지`를 누릅니다. 정지 버튼 클릭은 기록에서 제외됩니다.
5. 아래 `녹화 타임라인`에서 앱, component, 입력값과 변수화 여부를 확인합니다.
6. 왼쪽 팔레트에서 `N번 반복` 또는 `만약 ...` 블록을 끌어 놓고 녹화 블록을 안으로 이동합니다.
7. 상단 `실행`으로 기본값을 시험하거나 `Python 내보내기`로 저장합니다.
8. 원격 실행은 `배포 > PC별 실행표`에서 PC마다 macro와 입력값을 지정한 뒤 전송합니다.

연속 녹화는 사용자가 명시적으로 시작한 동안만 활성화되며 상단에 경과 시간과 동작 수가 계속 표시됩니다. 앱 내부 클릭은 제외되고 정지 시 전역 훅이 해제됩니다. 블록 하나만 만들 때는 기존 `클릭 녹화`, `입력 녹화`를 그대로 사용할 수 있습니다.

자세한 흐름은 [기본 매크로 만들기](https://stpcoder.github.io/win-automation-picker/macro-builder/basic-flow/)와 [Scratch식 블록 작업실](https://stpcoder.github.io/win-automation-picker/macro-builder/block-designer/)을 참고하십시오.

## 모니터링 빠른 시작

1. `감시 > 텍스트 상태` 또는 `색상 상태` 블록을 끌어 놓습니다.
2. 상태 component를 클릭합니다. 텍스트나 색상이 기대값으로 자동 샘플링됩니다.
3. 오른쪽에서 `탭`, `장비 / CH`, `표시 상태`를 입력합니다.
4. 복합 판정은 AND/OR 묶음 안으로 조건 블록을 끌어 넣습니다.
5. `모니터링` 탭에서 `한 번 확인` 또는 `자동 시작`을 누릅니다.
6. 규칙 표의 통과/실패, 최근 읽은 값, 최근 확인 시간을 확인합니다.
7. `보드 화면 구성`에서 행/열과 상태 순서를 원하는 대로 바꿉니다.

SK Commander처럼 같은 exe가 4개 떠 있는 경우에는 CH 식별 텍스트 조건과 상태 색상 조건을 AND로 묶을 수 있습니다. CH 이름은 `CH1` 형식으로 고정되지 않으며 `CH9`, `CH11`, `PC04-RIG2`처럼 자유롭게 지정합니다.

자세한 예시는 [조건과 모니터링](https://stpcoder.github.io/win-automation-picker/macro-builder/conditions-monitoring/)을 참고하십시오.

## FTP master/slave

사내 정책상 inbound port를 열 수 없는 환경을 위해 FTP를 공유 spool처럼 사용합니다.

1. master PC에서 `RigFtpCommander.exe`를 실행합니다.
2. `연결 설정`에 FTP 계정과 root directory를 입력하고 `연결 확인`을 누릅니다.
3. `서버 폴더 초기화`로 전용 폴더만 생성합니다.
4. slave별 `.info`를 내보내 각 PC의 exe 옆에 둡니다.
5. slave에서 `이 PC Agent > Agent 시작`을 실행합니다.
6. master의 `PC별 매크로 실행표`에서 각 slave의 macro와 변수 값을 정하고 제출합니다.

FTP 연결은 전송 시점에만 열고 닫으며, poll jitter, screenshot 최소 간격, 결과 보관 개수와 Agent 재연결 backoff로 부하를 제한합니다. 등록된 PC의 heartbeat가 끊기면 상태표에 offline으로 남고, 기존 FTP의 다른 폴더는 건드리지 않고 설정한 root 아래만 사용합니다.

- [FTP 구조](https://stpcoder.github.io/win-automation-picker/rig-ftp/overview/)
- [Master 세팅](https://stpcoder.github.io/win-automation-picker/rig-ftp/master-setup/)
- [Slave 세팅](https://stpcoder.github.io/win-automation-picker/rig-ftp/slave-setup/)
- [상태와 screenshot](https://stpcoder.github.io/win-automation-picker/rig-ftp/monitoring/)

## 소스 실행

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m win_automation_picker
```

테스트:

```powershell
python -m pip install pytest
python -m pytest -q
```

문서 미리보기:

```powershell
python -m pip install -r requirements-docs.txt
mkdocs serve
```

## Rig Commander CLI

설정 예제를 생성한 뒤 COM, PowerShell remoting, SSH 대상에 명령을 보낼 수 있습니다.

```powershell
RigCommander.exe init-config --output rig-config.json
RigCommander.exe list --config rig-config.json
RigCommander.exe run --config rig-config.json --target rig-pc-01 -- command args
RigCommander.exe status --config rig-config.json --target rig-pc-01
RigCommander.exe cancel --config rig-config.json --target rig-pc-01
```

`--backend auto`는 Windows/PowerShell 호스트에는 PowerShell remoting, 그 외 호스트에는 SSH를 사용합니다.

## 자동 빌드

`main` push 시 GitHub Actions가 테스트 후 네 Windows 실행 파일을 빌드하고 `latest` Release asset을 갱신합니다. 문서 변경은 `gh-pages`에 배포됩니다.

## 제한 사항

- native Windows UIA 대상에 맞춰져 있습니다.
- 게임, canvas 기반 앱, 브라우저 DOM, 자체 렌더링 UI는 selector 정보가 부족할 수 있습니다.
- 관리자 권한 프로그램을 자동화할 때 Picker도 같은 권한으로 실행해야 합니다.
- Windows interactive desktop session이 잠기거나 로그오프되면 GUI 자동화와 screenshot이 실패할 수 있습니다.
