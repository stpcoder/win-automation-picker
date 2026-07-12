from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Callable, Protocol, Sequence

from .rig import SerialPortConfig


MAX_CONSOLE_BUFFER_CHARS = 256_000
DEFAULT_BOOT_MARKERS = {
    "preloader": "PRELOADER",
    "bootloader": "BOOTLOADER",
    "lk2]": "LK",
    "lk]": "LK",
    "console": "OS CONSOLE",
    "login:": "OS LOGIN",
}
DEFAULT_ERROR_MARKERS = ("error", "fail", "panic", "exception")


class SerialConsoleError(RuntimeError):
    """Raised when a persistent fixture console cannot be used safely."""


class SerialConnection(Protocol):
    @property
    def in_waiting(self) -> int: ...

    @property
    def is_open(self) -> bool: ...

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[SerialPortConfig], SerialConnection]
OutputCallback = Callable[[str, str], None]
StateCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class SerialSequenceBlock:
    name: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class SerialCommandResult:
    block: str
    command: str
    ok: bool
    response: str
    timed_out: bool = False


@dataclass(frozen=True)
class SerialSequenceResult:
    channel: str
    ok: bool
    stopped: bool
    completed_commands: int
    total_commands: int
    commands: tuple[SerialCommandResult, ...] = ()


def validate_ascii_text(value: str) -> str:
    for character in value:
        code = ord(character)
        if code < 0x20 or code > 0x7E:
            raise SerialConsoleError(
                "Console input accepts printable ASCII only. Use the Enter and Ctrl buttons for control keys."
            )
    return value


def parse_serial_sequence(text: str) -> tuple[SerialSequenceBlock, ...]:
    blocks: list[SerialSequenceBlock] = []
    name = "UNLABELED"
    commands: list[str] = []

    def flush() -> None:
        nonlocal commands
        if commands:
            blocks.append(SerialSequenceBlock(name=name, commands=tuple(commands)))
            commands = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            flush()
            name = line.rstrip(";")
            continue
        if re.search(r";[ \t]+\S", raw_line):
            raise SerialConsoleError(
                "SEQ commands cannot contain whitespace after ';'. Use 'cmd1;cmd2;' without a gap."
            )
        if not line.endswith(";"):
            raise SerialConsoleError(f"SEQ command line must end with ';': {line}")
        for part in line.split(";"):
            command = part.strip()
            if command:
                validate_ascii_text(command)
                commands.append(f"{command};")
    flush()
    return tuple(blocks)


def detect_boot_state(
    text: str,
    markers: dict[str, str] | None = None,
) -> str:
    configured = markers or DEFAULT_BOOT_MARKERS
    folded = text.casefold()
    latest_index = -1
    latest_state = "CONNECTED"
    for marker, state in configured.items():
        index = folded.rfind(marker.casefold())
        if index > latest_index:
            latest_index = index
            latest_state = state
    return latest_state


def verify_serial_port_binding(
    config: SerialPortConfig,
    observations: Sequence[object],
) -> object:
    configured = next(
        (
            port
            for port in observations
            if str(getattr(port, "device", "") or "").casefold() == config.port.casefold()
        ),
        None,
    )
    if configured is None:
        raise SerialConsoleError(f"Configured COM port is not present: {config.port}")
    if not config.console_identity:
        return configured

    expected = config.console_identity.casefold()

    def identity_text(port: object) -> str:
        return " ".join(
            str(getattr(port, name, "") or "")
            for name in ("device", "description", "hwid", "location")
        ).casefold()

    if expected in identity_text(configured):
        return configured
    matching = [port for port in observations if expected in identity_text(port)]
    if len(matching) == 1:
        actual = str(getattr(matching[0], "device", "") or "")
        raise SerialConsoleError(
            f"Console identity moved from {config.port} to {actual}. Run COM compare before control."
        )
    if len(matching) > 1:
        raise SerialConsoleError(
            f"Console identity is ambiguous on {len(matching)} COM ports. "
            "Configure a USB serial-specific HWID."
        )
    actual = identity_text(configured).strip() or "unknown device"
    raise SerialConsoleError(
        f"Console identity mismatch on {config.port}. "
        f"Expected {config.console_identity}; actual {actual}."
    )


def _default_connection_factory(config: SerialPortConfig) -> SerialConnection:
    try:
        import serial
        import serial.tools.list_ports
    except ImportError as exc:
        raise SerialConsoleError("pyserial is required for the four-channel console.") from exc
    verify_serial_port_binding(config, tuple(serial.tools.list_ports.comports()))
    return serial.Serial(
        port=config.port,
        baudrate=config.baud,
        timeout=0.1,
        write_timeout=max(0.1, config.write_timeout_ms / 1000.0),
    )


