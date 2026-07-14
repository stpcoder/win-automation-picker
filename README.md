# Mobile DRAM AE Fixture Testing

This Windows application combines SEQ preparation, recorded SK Commander automation, direct serial control, fixture-PC communication, and centralized test monitoring.

## Downloads

| File | Purpose |
|---|---|
| [AEWorkbench.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AEWorkbench.exe) | Recommended GUI for setup, SEQ preparation, automation, execution, and monitoring |
| [AutomationBuilder.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AutomationBuilder.exe) | GUI recorder and nested block editor |
| [FixtureCommunication.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/FixtureCommunication.exe) | Standalone fixture-PC communication GUI |
| [FixtureControlCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/FixtureControlCli.exe) | Advanced serial, power, and Binary terminal |
| [FixtureCommunicationCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/FixtureCommunicationCli.exe) | Advanced communication-server terminal |
| [AEWorkbench-Windows-x64.zip](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AEWorkbench-Windows-x64.zip) | All Windows executables and their checksum manifest |

Most operators only need `AEWorkbench.exe`.

## Site Model

```text
Administrator PC
  └─ Communication server
      └─ TFT30
          ├─ TFT30-1 ─ CH1, CH2, CH3, CH4
          ├─ TFT30-2 ─ CH5, CH6, CH7, CH8
          ├─ TFT30-3 ─ CH9, CH10, CH11, CH12
          └─ TFT30-4 ─ CH13, CH14, CH15, CH16
```

A fixture PC hosts up to four physical fixtures. Every fixture stores its SoC, Binary, DRAM part, Lot, material ID, active test, SEQ, BL1/BL2/LK/OS stage, fault status, and edit history.

## Operator Flow

1. Follow steps 1 through 6 under `3 초기 설정 > 설정 순서` to register the communication server, TFT/UTF group, fixture PCs, and fixtures.
2. Map the fixture number and status components in each SK Commander window.
3. Validate the SEQ and record or edit the automatic execution flow under `2 SEQ · 자동 실행 순서 준비`.
4. Select fixtures and per-fixture values under `1 테스트 진행`, then start the test.
5. Monitor `PASS`, `진행 중`, `FAIL`, `없음`, and `중지` from the administrator PC.

![Test execution](docs/assets/screenshots/01-test-run.png)

## Automation Editor

- Records external application clicks and text input between start and stop.
- Excludes the recorder's own stop action.
- Supports rename, reorder, drag and drop, nesting, duplication, deletion, undo, and redo.
- Supports repeat blocks, text conditions, color conditions, and nested AND/OR groups.
- Converts recorded input into fixed values or values supplied separately for each fixture.
- Exports a validated flow as executable Python.

![Automatic execution flow](docs/assets/screenshots/02-automation-flow.png)

## Communication and Monitoring

- Fixture PCs poll briefly and disconnect instead of holding a permanent server connection.
- Full-screen capture is generated only when requested.
- Retention limits bound result, log, screenshot, and local work-file growth.
- Automatic monitoring stops when no test is running.
- Fixture and fixture-PC state can be exported to Excel.
- Binary edits use their own Binary timestamp; material, SoC, and fault edits use the general information timestamp, so newer values from opposite PCs are preserved independently.
- `시작 폴더 만들기` prepares one self-contained startup folder per fixture PC; operators do not need to assemble a separate archive manually.

## Direct Serial and Binary Updates

The application supports both SK Commander automation and direct serial SEQ execution. A physical fixture's serial port must not be opened by both routes at the same time.

Public Qualcomm QDL and MediaTek Genio command contracts are tested in CI, but external downloader executables are not bundled. Configure an approved local tool, validate it on one fixture, and only then enable execution. Format and bounded sector-write operations require explicit preflight checks and confirmation tokens.

## Manuals

- [Initial setup](docs/index.md)
- [Test operation](docs/operation/index.md)
- [Troubleshooting](docs/troubleshooting/index.md)

The GitHub Pages build presents the same material as a searchable GitBook-style manual. The operator text and screenshots are Korean because that is the target workplace language.

## Development

```powershell
python -m pip install -e .
python -m pytest -q
python -m win_automation_picker.ae_workbench
```

Updates to `main` run the full test suite, external command-contract checks, Windows packaging, and the `latest` release update. Pull requests and manually dispatched runs expose the Windows bundle as an Actions artifact.

Actual SK Commander selectors, serial hardware, physical download switches, and proprietary workplace downloader commands must be verified on the target Windows fixture PC.
