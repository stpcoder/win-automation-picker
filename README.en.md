# Mobile DRAM AE Fixture Testing

This Windows application combines SEQ preparation, recorded SK Commander automation, direct serial control, fixture-PC communication, and centralized test monitoring.

- Korean manual: [README.md](README.md)
- Searchable manual: https://stpcoder.github.io/win-automation-picker/

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

1. Register the communication server, TFT/UTF group, fixture PCs, and fixtures under `3 초기 설정`.
2. Map the fixture number and status fields in each SK Commander window.
3. Validate the SEQ and record or edit the automatic execution flow.
4. Select fixtures and per-fixture values, then start the test.
5. Monitor `PASS`, `진행 중`, `FAIL`, `없음`, and `중지` from the administrator PC.

The application supports both SK Commander automation and direct serial SEQ execution. A physical fixture's serial port must not be opened by both routes at the same time.

## Development

```powershell
python -m pip install -e .
python -m pytest -q
python -m win_automation_picker.ae_workbench
```

Actual SK Commander selectors, serial hardware, physical download switches, and proprietary workplace downloader commands must be verified on the target Windows fixture PC.
