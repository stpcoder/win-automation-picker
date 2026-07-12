# Win Automation Picker

A Windows UI Automation macro studio with a real nested block workspace, Python export, live text/color monitoring, and FTP-backed master/slave orchestration for restricted company networks.

- Korean manual: https://stpcoder.github.io/win-automation-picker/
- Korean README: [README.ko.md](README.ko.md)
- Latest release: https://github.com/stpcoder/win-automation-picker/releases/tag/latest

## Downloads

| File | Purpose |
| --- | --- |
| [AEWorkbench.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/AEWorkbench.exe) | Integrated SEQ, Scratch macro, validation, FTP deployment, and monitoring workspace |
| [WinAutomationPicker.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe) | Build, run, and export block macros |
| [RigFtpCommander.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe) | FTP master/slave GUI |
| [RigFtpCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe) | Advanced FTP CLI |
| [RigCommander.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe) | COM, PowerShell, and SSH rig-control CLI |

The executables are not code-signed, so Windows SmartScreen may show a warning.

## Highlights

![Daily workspace centered on automation selection and the PC/CH run table](docs/assets/screenshots/01-today-work.png)

- Separates daily execution, one-time automation preparation, and Rig setup into three task-frequency tabs.
- Keeps raw package upload and single-node arguments behind progressive disclosure in the daily view.
- Keeps the active SEQ recipe/package, Scratch source/Python export, and named macro buttons in one `*.aework.json` project.
- Opens Test Sequence Generator on the active recipe and calls `SeqTool.exe` in the background for validation and Rig-package builds.
- Treats temperature as a user-selected SEQ target rendered into `@TF set/run`, not as automatic measured-temperature collection.
- Opens the real Scratch editor from Rig, runs/stops a local test, and uploads the validated SEQ and current macro together.
- Edits detected local-test variables in labeled fields and stores them per Workbench project without requiring JSON syntax.
- Renames, annotates, and reorders named macro buttons without recreating the source macro.
- Continuously records external-app clicks, grouped text input, and common keys between explicit Start/Stop actions.
- Reads the final UIA field value so IME composition and paste become one input block; password values are never stored.
- Drags click, type, key, wait, repeat, if, and monitor blocks from a palette into the workspace.
- Defaults to a compact Scratch layout with 46 px blocks and a 2 px connected-stack gap.
- Moves blocks between top-level order and nested C-shaped repeat/condition containers.
- Selects, renames, duplicates, moves, and deletes nested children independently.
- Supports undo, redo, duplicate, and Delete keyboard editing.
- Focuses block naming and capture-quality feedback immediately after recording.
- Distinguishes multiple copies of one executable using text or regex window markers.
- Evaluates component text and sampled screen color as conditions or monitor states.
- Builds custom boards from arbitrary equipment/channel labels, states, axes, and display order.
- Runs monitor rules once or on an interval without executing click/type/key blocks.
- Replays pasted spreadsheet rows with `${name}`, `${col1}`, and `${row}` variables.
- Converts recorded values into runtime variables and submits a different macro/value matrix for each PC.
- Exports the complete nested workflow as runnable Python.
- Runs exported workflows inside the slave executable without requiring a separate Python installation.
- Distributes jobs and collects status, results, and screenshots through an FTP spool.
- Separates controller, FTP, fixture-PC, and physical-fixture identity and audits duplicate Node, asset, Windows, COM, HWID, and ADB bindings.
- Bulk-merges and exports Excel-friendly PC/fixture inventory CSV with free-form channel names.
- Verifies Test Sequence Generator `.rigseq.zip` artifacts and assigns each PC/slot/CH to direct COM or an SK Commander launcher.
- Tracks free-form per-PC channels with SoC, binary source/version/time, DRAM material, current test/SEQ, and Grid progress in FTP heartbeats and two-sheet Excel exports.
- Verifies checksummed AE campaign snapshots, expands PC/CH/repeat run rows, and shows acceptance/failure state in a campaign board.
- Stores operator failure classification and disposition in separate triage sidecars without rewriting raw results.
- Imports Seq Generator `.rigbinary.json` metadata without copying proprietary binary payloads.
- Keeps up to four COM consoles open for live output, boot-state markers, ASCII/control-key input, and parallel `.seq` runs.
- Revalidates the configured hardware identity before opening a COM and only suggests uniquely identified COM moves.
- Batches direct-COM run-table rows by slave/campaign/attempt and runs up to four distinct ports concurrently.
- Pins MTK/QC updates to one CH with XML hashes, USB identity, fixed ADB serials, vendor gates, and allowlisted external downloader rules.
- Exports `rig-ftp.info` and `rig-commander.config.json` together for each slave PC.
- Keeps configured but stale PCs visible as offline and matches screenshots to their exact request job.

