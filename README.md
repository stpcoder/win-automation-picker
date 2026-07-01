# Win Automation Picker

Win Automation Picker is a Windows UI Automation recorder and runner. It can
inspect the UI element under a real click, save that element as a reusable
selector, record click/type/wait steps, and replay the recorded workflow once or
for every pasted spreadsheet row.

## Download

Download the latest Windows executable:

https://github.com/stpcoder/win-automation-picker/releases/latest/download/WinAutomationPicker.exe

Windows may show a SmartScreen warning because the executable is not code-signed.

## What it does

- Finds Windows UI Automation elements under the next mouse click.
- Stores selectors using `AutomationId`, `Name`, `ControlType`, `ClassName`,
  sibling index, window metadata, and an XPath-like path.
- Runs one-off `Click` and `Type` actions against the current selector.
- Records workflows with `Record click step`, `Record type step`, and `Add wait`.
- Saves and loads selector JSON and workflow JSON.
- Accepts Excel/Google Sheets-style pasted rows in the `Data rows` tab.
- Replays the same workflow for every pasted row with template variables such as
  `${col1}`, `${col2}`, `${name}`, `${message}`, and `${row}`.

This is aimed at native Windows UI. Apps with custom canvas rendering, games,
or browser DOM content may expose poor UIA metadata and may need a specialized
adapter.

## Basic workflow

1. Open `WinAutomationPicker.exe`.
2. Click `Record click step`, then click a target button in another Windows app.
3. Put a text template in `Text / template`, for example `${message}`.
4. Click `Record type step`, then click the target text input in the other app.
5. Paste spreadsheet data into `Data rows`.
6. Click `Run rows`.

Example pasted data:

```tsv
name	message
Alice	Hello Alice
Bob	Hello Bob
```

If `First row headers` is enabled, `${name}` and `${message}` are available. The
same row can also be addressed by `${col1}` and `${col2}`. If there is no header
row, disable `First row headers` and use `${col1}`, `${col2}`, and so on.

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
  ]
}
```

## Install from source on Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
win-automation-picker
```

## Build pipeline

GitHub Actions runs tests on Linux, builds `WinAutomationPicker.exe` on
`windows-latest` with PyInstaller, uploads the executable as an artifact, and
updates the `latest` GitHub Release asset used by the download link above.

## Notes

- If the target app runs as Administrator, run this picker as Administrator too.
- UIA handles can change between launches, so selectors prefer stable metadata
  and keep handles as optional hints.
- Text input defaults to clipboard paste for reliability with arbitrary text.
- The executable is Windows-only.
