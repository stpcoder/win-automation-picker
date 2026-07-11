import zipfile

from win_automation_picker.xlsx_export import write_xlsx, write_xlsx_workbook


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


def test_write_xlsx_workbook_creates_multiple_state_sheets(tmp_path) -> None:
    path = tmp_path / "fleet.xlsx"

    write_xlsx_workbook(
        path,
        [
            ("PC State", [["Node"], ["PC04"]]),
            ("CH Inventory", [["CH", "SoC"], ["CH11", "MTK25D"]]),
        ],
    )

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        workbook = zf.read("xl/workbook.xml").decode("utf-8")
        second_sheet = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert "xl/worksheets/sheet2.xml" in names
    assert "CH Inventory" in workbook
    assert "MTK25D" in second_sheet