class SerialConsoleSession:
    def __init__(
        self,
        config: SerialPortConfig,
        *,
        connection_factory: ConnectionFactory | None = None,
        output_callback: OutputCallback | None = None,
        state_callback: StateCallback | None = None,
        max_buffer_chars: int = MAX_CONSOLE_BUFFER_CHARS,
    ) -> None:
        self.config = config
        self._connection_factory = connection_factory or _default_connection_factory
        self._output_callback = output_callback or (lambda _channel, _text: None)
        self._state_callback = state_callback or (lambda _channel, _state: None)
        self._output_taps: dict[int, OutputCallback] = {}
        self._output_taps_lock = threading.Lock()
        self._next_output_tap = 1
        self._max_buffer_chars = max(4096, int(max_buffer_chars))
        self._connection: SerialConnection | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._condition = threading.Condition()
        self._history = ""
        self._history_offset = 0
        self._last_rx_at = 0.0
        self._state = "DISCONNECTED"
        self._keepalive_interval = 0.0
        self._next_keepalive_at = 0.0

    @property
    def connected(self) -> bool:
        return bool(self._connection and self._connection.is_open and not self._stop.is_set())

    @property
    def state(self) -> str:
        return self._state

    @property
    def history(self) -> str:
        with self._condition:
            return self._history

    def add_output_tap(self, callback: OutputCallback) -> int:
        with self._output_taps_lock:
            token = self._next_output_tap
            self._next_output_tap += 1
            self._output_taps[token] = callback
        return token

    def remove_output_tap(self, token: int) -> None:
        with self._output_taps_lock:
            self._output_taps.pop(int(token), None)

    def connect(self) -> None:
        if self.connected:
            return
        self._stop.clear()
        try:
            self._connection = self._connection_factory(self.config)
        except Exception as exc:
            self._set_state("ERROR")
            raise SerialConsoleError(
                f"Could not open {self.config.id} ({self.config.port} @ {self.config.baud}): {exc}"
            ) from exc
        self._set_state("CONNECTED")
        self._reader = threading.Thread(
            target=self._reader_loop,
            name=f"serial-console-{self.config.id}",
            daemon=True,
        )
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._reader = None
        self._set_state("DISCONNECTED")
        with self._condition:
            self._condition.notify_all()

    def set_keepalive_enter(self, interval_seconds: float) -> None:
        self._keepalive_interval = max(0.0, float(interval_seconds))
        self._next_keepalive_at = (
            time.monotonic() + self._keepalive_interval if self._keepalive_interval else 0.0
        )

    def send_ascii(
        self,
        value: str,
        *,
        append_enter: bool = True,
        character_delay_ms: int = 0,
    ) -> int:
        validate_ascii_text(value)
        payload = value.encode("ascii")
        written = 0
        if character_delay_ms > 0:
            for byte in payload:
                written += self._write(bytes((byte,)))
                time.sleep(max(0, character_delay_ms) / 1000.0)
        else:
            written += self._write(payload)
        if append_enter:
            written += self.send_enter()
        self._emit(f"\n[TX] {value}{' <ENTER>' if append_enter else ''}\n")
        return written

    def send_enter(self) -> int:
        return self._write(self.config.newline.encode("ascii"))

    def send_control(self, key: str) -> int:
        normalized = key.strip().casefold()
        if len(normalized) != 1 or not "a" <= normalized <= "z":
            raise SerialConsoleError("Control key must be one ASCII letter.")
        code = ord(normalized) - ord("a") + 1
        written = self._write(bytes((code,)))
        self._emit(f"\n[TX] <CTRL+{normalized.upper()}>\n")
        return written

    def run_sequence(
        self,
        text: str,
        *,
        stop_event: threading.Event | None = None,
        command_timeout_seconds: float = 30.0,
        idle_seconds: float = 0.75,
        character_delay_ms: int = 0,
        error_markers: Sequence[str] = DEFAULT_ERROR_MARKERS,
        progress_callback: Callable[[str], None] | None = None,
    ) -> SerialSequenceResult:
        if not self.connected:
            raise SerialConsoleError(f"{self.config.id} is not connected.")
        blocks = parse_serial_sequence(text)
        total = sum(len(block.commands) for block in blocks)
        stop = stop_event or threading.Event()
        progress = progress_callback or (lambda _message: None)
        results: list[SerialCommandResult] = []
        for block_index, block in enumerate(blocks, start=1):
            progress(f"GRID {block.name}")
            for command in block.commands:
                if stop.is_set():
                    return SerialSequenceResult(
                        channel=self.config.id,
                        ok=False,
                        stopped=True,
                        completed_commands=len(results),
                        total_commands=total,
                        commands=tuple(results),
                    )
                response, timed_out = self.send_and_wait(
                    command,
                    timeout_seconds=command_timeout_seconds,
                    idle_seconds=idle_seconds,
                    character_delay_ms=character_delay_ms,
                    stop_event=stop,
                )
                if stop.is_set():
                    return SerialSequenceResult(
                        channel=self.config.id,
                        ok=False,
                        stopped=True,
                        completed_commands=len(results),
                        total_commands=total,
                        commands=tuple(results),
                    )
                failed = timed_out or any(
                    re.search(re.escape(marker), response, re.IGNORECASE)
                    for marker in error_markers
                    if marker
                )
                result = SerialCommandResult(
                    block=block.name,
                    command=command,
                    ok=not failed,
                    response=response,
                    timed_out=timed_out,
                )
                results.append(result)
                progress(f"{'PASS' if result.ok else 'FAIL'} {block.name} {command}")
                if failed:
                    return SerialSequenceResult(
                        channel=self.config.id,
                        ok=False,
                        stopped=False,
                        completed_commands=len(results),
                        total_commands=total,
                        commands=tuple(results),
                    )
            progress(f"GRID_DONE {block_index}/{len(blocks)} {block.name}")
        return SerialSequenceResult(
            channel=self.config.id,
            ok=True,
            stopped=False,
            completed_commands=len(results),
            total_commands=total,
            commands=tuple(results),
        )

    def send_and_wait(
        self,
        command: str,
        *,
        timeout_seconds: float,
        idle_seconds: float,
        character_delay_ms: int = 0,
        stop_event: threading.Event | None = None,
    ) -> tuple[str, bool]:
        with self._condition:
            start = self._history_offset + len(self._history)
            before_rx = self._last_rx_at
        self.send_ascii(
            command,
            append_enter=True,
            character_delay_ms=character_delay_ms,
        )
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        seen_response = False
        with self._condition:
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    break
                remaining = max(0.01, deadline - time.monotonic())
                self._condition.wait(timeout=min(0.1, remaining))
                seen_response = seen_response or self._last_rx_at > before_rx
                if seen_response and time.monotonic() - self._last_rx_at >= max(0.02, idle_seconds):
                    break
                if not self.connected:
                    break
            relative_start = start - self._history_offset
            if relative_start < 0:
                response = "[RESPONSE TRUNCATED TO CONSOLE BUFFER]\n" + self._history
            else:
                response = self._history[relative_start:]
        return response, not seen_response

    def _write(self, data: bytes) -> int:
        connection = self._connection
        if connection is None or not connection.is_open:
            raise SerialConsoleError(f"{self.config.id} is not connected.")
        try:
            with self._write_lock:
                written = int(connection.write(data))
        except Exception as exc:
            self._set_state("ERROR")
            raise SerialConsoleError(f"Serial write failed on {self.config.id}: {exc}") from exc
        if written != len(data):
            raise SerialConsoleError(
                f"Serial write was incomplete on {self.config.id}: {written}/{len(data)} bytes."
            )
        return written

    def _reader_loop(self) -> None:
        try:
            while not self._stop.is_set():
                connection = self._connection
                if connection is None or not connection.is_open:
                    break
                waiting = max(1, int(connection.in_waiting))
                chunk = connection.read(min(waiting, 8192))
                if chunk:
                    self._receive(chunk.decode("ascii", errors="replace"))
                self._maybe_send_keepalive()
        except Exception as exc:
            if not self._stop.is_set():
                self._emit(f"\n[ERROR] {exc}\n")
                self._set_state("ERROR")
        finally:
            if not self._stop.is_set() and self._state != "ERROR":
                self._set_state("DISCONNECTED")

    def _receive(self, text: str) -> None:
        with self._condition:
            combined = self._history + text
            overflow = max(0, len(combined) - self._max_buffer_chars)
            if overflow:
                self._history_offset += overflow
            self._history = combined[-self._max_buffer_chars :]
            self._last_rx_at = time.monotonic()
            state = detect_boot_state(self._history)
            self._condition.notify_all()
        self._emit(text)
        if state != self._state:
            self._set_state(state)

    def _maybe_send_keepalive(self) -> None:
        interval = self._keepalive_interval
        if not interval or time.monotonic() < self._next_keepalive_at:
            return
        self.send_enter()
        self._emit("\n[TX] <ENTER keepalive>\n")
        self._next_keepalive_at = time.monotonic() + interval

    def _emit(self, text: str) -> None:
        self._output_callback(self.config.id, text)
        with self._output_taps_lock:
            taps = tuple(self._output_taps.values())
        for callback in taps:
            try:
                callback(self.config.id, text)
            except Exception:
                continue

    def _set_state(self, state: str) -> None:
        self._state = state
        self._state_callback(self.config.id, state)


class SerialConsoleManager:
    def __init__(self, *, max_channels: int = 4) -> None:
        self.max_channels = max(1, int(max_channels))
        self.sessions: dict[str, SerialConsoleSession] = {}

    def add(self, session: SerialConsoleSession) -> None:
        if session.config.id not in self.sessions and len(self.sessions) >= self.max_channels:
            raise SerialConsoleError(f"This console supports up to {self.max_channels} channels.")
        self.sessions[session.config.id] = session

    def selected(self, channel_ids: Sequence[str]) -> list[SerialConsoleSession]:
        result: list[SerialConsoleSession] = []
        for channel_id in channel_ids:
            try:
                result.append(self.sessions[channel_id])
            except KeyError as exc:
                raise SerialConsoleError(f"Unknown console channel: {channel_id}") from exc
        return result

    def close_all(self) -> None:
        for session in tuple(self.sessions.values()):
            session.close()
