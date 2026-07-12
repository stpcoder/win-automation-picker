from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Sequence
import zipfile


def write_xlsx(path: str | Path, rows: Sequence[Sequence[Any]], *, sheet_name: str = "State") -> Path:
    return write_xlsx_workbook(path, [(sheet_name, rows)])


def write_xlsx_workbook(
    path: str | Path,
    sheets: Sequence[tuple[str, Sequence[Sequence[Any]]]],
) -> Path:
    if not sheets:
        raise ValueError("at least one worksheet is required")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_sheets(sheets)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(normalized)))
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("xl/workbook.xml", _workbook_xml([name for name, _rows in normalized]))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(normalized)))
        for index, (_name, rows) in enumerate(normalized, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))
        zf.writestr("xl/styles.xml", _styles_xml())
    return output


def _sheet_xml(rows: Sequence[Sequence[Any]]) -> str:
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            cells.append(_cell_xml(ref, value))
        row_xml.append(f'<row r="{row_index}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(row_xml)
        + "</sheetData></worksheet>"
    )


def _cell_xml(ref: str, value: Any) -> str:
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape("" if value is None else str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _content_types(sheet_count: int) -> str:
    worksheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  {worksheets}
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""


def _root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _workbook_xml(sheet_names: Sequence[str]) -> str:
    sheet_xml = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheet_xml}</sheets>
</workbook>"""


def _workbook_rels(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    style_id = sheet_count + 1
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {relationships}
  <Relationship Id="rId{style_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _normalize_sheets(
    sheets: Sequence[tuple[str, Sequence[Sequence[Any]]]],
) -> list[tuple[str, Sequence[Sequence[Any]]]]:
    normalized: list[tuple[str, Sequence[Sequence[Any]]]] = []
    used: set[str] = set()
    for index, (name, rows) in enumerate(sheets, start=1):
        base = "".join("_" if character in "[]:*?/\\" else character for character in str(name))
        base = (base.strip() or f"Sheet{index}")[:31]
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            suffix_text = f" ({suffix})"
            candidate = f"{base[: 31 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        used.add(candidate.casefold())
        normalized.append((candidate, rows))
    return normalized


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>"""
