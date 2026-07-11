# 설정 파일 예시

## rig-ftp.info

```json
{
  "ftp": {
    "host": "192.168.0.10",
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
    "max_result_files": 200,
    "max_log_files": 200,
    "max_archive_files": 500,
    "max_screenshot_files": 20
  },
  "variables": {
    "channel": "CH1",
    "line": "line-a"
  },
  "slaves": [
    {
      "node_id": "rig-pc-04",
      "alias": "PC04",
      "host": "192.168.0.104",
      "port": 0,
      "notes": "Line A channel 4",
      "variables": {
        "channel": "CH4"
      },
      "channels": [
        {
          "channel_id": "CH11",
          "name": "SK Commander 3",
          "slot_id": "S3",
          "com_port": "COM7",
          "soc_vendor": "mediatek",
          "soc_model": "MTK25D",
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
      "alias": "PC04",
      "target": "rig-pc-04",
      "package": "workflow.py",
      "variables": {
        "sequenceinput_value": "Seq 4"
      }
    }
  ]
}
```

## 주요 필드

| 필드 | 설명 |
| --- | --- |
| `ftp.host` | FTP 서버 주소 |
| `ftp.root_dir` | macro spool root |
| `runtime.node_id` | 현재 PC 식별자 |
| `runtime.poll_interval_seconds` | slave polling 주기 |
| `runtime.min_screenshot_interval_seconds` | screenshot 요청 최소 간격 |
| `runtime.python_executable` | 일반 Python package 전용 실행기. export workflow에는 사용하지 않음 |
| `runtime.max_*` | 파일 보관 개수 |
| `variables` | job 실행 시 기본 변수 |
| `slaves` | master에서 보는 slave roster |
| `slaves[].channels` | 자유 이름 CH/slot과 SoC, binary provenance, 자재, test, SEQ 초기값 |
| `run_profiles` | Master의 PC별 매크로 실행표 |

`저장`은 현재 실행표도 `run_profiles`에 저장합니다. `Slave .info 내보내기`로 생성되는 slave용 파일에서는 다른 PC의 실행표를 제거합니다. 실행표 변수는 일반 문자열로 저장되므로 비밀번호나 token은 저장하지 말고 실행 직전에 입력하십시오.

CH 이름은 `CH1` 형식을 강제하지 않습니다. `CH9`, `CH10`, `PC04-RIG2`를 사용할 수 있고 CH가 없는 단일 창은 `channel_id`를 비우고 `name`을 `Main`처럼 입력합니다. 각 항목에는 `channel_id`, `slot_id`, `name` 중 하나가 반드시 있어야 합니다.

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
