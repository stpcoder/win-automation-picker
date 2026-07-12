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
    ├─ packages/  (workflow, Python, .rigseq.zip)
    ├─ commands/
    ├─ status/
    ├─ results/
    ├─ triage/
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
| `packages/` | master가 업로드한 workflow, 고급 Python, 검증된 Rig SEQ package |
| `commands/{node}/pending/` | 특정 slave용 job |
| `commands/all/pending/` | 전체 slave broadcast job |
| `status/` | slave heartbeat, CH inventory, 최근 campaign attempt 상태(PC당 최대 256개) |
| `results/{node}/` | job 결과 JSON |
| `triage/{node}/` | 원본 결과와 분리된 AE 실패 분류, 조치, 담당자, 다음 작업 |
| `logs/{node}/` | stdout/stderr log |
| `screenshots/{node}/` | 화면 캡처 PNG |
| `archive/{node}/` | slave가 처리한 command 보관 |

## 권장 운영 흐름

1. `3 Rig 설정 > Master 연결`에서 FTP config를 저장합니다.
2. `연결 확인` 후 `원격 PC · CH > 서버 폴더 준비`로 FTP 폴더를 준비합니다.
3. slave별 `rig-ftp.info`를 만듭니다.
4. 각 slave PC에서 `이 PC Agent > Agent 시작`을 누릅니다.
5. `2 자동화 준비`에서 검증한 macro와 SEQ를 `서버 라이브러리 등록`으로 올립니다.
6. `1 오늘 작업 > 실행`에서 `직접 COM` 또는 `SK Commander`를 고르고 실행표를 채웁니다.
7. `PC · CH 상태`에서 상태, 결과와 요청한 전체 화면을 확인합니다.

Campaign이 포함된 `.rigseq.zip`은 [AE 캠페인 운영](ae-campaign.md) 화면에서 목적,
가설, 합격/중단 조건과 `PC x CH x attempt` 전체를 한 번에 확인할 수 있습니다.

Win Automation Picker가 export한 workflow는 slave EXE의 내장 엔진으로 실행됩니다. 별도 Python 설치는 필요하지 않습니다. 사용자가 직접 만든 일반 Python 파일을 업로드할 때만 slave의 `외부 Python (고급)` 경로와 모듈 설치가 필요합니다.

Test Sequence Generator가 내보낸 `.rigseq.zip`은 Slave가 체크섬과 validation 상태를 다시
확인합니다. `직접 COM`에서는 같은 PC의 최대 4개 CH를 병렬 실행하고 Grid/console 결과를
분리 저장합니다. `SK Commander`에서는 로컬에 SEQ를 저장한 뒤 Picker 런처 workflow에
`${seq_path}`, `${channel}`, `${slot_id}`를 전달합니다. 전체 절차는
[SEQ Generator와 실장기 실행 연동](seq-integration.md)을 따릅니다.

`.rigbinary.json`은 FTP package가 아니라 Master 설정용 metadata 교환 파일입니다. `CH 관리`에서 읽은 뒤 `.info`와 heartbeat에 필요한 provenance만 저장하며 실제 binary payload는 FTP spool로 올리지 않습니다.
