# Mobile DRAM AE 실장기 테스트

Mobile DRAM 테스트에 필요한 SEQ 작성, SK Commander 자동 조작, 실장기 직접 COM 제어, 여러 실장기 PC의 테스트 실행과 상태 확인을 한 프로그램에서 처리합니다.

## 프로그램 받기

| 파일 | 용도 |
|---|---|
| [AEWorkbench.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AEWorkbench.exe) | 권장 프로그램. 초기 설정, SEQ, 자동 실행 순서, 테스트 실행, 상태 확인을 통합한 GUI |
| [AutomationBuilder.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AutomationBuilder.exe) | 프로그램 클릭과 입력을 녹화하고 블록으로 편집하는 GUI |
| [FixtureCommunication.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/FixtureCommunication.exe) | 실장기 PC 통신과 상태 확인 전용 GUI |
| [FixtureControlCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/FixtureControlCli.exe) | 직접 COM·전원·Binary 작업용 고급 터미널 |
| [FixtureCommunicationCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/FixtureCommunicationCli.exe) | 통신 서버 작업용 고급 터미널 |
| [AEWorkbench-Windows-x64.zip](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AEWorkbench-Windows-x64.zip) | 모든 Windows 실행 파일과 검사값 목록 |

일반 작업자는 `AEWorkbench.exe`만 실행하면 됩니다.

## 현장 구성

```text
관리자 PC
  └─ 통신 서버
      └─ TFT30
          ├─ TFT30-1 ─ CH1, CH2, CH3, CH4
          ├─ TFT30-2 ─ CH5, CH6, CH7, CH8
          ├─ TFT30-3 ─ CH9, CH10, CH11, CH12
          └─ TFT30-4 ─ CH13, CH14, CH15, CH16
```

- **관리자 PC**: 테스트를 보내고 전체 상태를 확인하는 PC
- **실장기 PC**: 실장기가 최대 4대 연결된 Windows PC
- **실장기 번호**: 실제 실장기 한 대를 나타내는 `CH1` 같은 번호
- **통신 서버**: 관리자 PC와 실장기 PC가 요청·결과·화면을 주고받는 전용 폴더

## 기본 사용 순서

1. `3 초기 설정 > 설정 순서`의 1번부터 6번까지 따라 통신 서버와 TFT/UTF, 실장기 PC, 실장기를 등록합니다.
2. 각 실장기의 SoC 초기값, Binary, DRAM, Lot, 장착 자재 ID와 고장 상태를 입력합니다. Lot 또는 현재 Binary가 비어 있으면 `확인 필요`로 남습니다.
3. SK Commander의 실장기 번호·상태·SEQ·Load·Start 항목을 연결합니다.
4. `2 SEQ · 자동 실행 순서 준비`에서 SEQ 오류를 검사하고 자동 실행 순서를 녹화·편집합니다.
5. `1 테스트 진행`에서 사용할 실장기와 실장기별 입력값을 확인한 뒤 실행합니다.
6. `테스트 상태 보기`와 `실장기 상태`에서 PASS, 진행 중, FAIL, 없음, 중지를 확인합니다.

![테스트 실행 화면](docs/assets/screenshots/01-test-run.png)

## 실장기 기본 정보

각 실장기에는 다음 값이 함께 저장됩니다.

| 구분 | 값 예 |
|---|---|
| SoC | MTK24D, MTK25D, SM8850 |
| Binary | 이름, 버전, 원본 폴더, 수정 시각·수정자·수정 위치 |
| 장착 자재 | DRAM Part, Lot, AA-1, SS-2, AS1S1-1 |
| 테스트 | 현재 테스트, 사용 중인 SEQ, Grid 진행 수 |
| 상태 | 없음, 진행 중, PASS, FAIL, 중지 |
| 장치 | BL1, BL2, LK, OS, 고장 상태 |

Binary와 장착 자재 정보는 관리자 PC 또는 실장기 PC에서 수정할 수 있습니다. Binary는 `Binary 수정 시각`, 장착 자재·SoC·고장 상태는 `기본 정보 수정 시각`을 각각 비교하므로 서로 다른 PC에서 최신 값을 바꿔도 둘 다 유지됩니다. 현재 테스트와 BL1/BL2/LK/OS는 SK Commander 항목 연결 후 테스트 중에 확인됩니다.

## 자동 실행 순서

- `연속 녹화 시작`부터 `녹화 정지` 전까지 외부 프로그램의 클릭과 텍스트 입력을 기록합니다.
- 녹화 정지 버튼 자체는 기록하지 않습니다.
- 블록 이름 변경, 위아래 이동, 끌어놓기, 중첩·해제, 복제, 삭제, 실행 취소·다시 실행을 지원합니다.
- 반복, 텍스트 조건, 색상 조건, 여러 조건의 AND/OR 묶음을 지원합니다.
- 입력값을 고정값 또는 실장기별 입력값으로 지정할 수 있습니다.
- 검사한 순서를 실행 가능한 Python 파일로 내보낼 수 있습니다.

![자동 실행 순서 편집](docs/assets/screenshots/02-automation-flow.png)

## 상태 확인과 통신

- 실장기 PC는 설정된 간격마다 짧게 요청을 확인한 뒤 연결을 닫습니다.
- 전체 화면은 관리자가 요청할 때만 한 번 생성하며 자동으로 계속 전송하지 않습니다.
- 오래된 결과·로그·화면은 설정된 개수만 남겨 서버와 PC 용량 증가를 제한합니다.
- 진행 중인 테스트가 없으면 관리자 PC의 자동 모니터링이 종료됩니다.
- 관리자 PC에서는 실장기 PC별·실장기별 상태를 Excel로 내보낼 수 있습니다.
- `시작 폴더 만들기`를 누르면 실장기 PC별 설정과 실행 파일이 한 폴더로 준비되므로 별도의 압축 파일을 직접 만들 필요가 없습니다.

## 직접 COM과 Binary 업데이트

SK Commander를 사용하는 방식과 사용하지 않고 직접 COM으로 SEQ를 전송하는 방식을 모두 지원합니다. 같은 실장기의 COM을 두 프로그램에서 동시에 열지 않습니다.

Qualcomm QDL과 MediaTek Genio용 공개 명령 계약 검사가 포함되어 있지만 외부 다운로드 프로그램은 배포 파일에 포함하지 않습니다. 사내 승인 도구의 경로와 명령 형식을 등록하고 한 대에서 검증한 뒤 `실행 허용`을 켜야 합니다. Format 또는 섹터 쓰기는 사전 검사와 확인 문구를 통과해야만 실행됩니다.

## 설명서

- [초기 설정](docs/index.md)
- [테스트 운용](docs/operation/index.md)
- [문제 해결](docs/troubleshooting/index.md)

GitHub Pages에서는 같은 내용을 검색 가능한 GitBook형 화면으로 제공합니다.

## 개발과 검사

```powershell
python -m pip install -e .
python -m pytest -q
python -m win_automation_picker.ae_workbench
```

`main` 브랜치에 반영되면 GitHub Actions가 전체 테스트, 외부 도구 명령 계약 검사, Windows 실행 파일 빌드를 수행하고 `latest` 릴리스 파일을 갱신합니다. Pull Request와 수동 실행에서는 Windows 빌드 묶음을 Actions 아티팩트로 받을 수 있습니다.

실제 SK Commander 항목 인식, COM 연결, 물리 Download 스위치와 사내 전용 다운로드 프로그램은 해당 Windows PC와 실장기에서 최종 확인해야 합니다.
