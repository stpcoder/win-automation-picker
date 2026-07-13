# DRAM CA/DQ margin 원격 실행

## 실행 구조

DRAM margin은 일반 Python 매크로나 임의 shell job으로 실행하지 않습니다.

```text
Master PC
  DramMarginController.exe bundle
    -> checksummed *.drammargin.zip
    -> FTP packages/에 한 번 업로드

Slave Agent
  bundle 전체 SHA-256 재검증
    -> exact PC/CH와 ADB serial 대조
    -> nominal probe
    -> exact CA/DQ signal sweep / stress
    -> mV·ps·raw-code acceptance
    -> CSV와 raw JSONL을 ZIP 한 개로 FTP 업로드
```

번들에는 controller, plan, 승인 PHY reference와 native runner가 모두 들어갑니다. Slave에
Python이나 `mobile-dram-margin-lab` 소스를 설치할 필요가 없습니다. 허용된 네 파일 이외의
ZIP member, path traversal, symlink, 잘못된 PE/ELF architecture, checksum 불일치는 실행 전에
차단됩니다.

## 1. Windows controller 준비

`mobile-dram-margin-lab` 저장소의 `controller-latest` release에서 다음 파일을 같은 폴더에
둡니다.

- `DramMarginController.exe`
- `DramMarginController.exe.sha256`
- `windows-controller-manifest.json`

SHA-256이 manifest 및 `.sha256` 파일과 같은지 먼저 확인합니다. Controller는 Windows x64
one-file 실행 파일입니다.

## 2. plan과 PHY reference 준비

plan의 `target.target_id`는 Rig 연결 구조의 PC와 CH를 정확히 가리켜야 합니다. 권장값은
`PC04:CH11` 형식입니다.

| 실행 환경 | plan 값 | runner |
| --- | --- | --- |
| Android OS 실행 중 | `transport=adb`, `execution_context=live-os` | Android arm64 ELF |
| OS 없이 Windows fixture PC에서 실행 | `transport=local`, `execution_context=offline` | Windows x64 PE |

ADB 경로는 plan의 `target.adb_serial`과 `rig-ftp.info` CH의 `adb_serial`이 문자 단위로 같아야
합니다. `adb devices`의 첫 장치를 자동 선택하지 않습니다.

실제 Qualcomm/MediaTek PHY 변경은 `backend=vendor`, 승인 SoC profile, register/training spec
SHA-256, 검증된 physical DQ map SHA-256 및 실제 vendor backend가 모두 있어야 합니다. 일반
release runner는 이 backend를 포함하지 않으므로 이름만 SM8850/MTK25D로 바꿔 실행할 수
없습니다.

PHY reference는 plan의 exact `all`/`ca`/`dq` signal target과 모든 dimension 순서가 일치해야
하며 각 dimension에 raw-code conversion이 있어야 합니다. Plan에는 실행 당시의 data rate
`MT/s`, FSP, 온도 `C`, rail `mV`도 고정합니다. 이 conversion으로 결과의 실제 mV/ps 값까지
다시 계산합니다.

GUI에서는 `마진 번들 만들기 > PHY 기준 준비 · 승인`을 사용합니다.

1. `Worksheet 준비`에서 준비자, Ticket, SoC profile과 온도·주파수 같은 조건을 입력합니다.
2. read-only nominal probe가 있으면 선택할 수 있지만 값은 `observed_probe`에만 기록됩니다.
3. 생성된 UNAPPROVED worksheet의 null nominal/conversion 칸을 승인 PHY 문서 값으로 채웁니다.
4. `독립 승인`에서 준비자와 다른 승인자가 worksheet의 전체 plan SHA-256을 직접 입력합니다.
5. plan/profile/spec/DQ-map/dimension/unit/offset이 바뀌지 않았을 때만 v2 reference가 생성됩니다.
6. 승인 결과 경로는 원래 마진 번들 화면의 `PHY reference` 칸에 자동 반영됩니다.

Worksheet 자체는 schema가 달라 원격 번들에 넣을 수 없습니다. 승인 reference에는 worksheet와
plan SHA-256, 준비자·승인자, 시각과 Ticket이 고정되며 기존 파일을 덮어쓰지 않습니다.

## 3. 원격 번들 만들기

GUI에서는 `오늘 작업 > 운영 도구 열기 > 마진 번들 만들기`를 누르고 Controller, Plan, 승인된
PHY reference와 출력 위치를 선택합니다. 생성이 끝나면 결과 파일이 패키지 등록 칸에 자동으로
채워집니다.

같은 작업을 PowerShell에서 실행할 수도 있습니다.

