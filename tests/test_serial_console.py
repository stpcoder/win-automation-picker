from __future__ import annotations

from collections import deque
import time

import pytest

from win_automation_picker.rig import SerialPortConfig
from win_automation_picker.serial_console import (
    SerialConsoleError,
    SerialConsoleSession,
    detect_boot_state,
    parse_serial_sequence,
    validate_ascii_text,
)


class FakeConnection:
    def __init__(self) -> None:
        self.is_open = True
        self.pending: deque[bytes] = deque()
        self.writes: list[bytes] = []

    @property
    def in_waiting(self) -> int:
        return len(self.pending[0]) if self.pending else 0

    def read(self, _size: int) -> bytes:
        if self.pending:
            return self.pending.popleft()
        time.sleep(0.005)
        return b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        if data.endswith(b"\r\n"):
            self.pending.append(b"command OK\r\nLK2]")
        return len(data)

    def close(self) -> None:
        self.is_open = False


def _config() -> SerialPortConfig:
    return SerialPortConfig(id="CH1", port="COM3", baud=115200)


def test_ascii_input_accepts_seq_punctuation_and_rejects_unicode() -> None:
    assert validate_ascii_text("tm -de 0x2; /data/clk.sh -lf")
    with pytest.raises(SerialConsoleError, match="ASCII"):
        validate_ascii_text("한글")


def test_parse_serial_sequence_preserves_grids_and_semicolon_commands() -> None:
    blocks = parse_serial_sequence("#BOOT\nexit;exit;did;\n#RUN\nlog 0xff;tm -de 0x2;")

    assert blocks[0].name == "#BOOT"
    assert blocks[0].commands == ("exit;", "exit;", "did;")
    assert blocks[1].commands == ("log 0xff;", "tm -de 0x2;")


@pytest.mark.parametrize(
    "text, message",
    [
        ("#RUN\nlog 0xff; tm -de 0x2;", "whitespace"),
        ("#RUN\nlog 0xff", "end with"),
    ],
)
def test_parse_serial_sequence_rejects_field_invalid_command_layout(text, message) -> None:
    with pytest.raises(SerialConsoleError, match=message):
        parse_serial_sequence(text)


def test_boot_state_uses_latest_console_marker() -> None:
    assert detect_boot_state("PRELOADER... bootloader... LK2]") == "LK"
    assert detect_boot_state("LK2]\nAndroid console ready") == "OS CONSOLE"


def test_session_sends_control_and_character_delayed_ascii() -> None:
    connection = FakeConnection()
    session = SerialConsoleSession(_config(), connection_factory=lambda _config: connection)
    session.connect()
    try:
        session.send_ascii("exit", character_delay_ms=1)
        session.send_control("c")
    finally:
        session.close()

    assert connection.writes[:4] == [b"e", b"x", b"i", b"t"]
    assert b"\r\n" in connection.writes
    assert b"\x03" in connection.writes


def test_session_runs_sequence_and_detects_lk_prompt() -> None:
    connection = FakeConnection()
    states: list[str] = []
    session = SerialConsoleSession(
        _config(),
        connection_factory=lambda _config: connection,
        state_callback=lambda _channel, state: states.append(state),
    )
    session.connect()
    try:
        result = session.run_sequence(
            "#BOOT\nexit;exit;",
            command_timeout_seconds=1.0,
            idle_seconds=0.02,
        )
    finally:
        session.close()

    assert result.ok
    assert result.completed_commands == 2
    assert "LK" in states
