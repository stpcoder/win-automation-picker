# 설정 파일 예시

## ae-workbench.aework.json

```json
{
  "format": "rig-ae-workbench",
  "version": 1,
  "name": "RH 4-corner",
  "sequence_recipe_path": "D:/ae/RH_4C.hseq.json",
  "sequence_package_path": "D:/ae/RH_4C.rigseq.zip",
  "sequence_tool_path": "D:/tools/TestSeqGenerator",
  "macro_project_path": "D:/ae/sk-launcher.macro.json",
  "macro_export_path": "D:/ae/sk-launcher.py",
  "macro_export_source_sha256": "...",
  "macro_buttons": [
    {
      "name": "시험 시작",
      "project_path": "D:/ae/sk-launcher.macro.json",
      "export_path": "D:/ae/sk-launcher.py",
      "source_sha256": "...",
      "notes": ""
    }
  ]
}
```

이 파일은 작업 artifact 경로와 매크로 버튼만 저장합니다. FTP 비밀번호와 slave
설정은 `rig-ftp.info`에 분리됩니다. `macro_export_source_sha256`은 Workbench가 export
시 자동으로 기록하므로 직접 수정하지 않습니다.

## rig-ftp.info

```json
{
  "master": {
    "controller_id": "MASTER-AE-01",
    "alias": "AE Control 01",
    "windows_name": "AE-MASTER-01",
    "physical_location": "Mobile AE Lab / Control Desk 1"
  },
  "ftp": {
    "host": "192.168.0.10",
    "alias": "AE Automation FTP",
    "physical_location": "Internal DC / Storage Zone A",
    "port": 21,
    "username": "macro_user",
    "password": "change-me",
    "password_env": "",
    "root_dir": "/win_automation_macros",
    "tls": false,
    "passive": true
  },
  "runtime": {
    "node_id": "rig-pc-01",
    "poll_interval_seconds": 5,
    "poll_jitter_seconds": 3,
    "min_screenshot_interval_seconds": 30,
    "work_dir": "rig-ftp-work",
    "python_executable": "python",
    "capture_on_error": true,
    "max_output_chars": 200000,
    "max_artifact_upload_bytes": 16777216,
    "max_margin_artifact_upload_bytes": 134217728,
    "max_local_run_files": 40,
    "max_staged_margin_bundles": 10,
    "max_artifact_files": 40,
    "max_result_files": 200,
    "max_log_files": 200,
    "max_archive_files": 500,
    "max_screenshot_files": 20
  },
  "variables": {
    "channel": "CH1",
    "line": "line-a"
  },
  "device_tools": [
    {
      "id": "mtk-downloader",
      "vendor": "mediatek",
      "executable": "C:/Tools/MediaTek/VendorDownload.exe",
      "arguments": ["--xml", "{xml}", "--port", "{port}", "--mode", "{mode}"],
      "execution_enabled": false,
      "cli_evidence_ref": "docs/vendor-cli/mtk-downloader.md",
      "allowed_modes": ["download-only", "format-all-download"],
      "success_exit_codes": [0],
      "success_markers": ["Download OK"],
      "failure_markers": ["FAIL", "ERROR"]
    }
  ],
  "slaves": [
    {
      "node_id": "rig-pc-04",
      "alias": "PC04",
      "host": "192.168.0.104",
      "port": 0,
      "asset_id": "PC-ASSET-004",
      "windows_name": "AE-RIG-PC04",
      "physical_location": "Mobile AE Lab / Rack 04",
      "notes": "Line A channel 4",
      "variables": {
        "channel": "CH4"
      },
      "channels": [
        {
          "channel_id": "CH11",
          "name": "SK Commander 3",
          "slot_id": "S3",
          "fixture_id": "RIG-PC04-11",
          "fixture_model": "SK-RIG-MTK",
          "fixture_serial": "AE-RIG-0011",
          "physical_location": "Mobile AE Lab / Rack 04 / Bay 3",
          "com_port": "COM7",
          "baud_rate": 115200,
          "console_identity": "VID_0403&PID_6001\\AE-RIG-0011",
          "usb_location": "Rack04 Hub-A / Port 3",
          "firmware_port": "COM7",
          "soc_vendor": "mediatek",
          "soc_model": "Genio 720",
          "firmware_tool_id": "mtk-downloader",
          "download_identity": "MediaTek PreLoader USB VCOM",
          "adb_executable": "adb.exe",
          "adb_serial": "MTK-CH11",
          "adb_enabled": true,
          "adb_required_after_update": true,
          "power_on_command": "POWER ON 11",
          "power_off_command": "POWER OFF 11",
          "preloader_exit_command": "exit",
          "preloader_exit_count": 2,
          "preloader_exit_interval_ms": 150,
          "preloader_ready_marker": "LK2]",
          "preloader_ready_timeout_ms": 5000,
          "download_wait_seconds": 120,
          "download_poll_interval_seconds": 2,
          "binary_name": "download.xml",
          "binary_version": "MTK25D_20260711",
          "binary_source_path": "D:/binary/MTK25D_20260711",
          "binary_updated_at": "2026-07-11T12:00:00+00:00",
          "dram_part": "LPDDR5X-A",
          "lot_id": "LOT01",
          "sample_id": "SAMPLE03",
          "current_test": "Row Hammer",
          "sequence_name": "RH_4CORNER",
          "notes": ""
        },
        {
          "channel_id": "",
          "name": "Main",
          "slot_id": "",
          "soc_vendor": "qualcomm",
          "soc_model": "SM8850"
        }
      ]
    }
  ],
  "run_profiles": [
    {
      "enabled": true,
      "alias": "PC04 / CH11",
      "target": "rig-pc-04",
      "package": "row-hammer.rigseq.zip",
      "variables": {
        "channel": "CH11",
        "slot_id": "S3",
        "sequence_backend": "serial",
        "com_port": "COM7",
        "baud_rate": "115200",
        "sequence_name": "RH_4C_SM8850_V04"
      }
    }
  ]
}
```

