# Win Automation Picker

Korean manual: [README.ko.md](README.ko.md)

GitBook-style online manual:

https://stpcoder.github.io/win-automation-picker/

Win Automation Picker is a Windows UI Automation recorder and runner. It can
inspect the UI element under a real click, save that element as a reusable
selector, record click/type/wait steps, and replay the recorded workflow once or
for every pasted spreadsheet row.

## Download

Download the latest Windows executable:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe

Download the terminal rig-control CLI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigCommander.exe

Download the FTP master/slave orchestration GUI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCommander.exe

Download the advanced FTP master/slave CLI:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/RigFtpCli.exe

Windows may show a SmartScreen warning because the executable is not code-signed.

## What it does

- Finds Windows UI Automation elements under the next mouse click.
- Stores selectors using `AutomationId`, `Name`, `ControlType`, `ClassName`,
  sibling index, window metadata, and an XPath-like path.
- Runs one-off `Click` and `Type` actions against the current selector.
- Records workflows with `Click block`, `Type block`, `Wait`, and `Custom key`.
- Shows recording state, last captured window/control, click point, step count,
  and run progress in the `Run Log` tab.
- Lets you name captured controls with agent-friendly `element_id`, role, and
  notes.
- Adds an optional `Window identity` comparator so repeated windows can be
  distinguished by a component inside the window, such as `CH 1` or `CH 2`.
- Shows root-window candidate comparisons in the `Windows` tab.
- Resolves buttons inside popup/dialog windows, including nested UIA `Window`
  controls that are not returned as normal top-level windows.
- Saves and loads selector JSON and workflow JSON.
- Exports the recorded workflow as a runnable Python script.
- Shows a Scratch-style `Build` tab with a palette, workspace, and selected
  block inspector so each recorded action feels like a visual block.
- Lets you rename blocks, choose custom block colors, color the view by event
  type or target window, and wrap blocks in repeat or `if target exists`
  containers.
- Shows `Capture Quality` feedback after each pick so weak or false captures are
  visible before you build the rest of the macro.
- Adds a `Deploy` tab for uploading the current block workflow as a titled,
  annotated macro package and refreshing the server-side macro list.
- Lets you move recorded steps up/down and delete incorrect steps.
- Accepts Excel/Google Sheets-style pasted rows in the `Data Rows` tab.
- Replays the same workflow for every pasted row with template variables such as
  `${col1}`, `${col2}`, `${name}`, `${message}`, and `${row}`.
- Adds keyboard steps such as `{ENTER}`, `{TAB}`, or `^s`.
- Adds `RigFtpCommander.exe` for FTP-backed master/slave job distribution when
  company network policy does not allow opening custom inbound ports.

This is aimed at native Windows UI. Apps with custom canvas rendering, games,
or browser DOM content may expose poor UIA metadata and may need a specialized
adapter.

## Basic workflow

1. Open `WinAutomationPicker.exe`.
2. Put an agent-friendly name in `Target Setup` > `Name`, for example `search_button`.
3. Choose a `Type`, for example `button` or `input`. Leave it as `auto` to infer
   from the Windows UI Automation control type.
4. If several similar windows are open, put a distinguishing label in
   `Window match`, for example `CH 1`.
5. Click `Click block`, then click a target button in another Windows app.
6. Put a text template in the `Input` area's `Text` field, for example `${message}`.
   Leave `Clear` enabled to replace existing field contents. Use `Method=keys`
   only when the target app blocks clipboard paste.
7. Set `Name` to a field name such as `message_input`.
8. Click `Type block`, then click the target text input in the other app.
9. Use `Build > Action` or `More > Actions` for Enter and custom-key blocks.
10. Open the `Build` tab to rename blocks, change colors, wrap a block in a
    repeat loop, or wrap it in `If selected exists`.
11. In the `Sequence` tab, use `Move up`, `Move down`, or `Delete step` to fix the
    workflow order.
12. Paste spreadsheet data into `Data Rows`.
13. Click `Run rows`.
14. Click `Export Python` if you want a `.py` script version of the macro.

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

- `Click block` waits for one click in the target app, stores that UI
  element as a click step, then stops listening.
- `Type block` waits for one click in the target app, stores that UI
  element as a type step, then stops listening. The text comes from
  `Input` field.
- `Inspect` waits for one click and only updates the selector preview. It
  does not add a workflow step.
- `Wait` adds a delay step without picking a UI element.

