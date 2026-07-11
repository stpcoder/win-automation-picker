# Win Automation Picker

A Windows UI Automation macro studio with a real nested block workspace, Python export, live text/color monitoring, and FTP-backed master/slave orchestration for restricted company networks.

- Korean manual: https://stpcoder.github.io/win-automation-picker/
- Korean README: [README.ko.md](README.ko.md)
- Latest release: https://github.com/stpcoder/win-automation-picker/releases/tag/latest

## Downloads

| File | Purpose |
| --- | --- |
| [WinAutomationPicker.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe) | Build, run, and export block macros |
| [RigFtpCommander.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe) | FTP master/slave GUI |
| [RigFtpCli.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe) | Advanced FTP CLI |
| [RigCommander.exe](https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe) | COM, PowerShell, and SSH rig-control CLI |

The executables are not code-signed, so Windows SmartScreen may show a warning.

## Highlights

- Captures the Windows UIA component under the next external click.
- Drags click, type, key, wait, repeat, if, and monitor blocks from a palette into the workspace.
- Moves blocks between top-level order and nested C-shaped repeat/condition containers.
- Selects, renames, duplicates, moves, and deletes nested children independently.
- Supports undo, redo, duplicate, and Delete keyboard editing.
- Focuses block naming and capture-quality feedback immediately after recording.
- Distinguishes multiple copies of one executable using text or regex window markers.
- Evaluates component text and sampled screen color as conditions or monitor states.
- Builds custom boards from arbitrary equipment/channel labels, states, axes, and display order.
- Runs monitor rules once or on an interval without executing click/type/key blocks.
- Replays pasted spreadsheet rows with `${name}`, `${col1}`, and `${row}` variables.
- Exports the complete nested workflow as runnable Python.
- Distributes jobs and collects status, results, and screenshots through an FTP spool.

## Macro quick start

1. Start `WinAutomationPicker.exe`.
2. Click `클릭 녹화` (Record click), then click a button in the target app.
3. Rename the new block in the right inspector and press Enter.
4. Enter a value in the top input field and use `입력 녹화` (Record input) for a text field.
5. Drag a repeat or if block from the left palette into the workspace.
6. Drag action blocks into the C-shaped container.
7. Reorder blocks by dragging them to the blue insertion line.
8. Run once or export the workflow as Python.

Recording is one-shot: each record command captures the next outside-app click and then stops. Recording an input target does not type immediately; typing occurs during execution.

See the [basic macro guide](https://stpcoder.github.io/win-automation-picker/macro-builder/basic-flow/) and [block workspace guide](https://stpcoder.github.io/win-automation-picker/macro-builder/block-designer/).

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
2. Enter the FTP connection and root directory under `Connection Setup`.
3. Initialize the dedicated folders.
4. Export one `.info` file per slave and place it next to the executable on that PC.
5. Start `This PC Agent` on each slave.
6. Upload a macro and submit it to selected slaves from the master.

Connections are opened only for transfers. Poll jitter, screenshot rate limits, and retention limits reduce server load. The tool stays under its configured FTP root and does not touch unrelated folders.

See the [FTP overview](https://stpcoder.github.io/win-automation-picker/rig-ftp/overview/).

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
RigCommander.exe list --config rig-config.json
RigCommander.exe run --config rig-config.json --target rig-pc-01 -- command args
RigCommander.exe status --config rig-config.json --target rig-pc-01
RigCommander.exe cancel --config rig-config.json --target rig-pc-01
```

With `--backend auto`, Windows/PowerShell hosts use PowerShell remoting and other hosts use SSH.

## CI and releases

Every push to `main` runs tests, builds all four Windows executables, and updates the `latest` release assets. Documentation changes deploy to `gh-pages`.

## Limitations

- The picker targets native Windows UI Automation.
- Games, canvas-rendered apps, browser DOM content, and custom-rendered controls may expose insufficient selector metadata.
- Run the picker at the same integrity level as an elevated target application.
- GUI automation and screenshots require an active interactive Windows desktop session.
