# Win Automation Picker

This project is a first-pass Windows automation builder. It captures the UI
Automation element under a real click, turns it into a stable selector, and can
reuse that selector for click and text input actions.

## Why this stack

- `pywinauto` with the `uia` backend targets Microsoft UI Automation, the same
  accessibility layer inspected by tools such as Inspect.exe.
- The selector stores `AutomationId`, `Name`, `ControlType`, `ClassName`, sibling
  index, window metadata, and an XPath-like path.
- `pynput` is only used to capture the next global mouse click for picking.

This is aimed at native Windows UI. Apps with custom canvas rendering, games,
or browser DOM content may expose poor UIA metadata and may need a specialized
adapter.

## Install on Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

## Run

```powershell
win-automation-picker
```

Workflow:

1. Click `Pick next click`.
2. Click a target component in another Windows app.
3. Review the generated selector JSON and XPath-like path.
4. Use `Click`, `Type`, `Copy`, or `Save selector`.

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

## Notes

- If the target app runs as Administrator, run this picker as Administrator too.
- UIA handles can change between launches, so selectors prefer stable metadata
  and keep handles as optional hints.
- Text input defaults to clipboard paste for reliability with arbitrary text.
