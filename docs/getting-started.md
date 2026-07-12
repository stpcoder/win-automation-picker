# 설치와 실행

## 실행 파일 다운로드

GitHub Releases의 `latest`에서 다음 파일을 받습니다.

| 파일 | 설명 |
| --- | --- |
| `AEWorkbench.exe` | 권장 통합 앱 |
| `WinAutomationPicker.exe` | 매크로 생성기 |
| `RigFtpCommander.exe` | FTP master/slave GUI |
| `RigFtpCli.exe` | FTP master/slave CLI |
| `RigCommander.exe` | 터미널 기반 rig 제어 CLI |

다운로드 주소:

```text
https://github.com/stpcoder/win-automation-picker/releases/latest/download/AEWorkbench.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe
```

!!! note "SmartScreen 경고"
    코드 서명된 exe가 아니므로 Windows SmartScreen 경고가 나올 수 있습니다. 사내 배포 정책에 맞게 허용 후 실행합니다.

## 권한

대상 프로그램이 관리자 권한으로 실행 중이면 `WinAutomationPicker.exe`도 관리자 권한으로 실행해야 클릭/입력 자동화가 제대로 됩니다.

## 권장 통합 실행

1. `AEWorkbench.exe`를 실행합니다.
2. 처음 구축할 때는 `3 Rig 설정`, 프로그램을 만들 때는 `2 자동화 준비`를 사용합니다.
3. 평소에는 첫 화면 `1 오늘 작업`에서 자동화와 PC/CH 값만 확인합니다.

실장기 COM을 직접 볼 때는 `2 자동화 준비 > 실장기 제어 · Binary`를 엽니다. Binary를
사용하는 Slave는 `rig-ftp.info`와 `rig-commander.config.json`이 EXE 옆에 모두 있어야 합니다.
4. 자세한 순서는 [Mobile DRAM AE 업무 흐름](daily-workflow.md)을 따릅니다.

SEQ를 Workbench에서 편집·검증·빌드하려면 Test Sequence Generator release의
`TestSeqGenerator.exe`와 `SeqTool.exe`가 서로 같은 폴더에 있어야 합니다. 그 폴더를
`AEWorkbench.exe` 옆의 `TestSeqGenerator/`로 두면 자동으로 발견합니다.

## Windows 11 장치 확인

최신 Release는 GitHub `windows-latest` runner에서 GUI/EXE 시작과 전체 테스트를 수행합니다.
실제 사내 PC에서는 다음 명령으로 Windows build, PowerShell과 serial backend를 먼저 확인합니다.

```powershell
RigCommander.exe device system-check
```

COM과 UI Automation은 로그인된 interactive Windows 11 desktop에서 한 CH dry-run으로 최종
확인합니다. 표준 CI runner에는 실제 COM 실장기와 SK Commander가 없습니다.

## 매크로 생성기만 실행

1. `WinAutomationPicker.exe`를 실행합니다.
2. 대상 업무 프로그램을 켭니다.
3. `대상 확인`을 눌러 대상 프로그램의 버튼이나 입력칸을 클릭해 봅니다.
4. `매크로 만들기` 위쪽 캡처 품질 배지가 정상인지 확인합니다.
5. `이벤트 > 클릭 녹화` 블록을 작업실로 끌어 놓고 실제 드래그 편집이 되는지 확인합니다.

## FTP 운영 프로그램 실행

1. master PC에서 `RigFtpCommander.exe`를 실행합니다.
2. `3 Rig 설정 > Master · 원격 PC > Master 연결`에 FTP 정보를 입력합니다.
3. `연결 확인` 후 설정 파일 영역의 `저장`을 눌러 `rig-ftp.info`를 저장합니다.
4. slave PC에는 `AEWorkbench.exe`, 해당 PC용 `rig-ftp.info`,
   `rig-commander.config.json`을 같은 폴더에 둡니다.

## 로컬 개발 환경에서 실행

개발 환경에서는 다음처럼 실행할 수 있습니다.

```powershell
python -m pip install -e .
python -m win_automation_picker
python -m win_automation_picker.ae_workbench
python -m win_automation_picker.rig_ftp_commander
```
