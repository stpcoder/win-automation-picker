# Win Automation Picker

Win Automation Picker is a Windows UI Automation recorder and runner. It can
inspect the UI element under a real click, save that element as a reusable
selector, record click/type/wait steps, and replay the recorded workflow once or
for every pasted spreadsheet row.

## Download

Download the latest Windows executable:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe

Download the terminal rig-control CLI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe

Windows may show a SmartScreen warning because the executable is not code-signed.

## What it does

- Finds Windows UI Automation elements under the next mouse click.
- Stores selectors using `AutomationId`, `Name`, `ControlType`, `ClassName`,
  sibling index, window metadata, and an XPath-like path.
- Runs one-off `Click` and `Type` actions against the current selector.
- Records workflows with `Record click step`, `Record type step`, and `Add wait`.
- Shows recording state, last captured window/control, click point, step count,
  and run progress in the `Monitor` tab.
- Lets you name captured controls with agent-friendly `element_id`, role, and
  notes.
- Adds an optional `Window marker` comparator so repeated windows can be
  distinguished by a component inside the window, such as `CH 1` or `CH 2`.
- Shows root-window candidate comparisons in the `Window Debug` tab.
- Saves and loads selector JSON and workflow JSON.
- Exports the recorded workflow as a runnable Python script.
- Accepts Excel/Google Sheets-style pasted rows in the `Data rows` tab.
- Replays the same workflow for every pasted row with template variables such as
  `${col1}`, `${col2}`, `${name}`, `${message}`, and `${row}`.
- Adds keyboard steps such as `{ENTER}`, `{TAB}`, or `^s`.

This is aimed at native Windows UI. Apps with custom canvas rendering, games,
or browser DOM content may expose poor UIA metadata and may need a specialized
adapter.

## Basic workflow

1. Open `WinAutomationPicker.exe`.
2. Put an agent-friendly name in `Element name`, for example `search_button`.
3. Choose a `Role`, for example `button` or `input`. Leave it as `auto` to infer
   from the Windows UI Automation control type.
4. If several similar windows are open, put a distinguishing label in
   `Window marker`, for example `CH 1`.
5. Click `Record click step`, then click a target button in another Windows app.
6. Put a text template in `Text / template`, for example `${message}`.
7. Set `Element name` to a field name such as `message_input`.
8. Click `Record type step`, then click the target text input in the other app.
9. Use `Add Enter` or `Add key` for keyboard-only blocks.
10. Paste spreadsheet data into `Data rows`.
11. Click `Run rows`.
12. Click `Export Python` if you want a `.py` script version of the macro.

Example pasted data:

```tsv
name	message
Alice	Hello Alice
Bob	Hello Bob
```

If `First row headers` is enabled, `${name}` and `${message}` are available. The
same row can also be addressed by `${col1}` and `${col2}`. If there is no header
row, disable `First row headers` and use `${col1}`, `${col2}`, and so on.

## Recording model

The recorder is a step recorder, not a continuous background macro recorder.
Each record button arms the next outside-app click only:

- `Record click step` waits for one click in the target app, stores that UI
  element as a click step, then stops listening.
- `Record type step` waits for one click in the target app, stores that UI
  element as a type step, then stops listening. The text comes from
  `Text / template`.
- `Pick inspect` waits for one click and only updates the selector preview. It
  does not add a workflow step.
- `Add wait` adds a delay step without picking a UI element.

To record multiple clicks, press `Record click step` again for each target
click. To record click, type, click, type, repeat the buttons in that order.
Clicks inside Win Automation Picker are ignored while capture is armed, so using
`Cancel capture` or switching tabs will not add a workflow step.

The `Element name`, `Role`, and `Notes` fields are stored with the recorded step.
Select a step in the `Steps` tab and click `Apply to step` if you want to rename
or reclassify it after recording. Selecting a step also loads its selector, so
you can add a `Window marker` and use `Debug windows` against that exact step.