To record multiple clicks, press `Click block` again for each target
click. To record click, type, click, type, repeat the buttons in that order.
Clicks inside Win Automation Picker are ignored while capture is armed, so using
`Cancel capture` or switching tabs will not add a workflow step.

Type recording does not type during capture. `Type block` stores the
input control you click, and the text is read from the `Input` field when the
workflow runs. `Clear` is enabled by default, so type steps replace existing
field contents unless you uncheck it. Text input defaults to clipboard paste
because it is more reliable for Korean and other arbitrary Unicode text than
key-by-key input. If the target app blocks paste, change `Method` to `keys`.

When recording click or type steps, the app trims overly specific UIA paths. For
example, if Windows UI Automation reports a `Text` child inside a `Button`, the
recorded click step targets the `Button`; if it reports a `Text` child inside an
`Edit`, the recorded type step targets the `Edit`. This makes replay less
fragile than clicking or typing into a decorative label.

The `Name`, `Type`, and `Note` fields are stored with the recorded step.
Select a step in the `Sequence` tab and click `Apply` if you want to rename
or reclassify it after recording. Selecting a step also loads its selector, so
you can add a `Window match` and use `Test windows` against that exact step.

The `Sequence` tab also supports reordering and cleanup. Select a step and use
`Move up`, `Move down`, or `Delete step`. Keyboard shortcuts are `Alt+Up`,
`Alt+Down`, and `Delete`.

## Build tab

The `Build` tab is a visual sequence editor. It is laid out like a small block
coding workspace:

- `Add Blocks`: add common blocks from one place: click/type capture, wait,
  Enter, custom key, repeat, condition, and monitor groups.
- `Macro Workspace`: every recorded click/type/wait/key step appears as a numbered
  colored block. The selected block gets a yellow outline.
- `Selected Block`: rename the selected block, choose a fixed color, switch
  `Color by` between event colors and window colors, and set repeat count.
- `Repeat selected`: wraps the selected block in a repeat container.
- `If selected exists`: wraps the selected block in a condition container. The
  child block runs only if that target can be resolved at runtime.
- `If selected text`: wraps the selected block in a text condition. For example,
  run the child only when a status field contains `PASS`.
- `If selected color`: wraps the selected block in a screen color condition.
  The sampled point follows the originally clicked relative position inside the
  current element rectangle and is compared against `#RRGGBB` with tolerance.
- `Monitor text` and `Monitor color`: record OK/FAIL status without changing the
  execution flow.
- `Group AND` and `Group OR`: select multiple condition or monitor blocks in the
  `Sequence` tab and combine them into one monitor group block. AND requires every
  child condition to pass; OR passes when at least one child condition passes.
- `Board`, `CH`, and `State` in `Monitor Mapping`: dashboard metadata for where the
  monitor block should appear and which channel/state it represents.
- `Dashboard`: previews all monitor, condition, and group blocks by tab,
  channel, state, and logic.
- `Board Channels` in `Dashboard`: enter custom labels such as
  `CH9, CH10, CH11, CH12` and apply them to selected monitor blocks. Use
  `Clear CH` for equipment that has no channel concept.
- `Board Layout` in `Dashboard`: customize the monitoring screen itself.
  Rows can be `channel` and columns can be `state` for an Excel-like
  `CH x state` grid, or you can invert the axes for a state-first board.
  `Tab order`, `State order`, and `CH list` control display order.
- `Unwrap`: removes a repeat or condition container while keeping its child
  blocks.
- `Run once` and `Run rows`: run from the top `Run` area.
- `Export Python`: export from `More > Actions`.

Newly recorded or added blocks are selected automatically, so you can name or
color them immediately. This still keeps the workflow compatible with JSON and
Python export. Repeat blocks are saved as `kind: "repeat"` with `children`.
Condition blocks are saved as `kind: "if_exists"` with `children`. The exported
Python script uses the same runner, so these blocks execute the same way from
the app and from Python.

Typical color/text monitoring workflow:

1. Capture or inspect the status component.
2. Add `Monitor color` with an expected value such as `#0000FF` for blue or
   `#FF0000` for red, then set a tolerance.
3. Add `Monitor text` when a field should contain `PASS`, `FAIL`, or `READY`.
4. In the `Sequence` tab, select the CH identity condition and the state condition,
   then use `Group AND` or `Group OR` to turn them into one monitor group block.