```powershell
.\DramMarginController.exe bundle `
  .\plans\PC04-CH11.json `
  .\references\MTK25D-A0.json `
  --output .\packages\PC04-CH11.drammargin.zip

.\DramMarginController.exe verify-bundle `
  .\packages\PC04-CH11.drammargin.zip
```

frozen controller에서 `bundle`을 실행하면 현재 `DramMarginController.exe` 자체가 번들에
포함됩니다. 원본 plan은 바꾸지 않고, 번들 내부 plan의 runner 경로만 번들 상대 경로로
고정합니다.

## 4. Master에서 실행

1. `RigFtpCommander.exe`의 `오늘 작업`에서 `운영 도구 열기`를 누릅니다.
2. `패키지 등록`에서 `*.drammargin.zip`을 선택하고 업로드합니다.
3. 목록의 `[MARGIN]` badge와 Target, Signal Target, MT/s/FSP/온도/rail, DQ/Point 수를 확인합니다.
4. 대상은 `all`이 아니라 실장기 연결 PC 한 대를 선택합니다.
5. 여러 CH 중 하나를 명확히 지정하려면 입력값에 `channel=CH11`을 넣습니다.
6. 실행을 누릅니다.

Agent는 target ID를 PC 별명/Windows 이름/Node ID와 CH ID·slot·fixture ID 조합에 대조합니다.
일치하는 fixture가 정확히 하나가 아니면 native runner를 시작하지 않습니다.

## 5. 상태와 결과

CH 상태에는 다음 값이 같이 표시됩니다.

| 필드 | 의미 |
| --- | --- |
| `margin_status` | `pass`, `margin-failures`, `physical-evidence-rejected`, `interrupted` |
| `margin_result` | DQ stress/margin 자체 PASS/FAIL |
| `physical_unit_acceptance` | 승인 환산표와 mV/ps/raw-code 증거 일치 여부 |
| `result_rows` | 완료된 sweep point 수 |
| `artifact_path` | FTP의 검증된 결과 ZIP |

margin 밖의 failing point는 characterization 결과이므로 `margin-failures`입니다. 실행 오류와
구별되지만 CH 판정은 FAIL로 표시됩니다. 물리 단위 증거가 reference와 다르면
`physical-evidence-rejected`이며 결과를 승인값으로 사용하면 안 됩니다.

결과 ZIP에는 nominal probe, 각 sweep raw JSONL, run manifest, DQ summary CSV, point grid CSV,
PHY acceptance 및 campaign manifest가 들어갑니다. 모든 파일은 campaign manifest의 SHA-256과
size를 다시 확인한 뒤 업로드합니다.

## 실제 단위 로그 파싱과 2D 그래프

각 JSONL axis는 정수 요청 grid인 `requested_offset`과 실제 적용된 소수값
`actual_offset`, `nominal`, `absolute`, `raw_code`, `unit`을 별도로 보존합니다. Voltage는
`mV`, CBT/read/write timing은 `ps`이며 parser는 `absolute = nominal + actual_offset`과 실행
조건 단위를 검증합니다.

```powershell
.\DramMarginController.exe grid .\plan.json .\run --output .\all-points.csv
.\DramMarginController.exe summarize .\plan.json .\run --output .\margin-summary.csv
.\DramMarginController.exe plot .\plan.json .\run `
  --mode read-eye --signal-target DQ3 --dq DQ3 --output .\DQ3-read-eye.svg
```

CSV와 SVG는 요청 grid가 아니라 hardware가 반환한 실제 mV/ps 좌표를 사용합니다. parser
구현은 `mobile-dram-margin-lab/python/dram_margin_lab/analyze.py`에 있으며 혼합 signal 결과는
`--signal-target`을 지정하지 않으면 그래프 생성을 거부합니다.

## 긴급 중단과 FTP 부하

`긴급 중단`은 Agent가 약 2초 간격으로 확인합니다. Windows에서는 controller process group에
`Ctrl+Break`를 보내고, controller가 native runner에 종료를 전달해 nominal 복구를 기다립니다.
25초 내 복구 증거 없이 끝나지 않으면 강제 종료하고 해당 실행을 승인하지 않습니다.

FTP로 실시간 JSONL이나 화면 stream을 보내지 않습니다. package는 등록할 때 한 번, 결과 ZIP은
실행 종료 후 한 번 전송합니다. 기본 DRAM margin artifact 상한은 128 MiB이고 FTP 보관 개수는
기존 `max_artifact_files`를 따릅니다. 초과 결과는 Slave 로컬 폴더에 보존되고 FTP 결과에는
`artifact_error`가 남습니다.
