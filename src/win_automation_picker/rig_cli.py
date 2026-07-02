from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

from .rig import (
    CommandResult,
    RigConfig,
    RigConfigError,
    RigExecutionError,
    check_host,
    inspect_firmware_manifest,
    list_remote_ports,
    resolve_named_command,
    results_to_json,
    run_firmware_flashes,
    run_host_scripts,
    run_serial_commands,
    select_hosts,
    select_serial_targets,
    write_example_config,
)


DEFAULT_CONFIG = "rig-commander.config.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rig-commander",
        description="Control Windows rig PCs and COM-port connected devices from a terminal.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to rig config JSON. Default: {DEFAULT_CONFIG}",
    )
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    init_parser = subparsers.add_parser("init-config", help="Write an example config file.")
    init_parser.add_argument("-o", "--output", default=DEFAULT_CONFIG, help="Output path.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    init_parser.set_defaults(func=_cmd_init_config)

    list_parser = subparsers.add_parser("list", help="List configured hosts and serial channels.")
    list_parser.add_argument("--json", action="store_true", help="Print JSON.")
    list_parser.set_defaults(func=_cmd_list)

    check_parser = subparsers.add_parser("check", help="Check local/remote PowerShell reachability.")
    _add_target_args(check_parser)
    _add_runtime_args(check_parser)
    check_parser.set_defaults(func=_cmd_check)

    ports_parser = subparsers.add_parser("ports", help="List actual COM ports on selected host PCs.")
    _add_target_args(ports_parser)
    _add_runtime_args(ports_parser)
    ports_parser.set_defaults(func=_cmd_ports)

    exec_parser = subparsers.add_parser("exec", help="Run PowerShell on selected host PCs.")
    _add_target_args(exec_parser)
    _add_runtime_args(exec_parser)
    exec_parser.add_argument("--script", "-s", required=True, help="PowerShell script to run.")
    exec_parser.set_defaults(func=_cmd_exec)

    send_parser = subparsers.add_parser("send", help="Send a raw command string to selected serial channels.")
    _add_target_args(send_parser)
    _add_runtime_args(send_parser)
    send_parser.add_argument("--command", "-m", required=True, help="Raw command to send.")
    send_parser.set_defaults(func=_cmd_send)

    run_parser = subparsers.add_parser("run", help="Run a named command from the config.")
    _add_target_args(run_parser)
    _add_runtime_args(run_parser)
    run_parser.add_argument("name", help="Named command, e.g. status, power_on, power_off.")
    run_parser.set_defaults(func=_cmd_run)

    monitor_parser = subparsers.add_parser("monitor", help="Repeat a raw or named command and print results.")
    _add_target_args(monitor_parser)
    _add_runtime_args(monitor_parser)
    command_group = monitor_parser.add_mutually_exclusive_group(required=True)
    command_group.add_argument("--name", help="Named command from the config.")
    command_group.add_argument("--command", "-m", help="Raw command to send.")
    monitor_parser.add_argument("--interval", type=float, default=5.0, help="Seconds between polls.")
    monitor_parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of polling rounds. 0 means run until Ctrl+C.",
    )
    monitor_parser.set_defaults(func=_cmd_monitor)

    firmware_parser = subparsers.add_parser("firmware", help="Inspect or flash firmware manifests.")
    firmware_subparsers = firmware_parser.add_subparsers(dest="firmware_command", required=True)

    firmware_inspect = firmware_subparsers.add_parser("inspect", help="Inspect a firmware XML manifest.")
    firmware_inspect.add_argument("--xml", required=True, help="Firmware XML manifest path.")
    firmware_inspect.add_argument("--json", action="store_true", help="Print JSON.")
    firmware_inspect.set_defaults(func=_cmd_firmware_inspect)

    firmware_flash = firmware_subparsers.add_parser("flash", help="Run configured firmware downloader.")
    _add_target_args(firmware_flash)
    _add_runtime_args(firmware_flash)
    firmware_flash.add_argument("--xml", required=True, help="Firmware XML path as seen by the rig PC.")
    firmware_flash.add_argument(
        "--mode",
        choices=("download-only", "format-all-download"),
        default="download-only",
        help="Firmware download mode.",
    )
    firmware_flash.add_argument(
        "--ready-command",
        default="",
        help="Optional named serial command to poll before flashing, e.g. status.",
    )
    firmware_flash.add_argument(
        "--ready-marker",
        default="",
        help="Optional text marker that indicates the device is ready for firmware download.",
    )
    firmware_flash.add_argument("--ready-timeout", type=float, default=0.0, help="Seconds to wait for ready marker.")
    firmware_flash.add_argument("--ready-interval", type=float, default=2.0, help="Seconds between ready polls.")
    firmware_flash.set_defaults(func=_cmd_firmware_flash)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (RigConfigError, RigExecutionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-t",
        "--target",
        action="append",
        default=[],
        help=(
            "Target selector. Use all, host_id, host_id:port_id, or tag:name. "
            "Can be repeated. Default: all."
        ),
    )


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=float, default=None, help="Command timeout seconds.")
    parser.add_argument("--parallel", action="store_true", help="Run selected serial targets concurrently.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated PowerShell instead of running it.")
    parser.add_argument("--json", action="store_true", help="Print JSON results.")