5. Set the selected block's `Board`, `CH`, and `State` in `Monitor Mapping`, for example
   `SK Commander`, `CH1`, and `RUNNING`.
6. In `Dashboard`, adjust `Board Layout` until `Board Preview` has the
   table structure you want.
7. Export or upload the workflow from the `Deploy` tab.
8. When a slave runs it, stdout/result logs include `MONITOR OK` or
   `MONITOR FAIL` rows.

For SK Commander-style screens where four copies of the same exe are open and
each window contains a `CH1`, `CH2`, `CH3`, `CH4`, or `CH9`-`CH12` label, apply
a `Window identity` to each selector first. Then create color/text monitor blocks
inside that window and group them with `Group AND` to express rules such as
"this window is CH11 and its status lamp is blue."

Choose the `Window identity` mode carefully:

- `contains`: fast matching for long labels such as `Device CH11 Ready`.
- `equals`: use when `CH1` must not accidentally match `CH11`.
- `regex`: use for spacing/case variants such as `CH 11`, `Ch11`, or `ch   11`;
  for example `\bch\s*11\b`.

## Capture Quality

`Capture Quality` appears in the Build workspace and as the `Capture` tab. It is
meant to catch false or weak captures in dense enterprise screens:

- `Good capture`: the click point is inside the UIA rectangle, the target type
  matches the action, and the target has usable identity metadata.
- `Check`: replay may work, but there is a weak signal such as missing
  `Window identity`, weak root metadata, or an unusual clickable control type.
- `Needs review`: the target is likely wrong, for example a type step captured a
  non-input control, the click point is outside the UIA rectangle, or the target
  has no stable identifying metadata.

When the recorder normalizes a raw `Text` child to its parent `Button` or
`Edit`, the check records that adjustment so you can see that the captured click
was intentionally changed to a more replayable target.

## Run Log tab

Open the `Run Log` tab on the right side while recording or running a workflow.
It shows:

- `State`: `Idle`, `Armed`, `Running`, or `Error`.
- `Mode`: the current capture or run mode.
- `Steps`: the number of recorded workflow steps. In the GUI, use `Sequence` to inspect the list.
- `Window`: the root window used to identify the target app.
- `Target`: the captured button, input, menu item, or other UI Automation
  control.
- `Point`: the screen coordinate of the last captured click.
- Event history: armed captures, recorded steps, run progress, stop requests,
  and errors.

If a click is recorded, the `Sequence` tab gains a new numbered step and the
`Workflow JSON` tab is updated immediately. If those do not change, the click
was not recorded.

The `Targets` tab lists the reusable agent targets discovered from the workflow:
`element_id`, role, first step number, and captured target metadata. This is the
view to check when you are building a small tool surface for an AI agent.

## Windows tab

Use `Test windows` when the same program is open more than once, or when one
management PC controls several similar devices. The debug output lists visible
top-level windows and shows:

- `ROOT`: whether the window title/class matches the recorded selector root.
- `MARK`: whether the optional `Window identity` was found inside that window.
- `HINT`: whether the saved root handle points to that candidate.
- `SEL`: which candidate would be used by the runner.
- `MARKER TARGET`: the component that satisfied the marker, if any.

For example, if four test-device windows all have the same title, type `CH 1`
in `Window match`, pick `equals` or `regex` when channel labels can overlap,
click `Apply match`, then click `Test windows`. Only the candidate window
containing the matching UIA component should show `MARK=Y` and `SEL=*`. Repeat
with `CH 2`, `CH 3`, and `CH 4` for separate agent elements such as
`ch_1_start_button` and `ch_2_start_button`.

Nested popup or dialog windows can appear with `SCOPE=nested` in this tab. That
means the popup is exposed by UI Automation as a child window under the main app
instead of a normal desktop top-level window. The runner now searches those
nested window candidates when replaying selectors, so a button recorded inside
that popup can still resolve as long as the popup is open at run time.

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
should not match unless the root window also matches. The `Run Log` tab shows
the last captured `Window` and `Target` so you can verify that the right app and
control were recorded.

If the root window is not found among normal desktop windows, the runner scans
the descendants of each visible desktop window and looks for nested UIA `Window`
controls. This is the fallback used for many in-application popup dialogs. It
does not make custom-drawn canvas controls visible; the target app still has to
expose useful UI Automation metadata.

The `Window identity` mode sets one of `window_marker.name_contains`,
`window_marker.name_equals`, or `window_marker.name_regex`. For advanced cases
you can edit the selector JSON directly:

