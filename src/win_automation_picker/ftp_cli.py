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
from .windows_compat import configure_windows_console_utf8


DEFAULT_CONFIG = "fixture-connection.info"
LEGACY_CONFIG = "rig-ftp.info"
LEGACY_JSON_CONFIG = "rig-ftp.config.json"
DEFAULT_CONFIG_FILES = (DEFAULT_CONFIG, LEGACY_CONFIG, LEGACY_JSON_CONFIG)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fixture-communication",
        description="통신 서버를 통해 여러 실장기 PC의 테스트 실행과 상태 확인을 관리합니다.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"통신 설정 파일 경로입니다. 기본값: {DEFAULT_CONFIG}",
    )
    parser.add_argument(
        "--local-root",
        default="",
        help="통신 서버 대신 로컬 폴더를 사용합니다. 기능 점검용입니다.",
    )
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    init_config = subparsers.add_parser(
        "init-config", help="통신 설정 예시 파일을 만듭니다."
    )
    init_config.add_argument(
        "-o", "--output", default=DEFAULT_CONFIG, help="저장 경로입니다."
    )
    init_config.add_argument(
        "--force", action="store_true", help="기존 파일을 덮어씁니다."
    )
    init_config.set_defaults(func=_cmd_init_config)

    init_server = subparsers.add_parser(
        "init-server", help="통신 서버의 전용 폴더를 만듭니다."
    )
    init_server.add_argument(
        "--fixture-pc",
        dest="node",
        action="append",
        default=[],
        help="미리 준비할 실장기 PC 내부 식별값입니다.",
    )
    init_server.set_defaults(func=_cmd_init_server)

    deploy = subparsers.add_parser(
        "deploy", help="자동 실행 순서 또는 SEQ를 통신 서버에 올립니다."
    )
    deploy.add_argument("--file", required=True, help="올릴 파일의 로컬 경로입니다.")
    deploy.add_argument("--name", default="", help="통신 서버에서 사용할 파일명입니다.")
    deploy.add_argument("--title", default="", help="목록에 표시할 제목입니다.")
    deploy.add_argument("--notes", default="", help="목록에 표시할 설명입니다.")
    deploy.set_defaults(func=_cmd_deploy)

    packages = subparsers.add_parser(
        "packages", help="통신 서버에 올린 실행 파일을 표시합니다."
    )
    packages.add_argument(
        "--json", action="store_true", help="JSON 형식으로 표시합니다."
    )
    packages.set_defaults(func=_cmd_packages)

    submit_python = subparsers.add_parser(
        "submit-python", help="Python 실행 파일을 전송합니다."
    )
    _add_submit_args(submit_python)
    submit_python.add_argument(
        "--package", required=True, help="통신 서버에 올린 Python 파일명입니다."
    )
    submit_python.add_argument(
        "--arg", action="append", default=[], help="Python 파일에 전달할 인자입니다."
    )
    submit_python.set_defaults(func=_cmd_submit_python)

    submit_workflow = subparsers.add_parser(
        "submit-workflow",
        help="자동 실행 순서를 실장기 PC에 전송합니다.",
    )
    _add_submit_args(submit_workflow)
    submit_workflow.add_argument(
        "--package", required=True, help="통신 서버에 올린 자동 실행 순서 파일명입니다."
    )
    submit_workflow.set_defaults(func=_cmd_submit_workflow)

    submit_monitor = subparsers.add_parser(
        "submit-monitor",
        help="자동 실행 순서의 상태 확인 항목만 한 번 검사합니다.",
    )
    _add_submit_args(submit_monitor)
    submit_monitor.add_argument(
        "--package", required=True, help="통신 서버에 올린 자동 실행 순서 파일명입니다."
    )
    submit_monitor.set_defaults(func=_cmd_submit_monitor)

    submit_margin = subparsers.add_parser(
        "submit-margin",
        help="검사 완료된 DRAM 마진 테스트 실행 파일을 실장기 PC 한 대에 전송합니다.",
    )
    _add_submit_args(submit_margin)
    submit_margin.add_argument(
        "--package",
        required=True,
        help="통신 서버에 올린 DRAM 마진 테스트 파일명입니다.",
    )
    submit_margin.add_argument("--probe-timeout", type=float, default=120.0)
    submit_margin.add_argument("--sweep-timeout", type=float, default=3600.0)
    submit_margin.set_defaults(func=_cmd_submit_margin)

    submit_shell = subparsers.add_parser(
        "submit-shell", help="고급 사용자용 명령을 전송합니다."
    )
    _add_submit_args(submit_shell)
    command_group = submit_shell.add_mutually_exclusive_group(required=True)
    command_group.add_argument("--command", help="실행할 명령 한 줄입니다.")
    command_group.add_argument(
        "--args", nargs=argparse.REMAINDER, help="실행 파일과 인자 목록입니다."
    )
    submit_shell.add_argument("--cwd", default="", help="실장기 PC의 작업 폴더입니다.")
    submit_shell.set_defaults(func=_cmd_submit_shell)

    submit_device = subparsers.add_parser(
        "submit-device", help="실장기 직접 제어 명령을 전송합니다."
    )
    _add_submit_args(submit_device)
    submit_device.add_argument(
        "device_args",
        nargs=argparse.REMAINDER,
        help="-- 뒤의 인자를 실장기 직접 제어 프로그램에 전달합니다.",
    )
    submit_device.set_defaults(func=_cmd_submit_device)

    screenshot = subparsers.add_parser(
        "screenshot", help="실장기 PC의 전체 화면을 요청합니다."
    )
    screenshot.add_argument(
        "--target",
        action="append",
        default=[],
        help="실장기 PC 내부 식별값입니다. 기본값은 전체입니다.",
    )
    screenshot.add_argument(
        "--job-id", default="", help="필요할 때 지정하는 작업 ID입니다."
    )
    screenshot.add_argument(
        "--label", default="manual", help="화면 파일명에 넣을 구분 이름입니다."
    )
    screenshot.set_defaults(func=_cmd_screenshot)

    stop = subparsers.add_parser("stop", help="실장기 PC에 긴급 중단을 요청합니다.")
    stop.add_argument(
        "--target",
        action="append",
        default=[],
        help="실장기 PC 내부 식별값입니다. 기본값은 전체입니다.",
    )
    stop.add_argument(
        "--job-id",
        default="",
        help="중단할 작업 ID입니다. 비우면 현재 또는 다음 작업을 중단합니다.",
    )
    stop.add_argument("--reason", default="", help="중단 이유입니다.")
    stop.set_defaults(func=_cmd_stop)

    clear_stop_parser = subparsers.add_parser(
        "clear-stop", help="긴급 중단 신호를 해제합니다."
    )
    clear_stop_parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="실장기 PC 내부 식별값입니다. 기본값은 전체입니다.",
    )
    clear_stop_parser.set_defaults(func=_cmd_clear_stop)

    fixture_pc = subparsers.add_parser(
        "fixture-pc", help="실장기 PC에서 새 테스트 요청을 확인합니다."
    )
    fixture_pc.add_argument(
        "--fixture-pc",
        dest="node_id",
        default="",
        help="실장기 PC 내부 식별값을 지정합니다.",
    )
    fixture_pc.add_argument(
        "--once", action="store_true", help="한 번 확인한 뒤 종료합니다."
    )
    fixture_pc.add_argument(
        "--count", type=int, default=0, help="확인 횟수입니다. 0은 계속 확인합니다."
    )
    fixture_pc.set_defaults(func=_cmd_fixture_pc)

    status = subparsers.add_parser("status", help="실장기 PC 상태를 표시합니다.")
    status.add_argument("--json", action="store_true", help="JSON 형식으로 표시합니다.")
    status.set_defaults(func=_cmd_status)

    results = subparsers.add_parser(
        "results", help="실장기 PC 한 대의 결과를 표시합니다."
    )
    results.add_argument(
        "--fixture-pc",
        dest="node_id",
        required=True,
        help="실장기 PC 내부 식별값입니다.",
    )
    results.add_argument(
        "--json", action="store_true", help="JSON 형식으로 표시합니다."
    )
    results.set_defaults(func=_cmd_results)

    screenshots = subparsers.add_parser(
        "screenshots", help="실장기 PC의 화면 파일을 표시합니다."
    )
    screenshots.add_argument(
        "--fixture-pc",
        dest="node_id",
        required=True,
        help="실장기 PC 내부 식별값입니다.",
    )
    screenshots.set_defaults(func=_cmd_screenshots)

    cleanup = subparsers.add_parser(
        "cleanup", help="실장기 PC의 오래된 통신 파일을 정리합니다."
    )
    cleanup.add_argument(
        "--fixture-pc",
        dest="node_id",
        required=True,
        help="실장기 PC 내부 식별값입니다.",
    )
    cleanup.set_defaults(func=_cmd_cleanup)

    monitor = subparsers.add_parser(
        "monitor", help="실장기 PC 상태를 반복해서 표시합니다."
    )
    monitor.add_argument(
        "--interval", type=float, default=5.0, help="상태 확인 간격(초)입니다."
    )
    monitor.add_argument(
        "--count",
        type=int,
        default=0,
        help="확인 횟수입니다. 0은 Ctrl+C 전까지 계속합니다.",
    )
    monitor.set_defaults(func=_cmd_monitor)

    return parser


