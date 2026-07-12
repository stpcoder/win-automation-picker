from win_automation_picker.ftp_app import RigFtpApp, natural_label_key


def test_natural_label_key_orders_free_form_channel_names() -> None:
    labels = ["CH12", "QC-DL", "CH9", "CH11", "CH10"]

    assert sorted(labels, key=natural_label_key) == ["CH9", "CH10", "CH11", "CH12", "QC-DL"]


def test_campaign_attempt_label_omits_missing_repeat_total() -> None:
    assert RigFtpApp._campaign_attempt_label({"campaign_attempt": 1}) == "1"
    assert RigFtpApp._campaign_attempt_label({"campaign_attempt": 1, "campaign_repeat_count": 3}) == "1/3"
    assert RigFtpApp._campaign_attempt_label({}) == ""