```json
"window_marker": {
  "name_regex": "\\bch\\s*11\\b",
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

- The recorded click/type/wait/key/repeat/condition steps.
- Agent-readable `ELEMENTS` metadata for named controls.
- Window-marker comparator metadata for each named control.
- The current `Data Rows` text, if any.
- The `First row headers` setting.
- The `Row delay` setting.
- Helper functions: `list_elements()`, `click_element(id)`,
  `type_into(id, text, method="paste")`, `element_exists(id)`, and
  `press_key(keys)`.

Run the exported script on Windows in an environment where this package is
installed:

```powershell
python -m pip install -e .
python .\exported_workflow.py
```

If `Data Rows` is empty, the script runs the workflow once. If rows are present,
it runs once per row and applies the same `${col1}`, `${name}`, `${row}`, and
other template variables used by `Run rows`.

For agent use, the exported script exposes a small API:

```python
print(list_elements())
click_element("search_button")
type_into("message_input", "hello", clear=True)
type_into("message_input", "hello", clear=True, method="keys")
if element_exists("optional_popup_ok"):
    click_element("optional_popup_ok")
press_key("{ENTER}")
```

That lets a higher-level agent call stable named blocks instead of relying on
screen coordinates.

## Selector-only workflow

1. Click `Inspect`.
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
    "name_regex": "\\bch\\s*1\\b",
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
rig-ftp --help
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

## FTP Master/Slave Orchestration

`RigFtpCommander.exe` is the third program in this repository. Use it when each
rig/slave PC can reach an internal FTP server but you cannot open custom ports
for RPC, sockets, or agents.

The default flow is GUI-first. Launch `RigFtpCommander.exe` directly to open a
window with `Monitor & Run`, `This PC Agent`, and `Connection Setup` tabs.
PowerShell commands are kept for advanced automation through `RigFtpCli.exe`.

The FTP server is used as a shared spool:

- `packages/`: exported macro scripts or helper scripts uploaded by the master.
- `commands/{node}/pending/`: node-specific jobs.
- `commands/all/pending/`: broadcast jobs seen by all slaves.
- `status/`: one JSON heartbeat per slave.
- `results/{node}/`: one result JSON per completed job.
- `logs/{node}/`: stdout/stderr log files per completed job.
- `archive/{node}/`: command JSON files consumed by a slave.

Recommended GUI setup:

1. On the master PC, open `RigFtpCommander.exe`.
2. In `Connection Setup`, enter FTP host, username, password, and root dir, then save.
3. In `Monitor & Run`, enter known slave node ids and click `Init folders` once.
4. Upload macros from the `Deploy` tab in `WinAutomationPicker.exe` or from the
   `Monitor & Run` tab in `RigFtpCommander.exe`, with a title and notes.
5. Use `Server Setup > More > Export slave .info` in the `Monitor & Run` tab to
   create one `rig-ftp.info` per slave node.
6. On each slave PC, place `RigFtpCommander.exe` and that PC's `rig-ftp.info`
   in the same folder. Only `Node ID` needs to be unique per slave.
7. On each slave PC, click `Start agent` in the `This PC Agent` tab.
8. On the master PC, refresh the macro list, choose a target, click
   `Submit macro`, and monitor with status, results, screenshots, and emergency
   stop controls.

In the GUI, create or edit settings in the `Connection Setup` tab. Click
`Example` in the top `Connection Profile` area when you need a blank starting
config. The PowerShell equivalent is:

```powershell
.\RigFtpCli.exe init-config -o rig-ftp.info
```

Edit FTP address, credentials, root folder, and each slave's `node_id`. The
`slaves` list is the master roster. `alias` is the human-readable name such as
`PC04` and can also be used in target fields. `host` and `port` are metadata for
identification and documentation; the app does not open socket connections to
slaves. All control still goes through the FTP spool.

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

If you do not pass `-c`, both the GUI and CLI first look for `rig-ftp.info` in
the current folder and then next to the executable. If it is not found, the
legacy `rig-ftp.config.json` name is still accepted. This lets you place
`RigFtpCommander.exe` and `rig-ftp.info` in the same folder on a slave PC and
run it without extra setup. `RigFtpCli.exe` follows the same rule:

```powershell
.\RigFtpCli.exe slave
```

Initialize the server through the `Monitor & Run` tab, or with PowerShell:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info init-server --node rig-pc-01 --node rig-pc-02
```