## 주요 필드

| 필드 | 설명 |
| --- | --- |
| `master.*` | 명령을 만든 Master PC ID·별명·Windows 이름·실제 위치 |
| `ftp.host` | FTP 서버 주소 |
| `ftp.alias`, `ftp.physical_location` | 사람이 구별하는 FTP 별명과 실제 위치 |
| `ftp.root_dir` | macro spool root |
| `runtime.node_id` | 현재 PC 식별자 |
| `runtime.poll_interval_seconds` | slave polling 주기 |
| `runtime.min_screenshot_interval_seconds` | screenshot 요청 최소 간격 |
| `runtime.python_executable` | 일반 Python package 전용 실행기. export workflow에는 사용하지 않음 |
| `runtime.max_run_log_bytes` | CH별 직접 COM console 파일 상한. 기본 8 MB |
| `runtime.max_artifact_upload_bytes` | 실행 종료 시 FTP 증거 ZIP 상한. 기본 16 MB |
| `runtime.max_margin_artifact_upload_bytes` | raw JSONL·CSV를 포함한 DRAM margin 결과 ZIP 상한. 기본 128 MB, 허용 1 MB~1 GB |
| `runtime.max_local_run_files` | Slave의 직접 COM 실행 폴더 보관 개수. 기본 40 |
| `runtime.max_staged_margin_bundles` | Slave에 checksum별로 보관하는 DRAM margin 실행 번들 수. 기본 10, 허용 1~50 |
| `runtime.max_artifact_files` | PC별 FTP 증거 ZIP 보관 개수. 기본 40, `0`이면 증거 ZIP 업로드 끔 |
| `runtime.max_*` | 파일 보관 개수 |
| `variables` | job 실행 시 기본 변수 |
| `device_tools` | 실제 CLI와 결과 규칙을 확인한 외부 MTK/QC Downloader |
| `device_tools[].adapter_kind` | `qualcomm-qdl`, `mediatek-genio`, 또는 현장 CLI용 `generic` |
| `slaves` | master에서 보는 slave roster |
| `slaves[].asset_id`, `windows_name`, `physical_location` | 실장기 연결 PC의 물리 자산·OS 이름·랙 위치 |
| `slaves[].channels` | 물리 실장기 ID/Serial/위치, 자유 CH, COM/baud/ADB/전원, SoC, Binary, 자재와 SEQ |
| `channels[].console_identity`, `usb_location` | COM 번호 변경·오연결을 검출하는 HWID와 Hub/케이블 위치 |
| `channels[].download_serial` | QDL이 여러 EDL 장치 중 정확한 한 장치를 고르는 USB serial |
| `channels[].storage_type`, `storage_slot` | `ufs`/`emmc` 등 storage 종류와 QDL slot |
| `channels[].package_selector` | QDL contents flavor 또는 flashmap layout. 예: `ufs,safe_rtos`, `layout1/ufs` |
| `channels[].board_control_serial` | Genio가 정확한 FTDI board를 reset/download하는 serial |
| `channels[].bootstrap_*` | MTK Download Agent, SRAM 주소, ISA mode, DAA signature/auth |
| `channels[].gpio_*` | 기본값과 다를 때 쓰는 MTK power/reset/download GPIO |
| `channels[].preloader_exit_*` | Generic MTK 진입 명령 반복 횟수·간격과 LK marker 대기 |
| `channels[].download_wait_seconds`, `download_poll_interval_seconds` | 물리 switch/serial 전환 뒤 USB Download 장치를 재탐색하는 제한 시간과 간격 |
| `channels[].download_reentry_command` | Generic 단계형 Downloader에서 포맷 후 같은 실장기를 download mode로 돌리는 검증 명령 |
| `run_profiles` | Master의 PC별 매크로 실행표 |