def main(argv: Sequence[str] | None = None, *, gui_on_empty: bool = True) -> int:
    configure_windows_console_utf8()
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
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("중지됨", file=sys.stderr)
        return 130


def _add_submit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="실장기 PC 내부 식별값입니다. 여러 대면 반복하고, 전체는 all을 사용합니다.",
    )
    parser.add_argument(
        "--job-id", default="", help="필요할 때 지정하는 작업 ID입니다."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="제한 시간(초)입니다. 0은 제한하지 않습니다.",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        help="실장기별 입력값 KEY=VALUE입니다. 명령에서는 [KEY] 또는 {KEY}로 사용합니다.",
    )


def _cmd_init_config(args: argparse.Namespace) -> int:
    path = write_example_spool_config(args.output, force=bool(args.force))
    print(f"통신 설정 파일 저장: {path}")
    return 0


def _cmd_init_server(args: argparse.Namespace) -> int:
    config, backend = _load(args)
    initialize_spool(backend, nodes=args.node)
    print(f"통신 서버 폴더 준비 완료: {config.root_dir}")
    return 0


def _cmd_deploy(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    remote_path = deploy_package(
        backend, args.file, name=args.name, title=args.title, notes=args.notes
    )
    print(remote_path)
    return 0


def _cmd_packages(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    packages = list_packages(backend)
    if args.json:
        print(
            json.dumps(
                [package.to_mapping() for package in packages],
                indent=2,
                ensure_ascii=True,
            )
        )
        return 0
    if not packages:
        print("등록된 SEQ 또는 자동 실행 순서가 없습니다.")
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


def _cmd_submit_margin(args: argparse.Namespace) -> int:
    if not args.target or any(
        str(target).casefold() == "all" for target in args.target
    ):
        raise FtpSpoolError(
            "DRAM margin requires exactly one explicit fixture PC target."
        )
    if len(args.target) != 1:
        raise FtpSpoolError(
            "DRAM 마진 작업 한 건에는 실장기 PC 한 대만 선택할 수 있습니다."
        )
    job = _job(
        args,
        kind="dram_margin",
        payload={
            "package": args.package,
            "timeout_seconds": float(args.timeout or 0.0),
            "probe_timeout_seconds": float(args.probe_timeout),
            "sweep_timeout_seconds": float(args.sweep_timeout),
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


def _cmd_submit_device(args: argparse.Namespace) -> int:
    device_args = list(args.device_args)
    if device_args and device_args[0] == "--":
        device_args = device_args[1:]
    if not device_args:
        raise FtpSpoolError("submit-device 뒤에 --와 실장기 제어 인자를 입력하세요.")
    return _submit(
        args,
        _job(
            args,
            kind="rig",
            payload={
                "args": device_args,
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
        print(f"긴급 중단 신호 해제: {target}")
    return 0


def _cmd_fixture_pc(args: argparse.Namespace) -> int:
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
    slave_loop(
        backend,
        config,
        node_id=args.node_id or None,
        count=max(0, int(args.count or 0)),
    )
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
        print(
            f"[{state}] {row.get('node_id')} {row.get('job_id')} {row.get('kind')} rc={row.get('returncode')}"
        )
    return 0


def _cmd_screenshots(args: argparse.Namespace) -> int:
    _config, backend = _load(args)
    for path in list_screenshots(backend, args.node_id):
        print(path)
    return 0


def _cmd_cleanup(args: argparse.Namespace) -> int:
    config, backend = _load(args)
    cleanup_node_files(backend, args.node_id, config)
    print(f"오래된 통신 파일 정리 완료: {args.node_id}")
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


def _job(
    args: argparse.Namespace, *, kind: str, payload: dict[str, object]
) -> SpoolJob:
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
            raise FtpSpoolError(f"입력값은 이름=값 형식으로 적어야 합니다: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise FtpSpoolError(f"입력값 이름이 비어 있습니다: {item}")
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
        print("아직 실장기 PC 상태가 올라오지 않았습니다.")
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
