from win_automation_picker.recipe import AutomationRecipe, AutomationStep, DataSet, render_template
from win_automation_picker.selector import SelectorSegment, UISelector


def test_dataset_parses_excel_tsv_with_headers_and_col_aliases() -> None:
    dataset = DataSet.from_text("name\tmessage\nAlice\tHello\nBob\tHi", first_row_headers=True)

    assert dataset.headers == ["name", "message"]
    assert dataset.rows == [
        {
            "row": "1",
            "row_index": "1",
            "col1": "Alice",
            "name": "Alice",
            "col2": "Hello",
            "message": "Hello",
        },
        {
            "row": "2",
            "row_index": "2",
            "col1": "Bob",
            "name": "Bob",
            "col2": "Hi",
            "message": "Hi",
        },
    ]


def test_dataset_without_headers_uses_col_names() -> None:
    dataset = DataSet.from_text("Alice\tHello\nBob\tHi", first_row_headers=False)

    assert dataset.headers == ["col1", "col2"]
    assert dataset.rows[0]["col1"] == "Alice"
    assert dataset.rows[0]["col2"] == "Hello"


def test_render_template_replaces_known_variables_only() -> None:
    rendered = render_template(
        "Dear ${name}, row ${row}: ${missing}",
        {"name": "Alice", "row": "3"},
    )

    assert rendered == "Dear Alice, row 3: ${missing}"


def test_recipe_round_trip_json() -> None:
    selector = UISelector(
        root=SelectorSegment(control_type="Window", name="App"),
        path=[SelectorSegment(control_type="Edit", automation_id="input")],
    )
    recipe = AutomationRecipe(
        steps=[
            AutomationStep.click(selector),
            AutomationStep.type(selector, "${message}", clear=True),
            AutomationStep.wait(0.25),
        ]
    )

    restored = AutomationRecipe.from_json(recipe.to_json())

    assert restored == recipe
