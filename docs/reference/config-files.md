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
| `runtime.max_*` | 파일 보관 개수 |
| `variables` | job 실행 시 기본 변수 |
| `slaves` | master에서 보는 slave roster |

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