## Monitor tab

Open the `Monitor` tab on the right side while recording or running a workflow.
It shows:

- `State`: `Idle`, `Armed`, `Running`, or `Error`.
- `Mode`: the current capture or run mode.
- `Steps`: the number of recorded workflow steps.
- `Window`: the root window used to identify the target app.
- `Target`: the captured button, input, menu item, or other UI Automation
  control.
- `Point`: the screen coordinate of the last captured click.
- Event history: armed captures, recorded steps, run progress, stop requests,
  and errors.

If a click is recorded, the `Steps` tab gains a new numbered step and the
`Workflow JSON` tab is updated immediately. If those do not change, the click
was not recorded.

The `Elements` tab lists the reusable agent targets discovered from the workflow:
`element_id`, role, first step number, and captured target metadata. This is the
view to check when you are building a small tool surface for an AI agent.

## Window Debug tab

Use `Debug windows` when the same program is open more than once, or when one
management PC controls several similar devices. The debug output lists visible
top-level windows and shows:

- `ROOT`: whether the window title/class matches the recorded selector root.
- `MARK`: whether the optional `Window marker` was found inside that window.
- `HINT`: whether the saved root handle points to that candidate.
- `SEL`: which candidate would be used by the runner.
- `MARKER TARGET`: the component that satisfied the marker, if any.

For example, if four test-device windows all have the same title, type `CH 1`
in `Window marker`, click `Apply marker`, then click `Debug windows`. Only the
candidate window containing a UIA component whose `Name` includes `CH 1` should
show `MARK=Y` and `SEL=*`. Repeat with `CH 2`, `CH 3`, and `CH 4` for separate
agent elements such as `ch_1_start_button` and `ch_2_start_button`.

## How window targeting works

Every captured selector has two parts:

- `root`: the top-level target window. This stores metadata such as window
  `Name`, `ClassName`, root handle, and process id when available.
- `path`: the child controls inside that window, down to the clicked target.
- `window_marker`: optional comparator data. The runner searches inside each
  candidate root window for a component matching this marker before it walks the
  saved child-control path.

When a workflow runs, the app first finds the root window and then walks the
saved child-control path. That means the same button name in a different window
should not match unless the root window also matches. The `Monitor` tab shows
the last captured `Window` and `Target` so you can verify that the right app and
control were recorded.

The simple UI field sets `window_marker.name_contains`, which checks UIA
component `Name` text case-insensitively. For advanced cases you can edit the
selector JSON directly:

```json
"window_marker": {
  "name_contains": "CH 1",
  "automation_id": "channelLabel",
  "control_type": "Text",
  "class_name": ""
}
```

All non-empty marker fields must match the same component. The saved
`root_handle` remains only a fast hint; if it points to the wrong instance or
the marker is missing, the runner scans other matching root windows.

## Python export

`Export Python` writes the current workflow as a runnable `.py` file. The export
includes:

- The recorded click/type/wait steps.
- Agent-readable `ELEMENTS` metadata for named controls.
- Window-marker comparator metadata for each named control.
- The current `Data rows` text, if any.
- The `First row headers` setting.
- The `Row delay` setting.
- Helper functions: `list_elements()`, `click_element(id)`, `type_into(id, text)`,
  and `press_key(keys)`.

Run the exported script on Windows in an environment where this package is
installed:

```powershell
python -m pip install -e .
python .\exported_workflow.py
```

If `Data rows` is empty, the script runs the workflow once. If rows are present,
it runs once per row and applies the same `${col1}`, `${name}`, `${row}`, and
other template variables used by `Run rows`.

For agent use, the exported script exposes a small API:

```python
print(list_elements())
click_element("search_button")
type_into("message_input", "hello", clear=True)
press_key("{ENTER}")
```

That lets a higher-level agent call stable named blocks instead of relying on
screen coordinates.

## Selector-only workflow

