from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .device_acceptance import DeviceAcceptanceError, write_device_acceptance_report
from .device_qualification import (
    approve_device_qualification_candidate,
    approve_repeated_device_qualification_candidate,
    write_device_qualification_candidate,
    write_repeated_device_qualification_candidate,
)
from .rig import (
    CommandResult,
    RigConfig,
    RigConfigError,
    RigExecutionError,
    build_device_preflight_report,
    check_host,
    inspect_firmware_manifest,
    inspect_firmware_package,
    list_remote_ports,
    resolve_named_command,
    results_to_json,
    run_device_probe,
    run_device_update,
    run_firmware_flashes,
    run_host_scripts,
    run_qdl_raw_write,
    run_serial_command,
    run_serial_commands,
    select_hosts,
    select_serial_targets,
    write_example_config,
)
from .windows_compat import assess_windows_environment


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

    firmware_parser = subparsers.add_parser("firmware", help="Inspect packages or run a configured downloader.")
    firmware_subparsers = firmware_parser.add_subparsers(dest="firmware_command", required=True)

    firmware_inspect = firmware_subparsers.add_parser(
        "inspect",
        help="Inspect a firmware folder, XML/JSON descriptor, or installer ZIP.",
    )
    firmware_inspect.add_argument(
        "--xml",
        "--package",
        dest="xml",
        required=True,
        help="Firmware folder, XML/JSON descriptor, or installer ZIP path.",
    )
    firmware_inspect.add_argument("--json", action="store_true", help="Print JSON.")
    firmware_inspect.add_argument(
        "--vendor",
        choices=("qualcomm", "mediatek"),
        default="",
        help="Enable vendor package inspection instead of generic XML listing.",
    )
    firmware_inspect.add_argument(
        "--adapter",
        choices=("auto", "generic", "qualcomm-qdl", "mediatek-genio"),
        default="auto",
    )
    firmware_inspect.add_argument(
        "--storage",
        choices=("emmc", "nand", "nvme", "spinor", "ufs"),
        default="ufs",
    )
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

    device_parser = subparsers.add_parser(
        "device",
        help="Probe, power, and safely update one or more configured fixture channels.",
    )
    device_subparsers = device_parser.add_subparsers(dest="device_command", required=True)

    device_system = device_subparsers.add_parser(
        "system-check",
        help="Check Windows build, architecture, PowerShell, and pyserial prerequisites.",
    )
    device_system.add_argument("--json", action="store_true")
    device_system.set_defaults(func=_cmd_device_system_check)

    device_probe = device_subparsers.add_parser("probe", help="Check COM, download identity, or ADB state.")
    _add_target_args(device_probe)
    _add_runtime_args(device_probe)
    device_probe.add_argument(
        "--phase",
        choices=("normal", "download", "post"),
        default="normal",
        help="normal checks COM/ADB; download checks downloader/XML/USB identity; post checks COM/ADB.",
    )
    device_probe.add_argument("--xml", default="", help="Firmware XML path for download phase.")
    device_probe.add_argument("--xml-sha256", default="", help="Expected XML SHA-256.")
    device_probe.set_defaults(func=_cmd_device_probe)

    device_power = device_subparsers.add_parser("power", help="Run configured power commands over COM.")
    _add_target_args(device_power)
    _add_runtime_args(device_power)
    device_power.add_argument("action", choices=("on", "off", "cycle"))
    device_power.add_argument("--cycle-delay", type=float, default=2.0, help="Seconds between off and on.")
    device_power.set_defaults(func=_cmd_device_power)

    device_preflight = device_subparsers.add_parser(
        "preflight",
        help="Validate every destructive-update gate and optionally probe the target PC.",
    )
    _add_target_args(device_preflight)
    _add_runtime_args(device_preflight)
    _add_device_update_args(device_preflight)
    device_preflight.add_argument(
        "--static-only",
        action="store_true",
        help="Do not check the target PC's live COM, downloader, XML, and USB identity.",
    )
    device_preflight.set_defaults(func=_cmd_device_preflight)

    device_update = device_subparsers.add_parser(
        "update",
        help="Run the allowlisted downloader after all vendor and target gates pass.",
    )
    _add_target_args(device_update)
    _add_runtime_args(device_update)
    _add_device_update_args(device_update)
    device_update.add_argument(
        "--run-preloader-exit",
        action="store_true",
        help="Send the channel's configured preloader_exit command before MTK update.",
    )
    device_update.add_argument(
        "--journal-root",
        default="rig-firmware-results",
        help="Folder for per-step firmware manifests and logs.",
    )
    device_update.set_defaults(func=_cmd_device_update)

    device_qualify = device_subparsers.add_parser(
        "qualify",
        help="Prepare and independently approve a field qualification reference.",
    )
    qualification_subparsers = device_qualify.add_subparsers(
        dest="qualification_command",
        required=True,
    )
    qualification_prepare = qualification_subparsers.add_parser(
        "prepare",
        help="Create an explicitly unapproved candidate from successful device evidence.",
    )
    qualification_prepare.add_argument("--evidence", required=True)
    qualification_prepare.add_argument("--prepared-by", required=True)
    qualification_prepare.add_argument("--source-ticket", required=True)
    qualification_prepare.add_argument("--output", required=True)
    qualification_prepare.add_argument("--json", action="store_true")
    qualification_prepare.set_defaults(func=_cmd_device_qualification_prepare)
    qualification_prepare_set = qualification_subparsers.add_parser(
        "prepare-set",
        help="Create a production qualification candidate from 3-20 unique successful runs.",
    )
    qualification_prepare_set.add_argument(
        "--evidence", action="append", required=True
    )
    qualification_prepare_set.add_argument("--minimum-runs", type=int, default=3)
    qualification_prepare_set.add_argument("--prepared-by", required=True)
    qualification_prepare_set.add_argument("--source-ticket", required=True)
    qualification_prepare_set.add_argument("--output", required=True)
    qualification_prepare_set.add_argument("--json", action="store_true")
    qualification_prepare_set.set_defaults(func=_cmd_device_qualification_prepare_set)
    qualification_approve = qualification_subparsers.add_parser(
        "approve",
        help="Revalidate candidate evidence and create a reviewer-separated v2 reference.",
    )
    qualification_approve.add_argument("--candidate", required=True)
    qualification_approve.add_argument("--evidence", required=True)
    qualification_approve.add_argument("--qualification-id", required=True)
    qualification_approve.add_argument("--approved-by", required=True)
    qualification_approve.add_argument("--confirm-evidence-sha256", required=True)
    qualification_approve.add_argument("--output", required=True)
    qualification_approve.add_argument("--json", action="store_true")
    qualification_approve.set_defaults(func=_cmd_device_qualification_approve)
    qualification_approve_set = qualification_subparsers.add_parser(
        "approve-set",
        help="Approve an exact repeated-run candidate as a production v3 reference.",
    )
    qualification_approve_set.add_argument("--candidate", required=True)
    qualification_approve_set.add_argument(
        "--evidence", action="append", required=True
    )
    qualification_approve_set.add_argument("--qualification-id", required=True)
    qualification_approve_set.add_argument("--approved-by", required=True)
    qualification_approve_set.add_argument(
        "--confirm-evidence-set-sha256", required=True
    )
    qualification_approve_set.add_argument("--output", required=True)
    qualification_approve_set.add_argument("--json", action="store_true")
    qualification_approve_set.set_defaults(func=_cmd_device_qualification_approve_set)

    device_accept = device_subparsers.add_parser(
        "accept",
        help="Verify a completed device-update journal or FTP artifact against a field reference.",
    )
    device_accept.add_argument("--evidence", required=True, help="Journal folder or artifact ZIP.")
    device_accept.add_argument("--reference", required=True, help="Approved field reference JSON.")
    device_accept.add_argument("--output", required=True, help="Acceptance report JSON path.")
    device_accept.add_argument("--json", action="store_true", help="Print the full report.")
    device_accept.set_defaults(func=_cmd_device_accept)

    device_raw_write = device_subparsers.add_parser(
        "raw-write",
        help="Write one checksummed image to an explicit bounded QDL P/S+L sector range.",
    )
    _add_target_args(device_raw_write)
    _add_runtime_args(device_raw_write)
    device_raw_write.add_argument("--programmer", required=True)
    device_raw_write.add_argument("--image", required=True)
    device_raw_write.add_argument("--image-sha256", required=True)
    device_raw_write.add_argument("--address", required=True)
    device_raw_write.add_argument("--sector-size", type=int, choices=(512, 4096), default=4096)
    device_raw_write.add_argument("--qc-switch-confirmed", action="store_true")
    device_raw_write.add_argument("--confirm-write", default="")
    device_raw_write.add_argument("--journal-root", default="rig-firmware-results")
    device_raw_write.set_defaults(func=_cmd_device_raw_write)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        return interactive_loop()
    return run_command(
        args_list,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )


