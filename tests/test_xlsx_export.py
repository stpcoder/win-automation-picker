import zipfile

from win_automation_picker.xlsx_export import write_xlsx


def test_write_xlsx_creates_excel_workbook(tmp_path) -> None:
    path = tmp_path / "state.xlsx"

    write_xlsx(path, [["Alias", "Node"], ["PC04", "rig-pc-04"]], sheet_name="Slave State")

    assert path.exists()
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "PC04" in sheet
    assert "rig-pc-04" in sheet