1. Click `Pick inspect`.
2. Click a target component in another Windows app.
3. Review the generated selector JSON and XPath-like path.
4. Use `Click`, `Type`, `Copy selector`, or `Save selector`.

## Generated selector shape

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
  "window_marker": {
    "name_contains": "CH 1",
    "automation_id": "",
    "control_type": "Text",
    "class_name": "",
    "description": ""
  }
}
```

## Install from source on Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
win-automation-picker
rig-commander --help
```

## Rig Commander CLI

`RigCommander.exe` is the second program in this repository. It is for
terminal-based control of many Windows rig PCs that have Android test fixtures
or power-control devices attached through COM ports. Instead of opening a
remote desktop session and using a GUI such as SKCOMMANDER, it sends configured
serial commands through PowerShell.

If you double-click `RigCommander.exe`, it opens an interactive `rig>` shell
instead of closing immediately. Type `help` to list commands, `help firmware
flash` to inspect a subcommand, and `exit` to close the shell. From an existing
PowerShell window, you can also pass commands directly as shown below.

The architecture is:

- Your work PC runs `RigCommander.exe` from PowerShell.
- Each rig PC is listed in `rig-commander.config.json`.
- Remote rig PCs are reached through PowerShell Remoting / WinRM.
- COM port commands are executed on the rig PC with `.NET SerialPort`.
- Known device commands such as `power_on`, `power_off`, `reset`, and `status`
  are stored per channel in the config.

Create a starter config:

```powershell
.\RigCommander.exe init-config -o rig-commander.config.json
```

Edit the host names, COM ports, baud rates, and command strings:

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

Common commands:

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

Target selectors:

- `all`: every enabled host and channel.
- `rig-pc-01`: every configured channel on one host.
- `rig-pc-01:ch1`: one channel.
- `tag:line-a`: every enabled host with that tag.

Before using remote hosts, WinRM must be enabled and allowed by the company
network policy on each rig PC. A typical administrator setup on a trusted
internal network is:

```powershell
Enable-PSRemoting -Force
```

If the target device protocol is proprietary, this tool still needs the actual
serial command strings that SKCOMMANDER sends. Put those strings in the
`commands` section once they are known. If SKCOMMANDER only works through a DLL
or non-serial API, add a dedicated adapter instead of using the serial command
runner.

### Firmware download

`RigCommander.exe` can also orchestrate firmware download jobs through a
configured vendor downloader executable. This covers workflows where a GUI tool
loads an XML manifest, lists image files, then runs either `Download Only` or
`Format All + Download`.

The CLI does not guess the vendor protocol. Configure the downloader executable
and its arguments per rig PC:

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

Firmware commands:

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

The `--xml` path is passed to the rig PC. For remote flashing, use a path that
the remote PC can see, such as a local path on that PC or a shared UNC path.

If firmware can only be uploaded after a boot-stage marker such as memory
training, poll a configured serial status command before starting the downloader:

```powershell
.\RigCommander.exe -c .\rig-commander.config.json firmware flash `
  --target rig-pc-01:ch1 `
  --xml \\fileserver\firmware\build123\firmware.xml `
  --mode download-only `
  --ready-command status `
  --ready-marker "MEMORY TRAINING PASS" `
  --ready-timeout 180
```

Use `--dry-run` first to inspect the generated remote PowerShell without running
the downloader.

## Build pipeline

GitHub Actions runs tests on Linux, builds `WinAutomationPicker.exe` and
`RigCommander.exe` on `windows-latest` with PyInstaller, uploads the executables
as artifacts, and updates the `latest` GitHub Release assets used by the
download links above.

## Notes

- If the target app runs as Administrator, run this picker as Administrator too.
- UIA handles can change between launches, so selectors prefer stable metadata
  and keep handles as optional hints.
- Text input defaults to clipboard paste for reliability with arbitrary text.
- The executable is Windows-only.