`run_profiles[].variables.sequence_backend`은 `serial`(화면의 `직접 COM`) 또는
`sk_commander`입니다. 직접 COM 행에는 `com_port`와 `baud_rate`가 필요합니다.

`저장`은 현재 실행표도 `run_profiles`에 저장합니다. `Slave 설정 내보내기`로 생성되는 slave용 파일에서는 다른 PC의 실행표를 제거합니다. 실행표 변수는 일반 문자열로 저장되므로 비밀번호나 token은 저장하지 말고 실행 직전에 입력하십시오.

## rig-commander.config.json

이 파일은 직접 편집하는 기본 경로가 아닙니다. `Slave 설정 내보내기`가 각 PC의 CH와
`device_tools`를 기준으로 자동 생성합니다.

```json
{
  "hosts": [
    {
      "id": "rig-pc-04",
      "address": "localhost",
      "transport": "local",
      "firmware_tools": [
        {
          "id": "mtk-genio",
          "vendor": "mediatek",
          "adapter_kind": "mediatek-genio",
          "executable": "C:/Tools/Genio/genio-flash.exe",
          "execution_enabled": false,
          "allowed_modes": ["download-only", "format-all-download"],
          "storage_types": ["ufs", "emmc"]
        }
      ],
      "ports": [
        {
          "id": "CH11",
          "port": "COM7",
          "baud": 115200,
          "fixture_id": "RIG-PC04-11",
          "fixture_model": "SK-RIG-MTK",
          "fixture_serial": "AE-RIG-0011",
          "physical_location": "Mobile AE Lab / Rack 04 / Bay 3",
          "console_identity": "VID_0403&PID_6001\\AE-RIG-0011",
          "usb_location": "Rack04 Hub-A / Port 3",
          "soc_vendor": "mediatek",
          "soc_model": "MTK25D",
          "firmware_tool_id": "mtk-genio",
          "download_identity": "VID_0E8D&PID_0003",
          "storage_type": "ufs",
          "board_control_serial": "FTDI-CH11",
          "bootstrap_path": "D:/binary/Genio720/lk.bin",
          "bootstrap_address": "0x2001000",
          "bootstrap_mode": "aarch64",
          "preloader_exit_count": 2,
          "preloader_exit_interval_ms": 150,
          "preloader_ready_marker": "LK2]",
          "preloader_ready_timeout_ms": 5000,
          "download_wait_seconds": 120,
          "download_poll_interval_seconds": 2,
          "adb": {"enabled": true, "serial": "MTK-CH11"},
          "commands": {
            "preloader_exit": "exit",
            "download_reentry": "DOWNLOAD REENTER"
          }
        }
      ]
    }
  ]
}
```

`execution_enabled`는 실제 도구 버전의 CLI, 성공/실패 문구를 확인한 뒤 GUI에서만 켭니다.

CH 이름은 `CH1` 형식을 강제하지 않습니다. `CH9`, `CH10`, `PC04-RIG2`를 사용할 수 있고 CH가 없는 단일 창은 `channel_id`를 비우고 `name`을 `Main`처럼 입력합니다. 각 항목에는 `channel_id`, `slot_id`, `name` 중 하나가 반드시 있어야 합니다.

`fixture_id`와 `fixture_serial`은 물리 실장기를 따라가는 안정 식별자입니다. `com_port`는
Windows USB 재열거로 달라질 수 있으므로 `console_identity`와 `usb_location`을 함께
저장합니다. 자세한 변경 절차는 [PC · 실장기 · COM 연결 구조](../fixture-topology.md)를
따릅니다.

Heartbeat와 잘못된 동적 CH 누적을 제한하기 위해 Slave PC 한 대에는 최대 64개 CH/slot 항목을 등록할 수 있습니다.

## password_env 사용

비밀번호를 파일에 저장하기 싫으면 `password_env`에 환경 변수 이름을 넣습니다.

```json
{
  "ftp": {
    "password": "",
    "password_env": "RIG_FTP_PASSWORD"
  }
}
```

Windows에서는 실행 전 환경 변수를 설정합니다.

```powershell
$env:RIG_FTP_PASSWORD = "actual-password"
```

`password_env`가 설정된 config를 GUI에서 다시 저장해도 해석된 실제 비밀번호는 파일에 기록하지 않습니다.
