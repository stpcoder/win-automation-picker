from __future__ import annotations

import argparse
from dataclasses import replace
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Sequence

from .ftp_spool import (
    FtpSpoolConfig,
    FtpSpoolError,
    SpoolJob,
    agent_instance_lock,
    backend_from_config,
    cleanup_node_files,
    clear_stop,
    deploy_package,
    initialize_spool,
    list_packages,
    list_results,
    list_screenshots,
    list_status,
    request_stop,
    run_slave_once,
    slave_loop,
    submit_job,
    write_example_spool_config,
)


DEFAULT_CONFIG = "rig-ftp.info"
LEGACY_CONFIG = "rig-ftp.config.json"
DEFAULT_CONFIG_FILES = (DEFAULT_CONFIG, LEGACY_CONFIG)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rig-ftp",
        description="Distribute macro and rig jobs through an FTP-backed master/slave spool.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to FTP spool config JSON/info. Default search: {', '.join(DEFAULT_CONFIG_FILES)}",
    )
    parser.add_argument(
        "--local-root",
        default="",
        help="Use a local folder as the spool backend instead of FTP. Useful for tests and dry lab trials.",
    )
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    init_config = subparsers.add_parser("init-config", help="Write an example FTP spool config.")
    init_config.add_argument("-o", "--output", default=DEFAULT_CONFIG, help="Output config path.")
    init_config.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    init_config.set_defaults(func=_cmd_init_config)

    init_server = subparsers.add_parser("init-server", help="Create the FTP spool folder structure.")
    init_server.add_argument("--node", action="append", default=[], help="Pre-create folders for this slave node.")
    init_server.set_defaults(func=_cmd_init_server)

    deploy = subparsers.add_parser("deploy", help="Upload an exported macro or helper script to packages/.")
    deploy.add_argument("--file", required=True, help="Local file to upload.")
    deploy.add_argument("--name", default="", help="Package name on the spool. Defaults to file name.")
    deploy.add_argument("--title", default="", help="Human-readable package title.")
    deploy.add_argument("--notes", default="", help="Package notes shown in GUI package lists.")
    deploy.set_defaults(func=_cmd_deploy)

    packages = subparsers.add_parser("packages", help="List uploaded macro packages.")
    packages.add_argument("--json", action="store_true", help="Print JSON.")
    packages.set_defaults(func=_cmd_packages)

    submit_python = subparsers.add_parser("submit-python", help="Submit a package-backed Python job.")
    _add_submit_args(submit_python)
    submit_python.add_argument("--package", required=True, help="Package file under packages/, e.g. smoke.py.")
    submit_python.add_argument("--arg", action="append", default=[], help="Argument passed to the Python script.")
    submit_python.set_defaults(func=_cmd_submit_python)

    submit_workflow = subparsers.add_parser(
        "submit-workflow",
        help="Submit an exported workflow for the slave's embedded runner.",
    )
    _add_submit_args(submit_workflow)
    submit_workflow.add_argument("--package", required=True, help="Exported workflow under packages/.")
    submit_workflow.set_defaults(func=_cmd_submit_workflow)

    submit_monitor = subparsers.add_parser(
        "submit-monitor",
        help="Evaluate only monitor blocks from an exported workflow.",
    )
    _add_submit_args(submit_monitor)
    submit_monitor.add_argument("--package", required=True, help="Exported workflow under packages/.")
    submit_monitor.set_defaults(func=_cmd_submit_monitor)

    submit_shell = subparsers.add_parser("submit-shell", help="Submit a shell command job.")
    _add_submit_args(submit_shell)
    command_group = submit_shell.add_mutually_exclusive_group(required=True)
    command_group.add_argument("--command", help="Shell command line.")
    command_group.add_argument("--args", nargs=argparse.REMAINDER, help="Executable and arguments without shell parsing.")
    submit_shell.add_argument("--cwd", default="", help="Working directory on the slave.")
    submit_shell.set_defaults(func=_cmd_submit_shell)

    submit_rig = subparsers.add_parser("submit-rig", help="Submit a rig-commander argument list.")
    _add_submit_args(submit_rig)
    submit_rig.add_argument("rig_args", nargs=argparse.REMAINDER, help="Arguments after -- are passed to rig-commander.")
    submit_rig.set_defaults(func=_cmd_submit_rig)

    screenshot = subparsers.add_parser("screenshot", help="Request a full-screen screenshot from slave nodes.")
    screenshot.add_argument("--target", action="append", default=[], help="Slave node id. Default: all.")
    screenshot.add_argument("--job-id", default="", help="Optional explicit job id.")
    screenshot.add_argument("--label", default="manual", help="Screenshot label used in the output file name.")
    screenshot.set_defaults(func=_cmd_screenshot)

    stop = subparsers.add_parser("stop", help="Request emergency stop on slave nodes.")
    stop.add_argument("--target", action="append", default=[], help="Slave node id. Default: all.")
    stop.add_argument("--job-id", default="", help="Optional job id to stop. Empty stops the current/next job.")
    stop.add_argument("--reason", default="", help="Reason recorded in the stop file.")
    stop.set_defaults(func=_cmd_stop)

    clear_stop_parser = subparsers.add_parser("clear-stop", help="Clear stop signal for slave nodes.")
    clear_stop_parser.add_argument("--target", action="append", default=[], help="Slave node id. Default: all.")
    clear_stop_parser.set_defaults(func=_cmd_clear_stop)

    slave = subparsers.add_parser("slave", help="Run slave polling loop.")
    slave.add_argument("--node-id", default="", help="Override config runtime.node_id.")
    slave.add_argument("--once", action="store_true", help="Poll and execute once, then exit.")
    slave.add_argument("--count", type=int, default=0, help="Polling rounds. 0 means forever unless --once.")
    slave.set_defaults(func=_cmd_slave)

    status = subparsers.add_parser("status", help="Print slave status rows.")
    status.add_argument("--json", action="store_true", help="Print JSON.")
    status.set_defaults(func=_cmd_status)

    results = subparsers.add_parser("results", help="Print result rows for one slave node.")
    results.add_argument("--node-id", required=True, help="Slave node id.")
    results.add_argument("--json", action="store_true", help="Print JSON.")
    results.set_defaults(func=_cmd_results)

    screenshots = subparsers.add_parser("screenshots", help="List screenshot files for one slave node.")
    screenshots.add_argument("--node-id", required=True, help="Slave node id.")
    screenshots.set_defaults(func=_cmd_screenshots)

    cleanup = subparsers.add_parser("cleanup", help="Apply retention cleanup for one slave node.")
    cleanup.add_argument("--node-id", required=True, help="Slave node id.")
    cleanup.set_defaults(func=_cmd_cleanup)

    monitor = subparsers.add_parser("monitor", help="Repeat status display.")
    monitor.add_argument("--interval", type=float, default=5.0, help="Seconds between status polls.")
    monitor.add_argument("--count", type=int, default=0, help="Number of polls. 0 means until Ctrl+C.")
    monitor.set_defaults(func=_cmd_monitor)

    return parser