Upload an exported macro script through either GUI, or with PowerShell:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info deploy `
  --file .\exported_workflow.py `
  --name smoke.py `
  --title "Boot smoke" `
  --notes "Power on, login, and basic status check"

.\RigFtpCli.exe -c .\rig-ftp.info packages
```

Run the slave loop from the `This PC Agent` tab, or with PowerShell:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info slave
```

Submit work from the master:

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

Placeholders in job commands and args support both `[name]` and `{name}`.
Values come from the slave config `variables`, the implicit `node_id`, and
job-level `--var KEY=VALUE`.

For credentials, either put `password` directly in the config or set
`password_env` to an environment variable name such as `RIG_FTP_PASSWORD`.

Monitor from the master:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info status
.\RigFtpCli.exe -c .\rig-ftp.info monitor --interval 5
.\RigFtpCli.exe -c .\rig-ftp.info results --node-id rig-pc-01
```

In the GUI, select a monitoring macro in the `Monitor & Run` tab, set `Monitor sec`,
and click `Start monitor loop`. The master submits that macro at the interval,
and each slave uploads `Monitor color/text` output through normal result logs.
`Monitor sec` has a 10-second floor so FTP is used as a low-rate spool, not as a
real-time socket.

Click `Refresh status` to populate the slave state table; the GUI shows the
latest status load time and row count. Click `Refresh results` to load result
logs for the selected node and show its latest result load time. `More > Export Excel`
writes the table to `.xlsx`. Double-click a slave row, or use
`View screenshot`, to write a screenshot job to FTP. The slave captures the
full interactive desktop at original PNG size, uploads it under
`screenshots/{node}/`, and the master opens the uploaded PNG.

Emergency stop and screen monitoring:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info stop --target rig-pc-01 --reason "wrong sequence"
.\RigFtpCli.exe -c .\rig-ftp.info clear-stop --target rig-pc-01
.\RigFtpCli.exe -c .\rig-ftp.info screenshot --target rig-pc-01 --label before_debug
.\RigFtpCli.exe -c .\rig-ftp.info screenshots --node-id rig-pc-01
```

`stop` terminates running `submit-shell` and `submit-python` child processes.
`submit-rig` jobs run inside the current process, so use short `--timeout`
values for rig commands that may hang.

Screenshots capture the full Windows virtual desktop through PowerShell and are
uploaded to `screenshots/{node}/`. They require the slave process to run in an
interactive desktop session. Services running in session 0 generally cannot
capture the visible user desktop.

Operational load guidance:

- This FTP design is for 10-60 second command/result spooling, not sub-second
  realtime control.
- Status refresh, result reads, and screenshots should be on-demand or low rate.
- Screenshots are large, so request them on demand and keep
  `max_screenshot_files` low. `min_screenshot_interval_seconds` prevents repeated
  screenshot requests to the same target.
- Idle slave polling is mostly FTP list/read/write work and should have low CPU
  impact, but very short `poll_interval_seconds` or many monitor jobs will load
  both the FTP server and slave Python processes. `poll_jitter_seconds` spreads
  slave polling over a random 0-N second window to avoid synchronized spikes.
- `max_result_files`, `max_log_files`, `max_archive_files`,
  `max_screenshot_files`, and `max_output_chars` bound FTP growth.

Retention cleanup:

```powershell
.\RigFtpCli.exe -c .\rig-ftp.info cleanup --node-id rig-pc-01
```

Slaves also apply cleanup after each processed job. The retention fields in
`runtime` keep result, log, archive, and screenshot counts bounded so the FTP
folder does not grow without limit. Command paths are sanitized and the local
test backend rejects `..` paths, so cleanup only touches known spool folders.

For lab trials without an FTP server, add `--local-root .\spool` to use a local
folder with the same layout.

## Build pipeline

GitHub Actions runs tests on Linux, builds icon-branded `WinAutomationPicker.exe`,
`RigCommander.exe`, `RigFtpCommander.exe`, and `RigFtpCli.exe` on
`windows-latest` with PyInstaller, uploads the executables as artifacts, and
updates the `latest` GitHub Release assets used by the download links above.

## Notes

- If the target app runs as Administrator, run this picker as Administrator too.
- UIA handles can change between launches, so selectors prefer stable metadata
  and keep handles as optional hints.
- Text input defaults to clipboard paste for reliability with arbitrary text.
- The executable is Windows-only.