def run_command(
    argv: Sequence[str],
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.progress_callback = progress_callback
    args.cancel_callback = cancel_callback
    try:
        return int(args.func(args) or 0)
    except (RigConfigError, RigExecutionError, DeviceAcceptanceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130


def interactive_loop() -> int:
    parser = build_parser()
    print("Rig Commander interactive shell")
    print("Type help for commands, or exit to close.")
    print("")
    parser.print_help()

    while True:
        try:
            line = input("rig> ").strip()
        except EOFError:
            print("")
            return 0
        except KeyboardInterrupt:
            print("")
            return 130

        if not line:
            continue
        if line.casefold() in {"exit", "quit", "q"}:
            return 0

        try:
            argv = [_strip_wrapping_quotes(item) for item in shlex.split(line, posix=False)]
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            continue

        if not argv:
            continue
        if argv[0].casefold() in {"help", "?"}:
            _print_interactive_help(parser, argv[1:])
            continue

        try:
            run_command(argv)
        except SystemExit:
            continue


def _print_interactive_help(parser: argparse.ArgumentParser, words: Sequence[str]) -> None:
    if not words:
        parser.print_help()
        return

    try:
        run_command([*words, "--help"])
    except SystemExit:
        return


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the generated command without running it.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON results.")


def _add_device_update_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--xml", required=True, help="Firmware XML path as seen by the target rig PC.")
    parser.add_argument("--xml-sha256", default="", help="Expected XML SHA-256 from inventory metadata.")
    parser.add_argument(
        "--mode",
        choices=("download-only", "format-all-download", "provision-only"),
        default="download-only",
    )
    parser.add_argument("--qc-switch-confirmed", action="store_true")
    parser.add_argument("--mtk-preloader-confirmed", action="store_true")
    parser.add_argument(
        "--confirm-format",
        default="",
        help="For format mode, type the exact token shown by device preflight.",
    )


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
                                    "soc_vendor": port.soc_vendor,
                                    "soc_model": port.soc_model,
                                    "firmware_tool_id": port.firmware_tool_id,
                                    "download_identity": port.download_identity,
                                    "adb_serial": port.adb.serial,
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
            device = " ".join(part for part in (port.soc_vendor.upper(), port.soc_model) if part)
            adb = f" adb={port.adb.serial}" if port.adb.serial else ""
            tool = f" tool={port.firmware_tool_id}" if port.firmware_tool_id else ""
            print(
                f"  {port.id}: {port.port} baud={port.baud} device={device or '-'}"
                f"{tool}{adb} commands={commands}"
            )
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
    if args.vendor:
        inspection = inspect_firmware_package(
            args.xml,
            vendor=str(args.vendor),
            adapter_kind=str(args.adapter),
            storage_type=str(args.storage),
        )
        if args.json:
            print(json.dumps(inspection.to_mapping(), indent=2, ensure_ascii=True))
        else:
            print(inspection.render())
        return 0 if inspection.ready else 1
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
    if not args.dry_run:
        raise RigConfigError(
            "Legacy 'firmware flash' execution is disabled because it cannot enforce device identity "
            "and vendor gates. Use 'device preflight' and 'device update'."
        )
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


def _cmd_device_probe(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    phase = str(args.phase)
    if phase == "download" and not str(args.xml).strip():
        raise RigConfigError("device probe --phase download requires --xml.")
    targets = select_serial_targets(config, args.target)
    results = [
        run_device_probe(
            target,
            phase=phase,
            xml_path=str(args.xml or ""),
            expected_xml_sha256=str(args.xml_sha256 or ""),
            timeout=_timeout(args, config),
            dry_run=bool(args.dry_run),
        )
        for target in targets
    ]
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_device_system_check(args: argparse.Namespace) -> int:
    report = assess_windows_environment()
    if args.json:
        print(json.dumps(report.to_mapping(), indent=2, ensure_ascii=True))
    else:
        print(report.render())
    return 0 if report.ready else 1


def _cmd_device_power(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    action = str(args.action)
    timeout = _timeout(args, config)
    results: list[CommandResult] = []
    for target in targets:
        names = ("power_off", "power_on") if action == "cycle" else (f"power_{action}",)
        step_results: list[CommandResult] = []
        for index, name in enumerate(names):
            step_results.append(
                run_serial_command(
                    target,
                    resolve_named_command(target, name),
                    timeout=timeout,
                    dry_run=bool(args.dry_run),
                )
            )
            if not step_results[-1].ok:
                break
            if action == "cycle" and index == 0 and not args.dry_run:
                time.sleep(max(0.0, float(args.cycle_delay)))
        results.append(
            CommandResult(
                target=target.label(),
                ok=bool(step_results) and all(result.ok for result in step_results),
                returncode=next((result.returncode for result in step_results if not result.ok), 0),
                stdout="\n".join(result.stdout for result in step_results if result.stdout),
                stderr="\n".join(result.stderr for result in step_results if result.stderr),
                command=f"device-power:{action}",
                dry_run=bool(args.dry_run),
            )
        )
    _print_results(results, as_json=bool(args.json))
    return _exit_code(results)


def _cmd_device_preflight(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    reports = [
        build_device_preflight_report(
            target,
            xml_path=str(args.xml),
            mode=str(args.mode),
            expected_xml_sha256=str(args.xml_sha256 or ""),
            physical_switch_confirmed=bool(args.qc_switch_confirmed),
            preloader_exit_confirmed=bool(args.mtk_preloader_confirmed),
            format_confirmation=str(args.confirm_format or ""),
        )
        for target in targets
    ]
    if args.json:
        print(json.dumps([report.to_mapping() for report in reports], indent=2, ensure_ascii=True))
    else:
        print("\n\n".join(report.render() for report in reports))
    if not all(report.ready for report in reports):
        return 1
    if args.static_only:
        return 0
    runtime_results = [
        run_device_probe(
            target,
            phase="download",
            xml_path=str(args.xml),
            expected_xml_sha256=str(args.xml_sha256 or ""),
            timeout=_timeout(args, config),
            dry_run=bool(args.dry_run),
        )
        for target in targets
    ]
    _print_results(runtime_results, as_json=False)
    return _exit_code(runtime_results)


def _cmd_device_update(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    if len(targets) != 1:
        raise RigConfigError(
            "Device update accepts exactly one channel at a time. Submit separate jobs per CH."
        )
    result = run_device_update(
        targets[0],
        xml_path=str(args.xml),
        mode=str(args.mode),
        expected_xml_sha256=str(args.xml_sha256 or ""),
        physical_switch_confirmed=bool(args.qc_switch_confirmed),
        preloader_exit_confirmed=bool(args.mtk_preloader_confirmed),
        run_preloader_exit=bool(args.run_preloader_exit),
        format_confirmation=str(args.confirm_format or ""),
        timeout=args.timeout,
        dry_run=bool(args.dry_run),
        journal_root=str(args.journal_root or ""),
        progress_callback=args.progress_callback,
        cancel_callback=args.cancel_callback,
    )
    _print_results([result], as_json=bool(args.json))
    return 0 if result.ok else 1


def _cmd_device_qualification_prepare(args: argparse.Namespace) -> int:
    candidate = write_device_qualification_candidate(
        args.evidence,
        args.output,
        prepared_by=args.prepared_by,
        source_ticket=args.source_ticket,
    )
    if args.json:
        print(json.dumps(candidate, indent=2, ensure_ascii=True))
    else:
        print("Device qualification candidate: UNAPPROVED")
        print(f"Target: {candidate['reference_draft']['target']}")
        print(f"Evidence SHA-256: {candidate['evidence']['sha256']}")
        print(f"Candidate: {Path(args.output).expanduser().resolve()}")
    return 0


def _cmd_device_qualification_prepare_set(args: argparse.Namespace) -> int:
    candidate = write_repeated_device_qualification_candidate(
        list(args.evidence),
        args.output,
        prepared_by=args.prepared_by,
        source_ticket=args.source_ticket,
        minimum_successful_runs=args.minimum_runs,
    )
    if args.json:
        print(json.dumps(candidate, indent=2, ensure_ascii=True))
    else:
        print("Repeated device qualification candidate: UNAPPROVED")
        print(f"Target: {candidate['reference_draft']['target']}")
        print(f"Successful runs: {len(candidate['evidence_set']['runs'])}")
        print(f"Evidence-set SHA-256: {candidate['evidence_set']['sha256']}")
        print(f"Candidate: {Path(args.output).expanduser().resolve()}")
    return 0


def _cmd_device_qualification_approve(args: argparse.Namespace) -> int:
    reference = approve_device_qualification_candidate(
        args.candidate,
        args.evidence,
        args.output,
        qualification_id=args.qualification_id,
        approved_by=args.approved_by,
        confirm_evidence_sha256=args.confirm_evidence_sha256,
    )
    if args.json:
        print(json.dumps(reference, indent=2, ensure_ascii=True))
    else:
        print("Device qualification reference: APPROVED")
        print(f"Qualification: {reference['qualification_id']}")
        print(f"Target: {reference['target']}")
        print(f"Reference: {Path(args.output).expanduser().resolve()}")
    return 0


def _cmd_device_qualification_approve_set(args: argparse.Namespace) -> int:
    reference = approve_repeated_device_qualification_candidate(
        args.candidate,
        list(args.evidence),
        args.output,
        qualification_id=args.qualification_id,
        approved_by=args.approved_by,
        confirm_evidence_set_sha256=args.confirm_evidence_set_sha256,
    )
    if args.json:
        print(json.dumps(reference, indent=2, ensure_ascii=True))
    else:
        print("Repeated device qualification reference: APPROVED")
        print(f"Qualification: {reference['qualification_id']}")
        print(f"Successful runs: {len(reference['approval']['qualification_evidence'])}")
        print(f"Reference: {Path(args.output).expanduser().resolve()}")
    return 0


def _cmd_device_accept(args: argparse.Namespace) -> int:
    report = write_device_acceptance_report(
        args.evidence,
        args.reference,
        args.output,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        state = "PASS" if report["ok"] else "FAIL"
        failed = [check["id"] for check in report["checks"] if not check["ok"]]
        print(f"Device field acceptance: {state}")
        print(f"Qualification: {report['qualification_id']}")
        print(f"Target: {report['target']}")
        print(f"Report: {Path(args.output).expanduser().resolve()}")
        if failed:
            print("Failed checks: " + ", ".join(failed))
    return 0 if report["ok"] else 1


def _cmd_device_raw_write(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    targets = select_serial_targets(config, args.target)
    if len(targets) != 1:
        raise RigConfigError("Device raw-write accepts exactly one channel at a time.")
    result = run_qdl_raw_write(
        targets[0],
        programmer_path=str(args.programmer),
        image_path=str(args.image),
        image_sha256=str(args.image_sha256),
        address=str(args.address),
        confirmation=str(args.confirm_write or ""),
        physical_switch_confirmed=bool(args.qc_switch_confirmed),
        sector_size=int(args.sector_size),
        timeout=args.timeout,
        dry_run=bool(args.dry_run),
        journal_root=str(args.journal_root or ""),
        progress_callback=args.progress_callback,
        cancel_callback=args.cancel_callback,
    )
    _print_results([result], as_json=bool(args.json))
    return 0 if result.ok else 1


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