def main(argv: Sequence[str] | None = None, *, gui_on_empty: bool = True) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args and gui_on_empty:
        return _run_gui()
    parser = build_parser()
    if not raw_args:
        parser.print_help()
        return 0
    args = parser.parse_args(raw_args)
    try:
        return int(args.func(args) or 0)
    except FtpSpoolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130


def _add_submit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Slave node id. Repeat for multiple nodes. Use all to broadcast.",
    )
    parser.add_argument("--job-id", default="", help="Optional explicit job id.")
    parser.add_argument("--timeout", type=float, default=0.0, help="Job timeout seconds. 0 means no timeout.")
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        help="Job variable KEY=VALUE. Use [KEY] or {KEY} placeholders in commands and args.",
    )


def _cmd_init_config(args: argparse.Namespace) -> int:
    path = write_example_spool_config(args.output, force=bool(args.force))
    print(f"Wrote {path}")
    return 0


def _cmd_init_server(args: argparse.Namespace) -> int:
    config, backend = _load(args)
    initialize_spool(backend, nodes=args.node)
    print(f"Initialized spool root {config.root_dir}")
    return 0


def _cmd_deploy(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    remote_path = deploy_package(backend, args.file, name=args.name, title=args.title, notes=args.notes)
    print(remote_path)
    return 0


def _cmd_packages(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    packages = list_packages(backend)
    if args.json:
        print(json.dumps([package.to_mapping() for package in packages], indent=2, ensure_ascii=True))
        return 0
    if not packages:
        print("No packages have been uploaded.")
        return 0
    for package in packages:
        title = f" | {package.title}" if package.title else ""
        print(f"{package.name}{title}")
        if package.notes:
            print(f"  {package.notes}")
    return 0


def _cmd_submit_python(args: argparse.Namespace) -> int:
    job = _job(
        args,
        kind="python",
        payload={
            "package": args.package,
            "args": list(args.arg),
            "timeout_seconds": float(args.timeout or 0.0),
        },
    )
    return _submit(args, job)


def _cmd_submit_workflow(args: argparse.Namespace) -> int:
    job = _job(
        args,
        kind="workflow",
        payload={
            "package": args.package,
            "timeout_seconds": float(args.timeout or 0.0),
        },
    )
    return _submit(args, job)


def _cmd_submit_monitor(args: argparse.Namespace) -> int:
    job = _job(
        args,
        kind="monitor",
        payload={
            "package": args.package,
            "timeout_seconds": float(args.timeout or 0.0),
        },
    )
    return _submit(args, job)


def _cmd_submit_shell(args: argparse.Namespace) -> int:
    payload = {
        "cwd": args.cwd,
        "timeout_seconds": float(args.timeout or 0.0),
    }
    if args.args:
        shell_args = list(args.args)
        if shell_args and shell_args[0] == "--":
            shell_args = shell_args[1:]
        payload["args"] = shell_args
    else:
        payload["command"] = str(args.command)
    return _submit(args, _job(args, kind="shell", payload=payload))


def _cmd_submit_rig(args: argparse.Namespace) -> int:
    rig_args = list(args.rig_args)
    if rig_args and rig_args[0] == "--":
        rig_args = rig_args[1:]
    if not rig_args:
        raise FtpSpoolError("submit-rig requires rig-commander args after --.")
    return _submit(
        args,
        _job(
            args,
            kind="rig",
            payload={
                "args": rig_args,
                "timeout_seconds": float(args.timeout or 0.0),
            },
        ),
    )


def _cmd_screenshot(args: argparse.Namespace) -> int:
    job = SpoolJob.create(
        kind="screenshot",
        payload={"label": args.label},
        job_id=args.job_id,
    )
    return _submit(args, job)


def _cmd_stop(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    targets = args.target or ["all"]
    for target in targets:
        path = request_stop(backend, target, job_id=args.job_id, reason=args.reason)
        print(path)
    return 0


def _cmd_clear_stop(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    targets = args.target or ["all"]
    for target in targets:
        clear_stop(backend, target)
        print(f"cleared control/{target}/stop.json")
    return 0


def _cmd_slave(args: argparse.Namespace) -> int:
    config, backend = _load(args)
    if args.once:
        node = args.node_id or config.node_id
        with agent_instance_lock(config, node):
            results = run_slave_once(backend, config, node_id=node)
        for result in results:
            state = "OK" if result.ok else "FAIL"
            print(f"[{state}] {result.node_id} {result.job_id} {result.kind}")
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        return 0 if all(result.ok for result in results) else 1
    slave_loop(backend, config, node_id=args.node_id or None, count=max(0, int(args.count or 0)))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    rows = list_status(backend)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=True))
        return 0
    _print_status(rows)
    return 0


def _cmd_results(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    rows = list_results(backend, args.node_id)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=True))
        return 0
    for row in rows:
        state = "OK" if row.get("ok") else "FAIL"
        print(f"[{state}] {row.get('node_id')} {row.get('job_id')} {row.get('kind')} rc={row.get('returncode')}")
    return 0


def _cmd_screenshots(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    for path in list_screenshots(backend, args.node_id):
        print(path)
    return 0


def _cmd_cleanup(args: argparse.Namespace) -> int:
    config, backend = _load(args)
    cleanup_node_files(backend, args.node_id, config)
    print(f"Cleaned retained files for {args.node_id}")
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    interval = max(0.2, float(args.interval or 5.0))
    count = max(0, int(args.count or 0))
    index = 0
    while True:
        index += 1
        print(f"# poll {index} {time.strftime('%Y-%m-%d %H:%M:%S')}")
        _print_status(list_status(backend))
        if count and index >= count:
            return 0
        time.sleep(interval)


def _submit(args: argparse.Namespace, job: SpoolJob) -> int:
    config, backend = _load(args)
    if not job.origin:
        job = replace(
            job,
            origin={
                key: value
                for key, value in {
                    "controller_id": config.master.controller_id,
                    "alias": config.master.alias,
                    "windows_name": config.master.windows_name,
                    "physical_location": config.master.physical_location,
                }.items()
                if value
            },
        )
    paths = submit_job(backend, job, args.target or ["all"])
    for path in paths:
        print(path)
    return 0


def _job(args: argparse.Namespace, *, kind: str, payload: dict[str, object]) -> SpoolJob:
    return SpoolJob.create(
        kind=kind,
        payload=payload,
        variables=_parse_vars(args.var),
        job_id=args.job_id,
    )


def _parse_vars(items: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise FtpSpoolError(f"Variable must be KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise FtpSpoolError(f"Variable key is empty: {item}")
        result[key] = value
    return result


def _load(args: argparse.Namespace):
    config = FtpSpoolConfig.load(_resolve_config_path(args.config))
    local_root = Path(args.local_root) if args.local_root else None
    return config, backend_from_config(config, local_root=local_root)


def _resolve_config_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.exists() or candidate.name not in DEFAULT_CONFIG_FILES:
        return candidate
    search_dirs = [
        Path.cwd(),
        Path(sys.argv[0]).resolve().parent,
        Path(sys.executable).resolve().parent,
    ]
    for directory in search_dirs:
        for name in DEFAULT_CONFIG_FILES:
            resolved = directory / name
            if resolved.exists():
                return resolved
    return candidate


def _print_status(rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        print("No slave status has been published.")
        return
    for row in rows:
        print(
            f"{row.get('node_id', '-'):20} "
            f"{row.get('state', '-'):10} "
            f"{row.get('current_job', '-') or '-':28} "
            f"{row.get('updated_at', '-')} "
            f"{row.get('message', '')}"
        )


def split_command_line(line: str) -> list[str]:
    return shlex.split(line, posix=False)


def _run_gui() -> int:
    from .ftp_app import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
