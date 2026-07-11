# 설치와 실행

## 실행 파일 다운로드

GitHub Releases의 `latest`에서 다음 파일을 받습니다.

| 파일 | 설명 |
| --- | --- |
| `WinAutomationPicker.exe` | 매크로 생성기 |
| `RigFtpCommander.exe` | FTP master/slave GUI |
| `RigFtpCli.exe` | FTP master/slave CLI |
| `RigCommander.exe` | 터미널 기반 rig 제어 CLI |

다운로드 주소:

```text
https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe
https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe
```

!!! note "SmartScreen 경고"
    코드 서명된 exe가 아니므로 Windows SmartScreen 경고가 나올 수 있습니다. 사내 배포 정책에 맞게 허용 후 실행합니다.

## 권한

대상 프로그램이 관리자 권한으로 실행 중이면 `WinAutomationPicker.exe`도 관리자 권한으로 실행해야 클릭/입력 자동화가 제대로 됩니다.

## 기본 실행

1. `WinAutomationPicker.exe`를 실행합니다.
2. 대상 업무 프로그램을 켭니다.
3. `대상 확인`을 눌러 대상 프로그램의 버튼이나 입력칸을 클릭해 봅니다.
4. `매크로 만들기` 위쪽 캡처 품질 배지가 정상인지 확인합니다.
5. `이벤트 > 클릭 녹화` 블록을 작업실로 끌어 놓고 실제 드래그 편집이 되는지 확인합니다.

## FTP 운영 프로그램 실행

1. master PC에서 `RigFtpCommander.exe`를 실행합니다.
2. `Connection Setup` 탭에 FTP 서버 정보를 입력합니다.
3. `Save`를 눌러 `rig-ftp.info`를 저장합니다.
4. slave PC에는 `RigFtpCommander.exe`와 해당 PC용 `rig-ftp.info`를 같은 폴더에 둡니다.

## 로컬 개발 환경에서 실행

개발 환경에서는 다음처럼 실행할 수 있습니다.

```powershell
python -m pip install -e .
python -m win_automation_picker
python -m win_automation_picker.rig_ftp_commander
```
