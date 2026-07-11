# FTP master/slave 개요

사내망에서 custom inbound port를 열 수 없을 때 FTP 서버를 공유 spool처럼 사용합니다.

## 구조

```text
Master PC
  RigFtpCommander.exe
  ├─ macro upload
  ├─ job submit
  ├─ status refresh
  └─ screenshot request

FTP Server
  /win_automation_macros
    ├─ packages/
    ├─ commands/
    ├─ status/
    ├─ results/
    ├─ logs/
    ├─ screenshots/
    └─ archive/

Slave PC
  RigFtpCommander.exe
  └─ This PC Agent
```

## FTP 폴더 역할

| 폴더 | 역할 |
| --- | --- |
| `packages/` | master가 업로드한 workflow 또는 고급 Python package |
| `commands/{node}/pending/` | 특정 slave용 job |
| `commands/all/pending/` | 전체 slave broadcast job |
| `status/` | slave heartbeat |
| `results/{node}/` | job 결과 JSON |
| `logs/{node}/` | stdout/stderr log |
| `screenshots/{node}/` | 화면 캡처 PNG |
| `archive/{node}/` | slave가 처리한 command 보관 |

## 권장 운영 흐름

1. master PC에서 FTP config를 저장합니다.
2. `연결 확인` 후 `서버 폴더 초기화`로 FTP 폴더를 준비합니다.
3. slave별 `rig-ftp.info`를 만듭니다.
4. 각 slave PC에서 `이 PC Agent > Agent 시작`을 누릅니다.
5. master에서 macro를 업로드합니다.
6. PC별 실행표를 채우고 `실행표 전송`을 누릅니다.
7. `상태 모니터링`에서 상태, 결과와 요청한 전체 화면을 확인합니다.

Win Automation Picker가 export한 workflow는 slave EXE의 내장 엔진으로 실행됩니다. 별도 Python 설치는 필요하지 않습니다. 사용자가 직접 만든 일반 Python 파일을 업로드할 때만 slave의 `외부 Python (고급)` 경로와 모듈 설치가 필요합니다.