def _cmd_init_config(args: argparse.Namespace) -> int:
    path = write_example_config(args.output, force=bool(args.force))
    print(f"Wrote {path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if args.json:
        print(
            json.dumps(
                {
                    "hosts": [
                        {
                            "id": host.id,
                            "address": host.address,
                            "transport": host.transport,
                            "enabled": host.enabled,
                            "tags": list(host.tags),
                            "ports": [
                                {
                                    "id": port.id,
                                    "port": port.port,
                                    "baud": port.baud,
                                    "commands": sorted(port.commands),
                                }
                                for port in host.ports
                            ],
                        }
                        for host in config.hosts
                    ]
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return 0

    for host in config.hosts:
        state = "enabled" if host.enabled else "disabled"
        tags = f" tags={','.join(host.tags)}" if host.tags else ""
        print(f"{host.id} ({state}) address={host.address} transport={host.transport}{tags}")
        for port in host.ports:
            commands = ", ".join(sorted(port.commands)) or "-"
            print(f"  {port.id}: {port.port} baud={port.baud} commands={commands}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    timeout = _timeout(args, config)
    results = [
        check_host(target.host, timeout=timeout, dry_run=bool(args.dry_run))
        for target in select_hosts(config, args.target)
    ]
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_ports(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    timeout = _timeout(args, config)
    results = [
        list_remote_ports(target.host, timeout=timeout, dry_run=bool(args.dry_run))
        for target in select_hosts(config, args.target)
    ]
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_exec(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_hosts(config, args.target)
    results = run_host_scripts(
        targets,
        str(args.script),
        timeout=_timeout(args, config),
        parallel=bool(args.parallel),
        dry_run=bool(args.dry_run),
    )
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_send(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    commands = {target.label(): str(args.command) for target in targets}
    results = run_serial_commands(
        targets,
        commands,
        timeout=_timeout(args, config),
        parallel=bool(args.parallel),
        dry_run=bool(args.dry_run),
    )
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    commands = {target.label(): resolve_named_command(target, args.name) for target in targets}
    results = run_serial_commands(
        targets,
        commands,
        timeout=_timeout(args, config),
        parallel=bool(args.parallel),
        dry_run=bool(args.dry_run),
    )
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_monitor(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    timeout = _timeout(args, config)
    interval = max(0.1, float(args.interval))
    count = max(0, int(args.count))
    round_index = 0

    while True:
        round_index += 1
        if args.name:
            commands = {target.label(): resolve_named_command(target, args.name) for target in targets}
        else:
            commands = {target.label(): str(args.command) for target in targets}

        print(f"# poll {round_index} {time.strftime('%Y-%m-%d %H:%M:%S')}")
        results = run_serial_commands(
            targets,
            commands,
            timeout=timeout,
            parallel=bool(args.parallel),
            dry_run=bool(args.dry_run),
        )
        _print_results(results, as_json=bool(args.json))
        if _exit_code(results) != 0 and count == 1:
            return _exit_code(results)
        if count and round_index >= count:
            return _exit_code(results)
        time.sleep(interval)


def _cmd_firmware_inspect(args: argparse.Namespace) -> int:
    manifest = inspect_firmware_manifest(args.xml)
    if args.json:
        print(json.dumps(manifest.to_mapping(), indent=2, ensure_ascii=True))
        return 0
    print(f"Firmware XML: {manifest.path}")
    if not manifest.files:
        print("No image/file attributes were found.")
        return 0
    for item in manifest.files:
        print(f"{item.index:>3}. {item.tag}: {item.path}")
    return 0


def _cmd_firmware_flash(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    results = run_firmware_flashes(
        targets,
        xml_path=str(args.xml),
        mode=str(args.mode),
        timeout=args.timeout,
        parallel=bool(args.parallel),
        dry_run=bool(args.dry_run),
        ready_command=str(args.ready_command or ""),
        ready_marker=str(args.ready_marker or ""),
        ready_timeout=float(args.ready_timeout or 0.0),
        ready_interval=float(args.ready_interval or 2.0),
    )
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _load_config(path: str | Path) -> RigConfig:
    return RigConfig.load(path)


def _timeout(args: argparse.Namespace, config: RigConfig) -> float:
    return float(args.timeout if args.timeout is not None else config.default_timeout_seconds)


def _print_results(results: Sequence[CommandResult], *, as_json: bool = False) -> None:
    if as_json:
        print(results_to_json(results))
        return

    for result in results:
        state = "OK" if result.ok else "FAIL"
        mode = " dry-run" if result.dry_run else ""
        print(f"[{state}]{mode} {result.target}")
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)


def _exit_code(results: Sequence[CommandResult]) -> int:
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
