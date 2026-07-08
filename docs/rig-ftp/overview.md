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
| `packages/` | master가 업로드한 Python macro |
| `commands/{node}/pending/` | 특정 slave용 job |
| `commands/all/pending/` | 전체 slave broadcast job |
| `status/` | slave heartbeat |
| `results/{node}/` | job 결과 JSON |
| `logs/{node}/` | stdout/stderr log |
| `screenshots/{node}/` | 화면 캡처 PNG |
| `archive/{node}/` | slave가 처리한 command 보관 |

## 권장 운영 흐름

1. master PC에서 FTP config를 저장합니다.
2. `Init folders`로 FTP 폴더를 초기화합니다.
3. slave별 `rig-ftp.info`를 만듭니다.
4. 각 slave PC에서 `This PC Agent > Start agent`를 누릅니다.
5. master에서 macro를 업로드합니다.
6. target을 선택해 `Submit macro`를 누릅니다.
7. `Slave Monitor`에서 상태와 결과를 확인합니다.