## Macro quick start

For the complete flow, use the [AE Workbench guide](https://stpcoder.github.io/win-automation-picker/ae-workbench/). For macro-only work:

1. Open `AEWorkbench.exe > 2 자동화 준비 > Scratch 더보기 > 새 매크로 만들기`, or start `WinAutomationPicker.exe` directly.
2. Keep `입력값을 PC별 변수로` enabled and click `연속 녹화 시작`.
3. Use the target application normally: click fields, type values, and click buttons.
4. Return to the Picker and click `녹화 정지`; the stop click itself is excluded.
5. Inspect the app, component, captured value, and variable mode in the recording timeline.
6. Drag repeat or if blocks around the recorded blocks as needed.
7. Run with the captured defaults or export the workflow as Python.
8. Use the Deploy run matrix to assign a different macro and input value to each PC.

Continuous recording is active only after an explicit start and always shows elapsed time and action count. One-shot click/input capture remains available for adding a single block.

See the [basic macro guide](https://stpcoder.github.io/win-automation-picker/macro-builder/basic-flow/) and [block workspace guide](https://stpcoder.github.io/win-automation-picker/macro-builder/block-designer/).

## Direct fixture control and binary updates

![Four persistent serial consoles](docs/assets/screenshots/12-four-channel-console.png)

Open `2 자동화 준비 > 실장기 제어 · Binary` on the PC that owns the COM ports. The console
supports printable ASCII, explicit Enter/Ctrl+C/Ctrl+V, per-character delay, keepalive Enter, and
parallel execution of one `.seq` on up to four selected channels.

Binary jobs import checksummed Seq Generator metadata and execute one channel at a time. The slave
rechecks the SoC vendor, downloader allowlist, XML hash, COM port, USB download identity, Qualcomm
physical switch or MediaTek preloader state, and optional post-download ADB serial.

See the [Korean device-control guide](https://stpcoder.github.io/win-automation-picker/device-control/).

## Monitoring quick start

1. Drag a text-state or color-state monitor block into the workspace.
2. Click the target status component; its current value is sampled as the expectation.
3. Set the board, equipment/channel label, and displayed state in the inspector.
4. Nest rules inside AND/OR monitor groups for compound decisions.
5. Open the monitoring tab and run one check or start interval monitoring.
6. Inspect pass/fail, the latest actual value, and the latest refresh time.
7. Customize board rows, columns, state order, and channel order.

Equipment labels are free-form. `CH9`, `CH11`, and `PC04-RIG2` work without a fixed CH schema.

## FTP master/slave

The FTP tools use a configured root directory as a shared spool when inbound ports cannot be opened.

1. Start `RigFtpCommander.exe` on the master PC.
2. Audit Master/FTP/fixture-PC/fixture identity under `3 Rig 설정 > 연결 구조`, then verify FTP under `Master · FTP`.
3. Open `실장기 연결 PC` and run `서버 폴더 준비` for the dedicated folders.
4. Export one `.info` file per slave and place it next to the executable on that PC.
5. Start `이 PC Agent` on each slave.
6. Open `1 오늘 작업 > 실행`, load the Rig targets, assign each macro or SEQ, and click `실행 시작`.

Connections are opened only for transfers. Poll jitter, screenshot rate limits, retention limits, stale-heartbeat classification, and agent reconnect backoff reduce server load and false status. The tool stays under its configured FTP root and does not touch unrelated folders.

See the [FTP overview](https://stpcoder.github.io/win-automation-picker/rig-ftp/overview/) and [SEQ Generator / fixture execution workflow](https://stpcoder.github.io/win-automation-picker/rig-ftp/seq-integration/).

## Install from source

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m win_automation_picker
```

Run tests:

```powershell
python -m pip install pytest
python -m pytest -q
```

Preview documentation:

```powershell
python -m pip install -r requirements-docs.txt
mkdocs serve
```

## Rig Commander CLI

```powershell
RigCommander.exe init-config --output rig-config.json
RigCommander.exe -c rig-config.json list
RigCommander.exe -c rig-config.json run --target rig-pc-01:ch1 status
RigCommander.exe -c rig-config.json device probe --target rig-pc-01:ch1
RigCommander.exe device system-check
```

## CI and releases

Every push to `main` runs tests, builds all five Windows executables, and updates the `latest` release assets. Documentation changes deploy to `gh-pages`.

## Limitations

- The picker targets native Windows UI Automation.
- Games, canvas-rendered apps, browser DOM content, and custom-rendered controls may expose insufficient selector metadata.
- Run the picker at the same integrity level as an elevated target application.
- GUI automation and screenshots require an active interactive Windows desktop session.
