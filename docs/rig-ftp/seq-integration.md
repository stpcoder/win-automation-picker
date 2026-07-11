# SEQ Generator와 SK Commander 연동

Mobile DRAM AE 업무에서 PC 1대에 여러 실장기와 SK Commander 창이 연결된 경우의 권장 흐름입니다.

```text
TestSeqGenerator.exe
  -> Validate
  -> Export Rig Package (.rigseq.zip)
  -> RigFtpCommander Master
  -> FTP
  -> RigFtpCommander Slave
  -> SK Commander 런처 workflow
  -> CH/슬롯에 맞는 SK Commander 창
```

## 준비물

| 파일 | 만드는 프로그램 | 용도 |
| --- | --- | --- |
| `*.rigseq.zip` | Test Sequence Generator | 검증된 SEQ, Recipe, 검사 결과, SHA-256을 묶은 배포본 |
| `*.py` workflow | Win Automation Picker | SK Commander에서 SEQ를 불러오고 시작하는 UI 자동화 |
| `rig-ftp.info` | Rig FTP Commander | FTP와 Slave PC 목록, 실행표 설정 |
| `*.rigbinary.json` | Test Sequence Generator | 선택 XML의 SoC, 버전, 원본 폴더, 수정 시각, SHA-256 메타데이터 |

## 1. SK Commander 런처 workflow 만들기

Win Automation Picker에서 실제 업무를 한 번 녹화합니다.

1. `연속 녹화 시작`을 누릅니다.
2. 대상 SK Commander에서 `SEQ 불러오기`를 누릅니다.
3. 파일 선택창에서 임시 SEQ를 선택합니다.
4. SK Commander에서 최종 `시작` 버튼까지 누릅니다.
5. Picker로 돌아와 `녹화 정지`를 누릅니다.
6. 파일 경로를 입력하는 블록의 실제 경로를 `${seq_path}`로 바꿉니다.
7. 같은 제목의 창이 여러 개면 창 구분 component의 marker를 `${channel}`로 바꿉니다.
8. `선택 블록 시험`과 전체 `실행`으로 확인한 뒤 Python workflow를 내보냅니다.

`channel=CH11`인 실행 행에서는 selector의 root 이름, AutomationId, class, 창 marker에 포함된 `${channel}`이 모두 `CH11`로 치환됩니다. 모니터 보드의 tab/channel/state 이름도 같은 변수를 사용할 수 있습니다.

CH가 없는 장비는 `channel`을 비워 둡니다. 대신 창 안의 다른 고유 component marker를 사용하거나 슬롯별 런처를 따로 만듭니다.

## 2. SEQ 패키지와 런처 업로드

1. Rig Master의 `자동화 / SEQ 업로드`에서 런처 `.py`를 먼저 업로드합니다.
2. `.rigseq.zip`을 업로드합니다.
3. 목록에서 `[FLOW]`와 `[SEQ]`로 구별되는지 확인합니다.
4. SEQ 상세 정보에서 Recipe, Command Set, corner, block/command 수를 확인합니다.

`Field Verified: False`는 패키지 오류가 아니라 실제 장비 성공 자료와 아직 대조되지 않았다는 의미입니다. Grid 한 줄 문법과 설정된 SK compatibility profile 검사는 통과했지만 실제 prompt/timing/protocol 호환성을 보증하지 않습니다.

## 3. 한 PC에 4개 실장기 배정

1. `연결 설정 > Slave PC 목록`에서 PC를 선택하고 `CH 관리`를 누릅니다.
2. CH9, CH10, CH11, CH12와 Slot, SoC, 자재를 등록합니다.
3. 필요하면 Seq Generator의 `.rigbinary.json`을 선택 CH에 불러옵니다.
4. `[SEQ]` 패키지를 선택합니다.
5. 빠른 실행의 `SK Commander 런처`에서 `[FLOW]` 런처를 고릅니다.
6. `PC / 슬롯 / CH별 실행표 > 설정 PC 불러오기`를 누릅니다.
7. 등록된 네 CH가 네 행으로 생겼는지 확인하고 SEQ/런처를 조정합니다.

예:

| 실행 | PC / Node | SEQ / 매크로 | CH | 슬롯 | SK Commander 런처 |
| --- | --- | --- | --- | --- | --- |
| 체크 | rig-pc-04 | four-corner.rigseq.zip | CH9 | S1 | sk-launcher.py |
| 체크 | rig-pc-04 | four-corner.rigseq.zip | CH10 | S2 | sk-launcher.py |
| 체크 | rig-pc-04 | row-hammer.rigseq.zip | CH11 | S3 | sk-launcher.py |
| 체크 | rig-pc-04 | aging.rigseq.zip | CH12 | S4 | sk-launcher.py |

같은 Node ID를 여러 행에 사용하는 것이 정상입니다. 각 행은 별도 job이 되어 해당 CH/슬롯에 지정한 SEQ를 실행합니다.

실행표에는 CH뿐 아니라 `soc_vendor`, `soc_model`, `binary_name`, `binary_version`, `binary_source_path`, `binary_updated_at`, `dram_part`, `lot_id`, `sample_id`, `test_name`, `sequence_name`이 함께 전달됩니다. Slave heartbeat의 같은 CH 행이 이 값과 실행 상태를 유지합니다.

한 PC의 UI job은 포커스 충돌을 막기 위해 순서대로 처리됩니다. 런처 workflow는 `SEQ 불러오기 > 시작`까지만 수행하고 종료하는 구성이 적합합니다. 네 SK Commander가 테스트를 시작한 뒤의 장시간 상태 추적은 클릭/입력이 없는 별도 monitor workflow로 수행합니다.

## 4. Slave에서 일어나는 일

Slave는 job을 받으면 다음 순서로 처리합니다.

1. ZIP 크기와 고정 member를 확인합니다.
2. `sequence.seq`와 `recipe.hseq.json`의 SHA-256을 확인합니다.
3. 생성기 validation이 `ok=true`인지 확인합니다.
4. `작업 폴더/sequences/{bundle_id}/sequence.seq`에 저장합니다.
5. 런처 workflow에 런타임 변수를 넘깁니다.

| 변수 | 값 |
| --- | --- |
| `${seq_path}` | Slave에 저장된 SEQ의 절대 경로 |
| `${channel}` | 실행표의 CH 값 |
| `${slot_id}` | 실행표의 슬롯 값 |
| `${seq_recipe}` | 패키지 Recipe 이름 |
| `${seq_command_set}` | Command Set ID |

SEQ bundle의 Grid 수는 CH 상태의 `total_grids`에 기록됩니다. 런처가 SK Commander의 시작 버튼을 누른 직후 상태는 `running`, 완료 수는 `0`입니다. 실제 `3/12`, PASS/FAIL 같은 진행 상태는 별도의 monitor workflow가 화면 component에서 읽어 heartbeat에 반영해야 합니다.

ZIP을 `extractall`하지 않으므로 패키지 안의 임의 경로가 Slave의 다른 폴더를 덮어쓰지 않습니다. 같은 bundle은 같은 SHA 기반 폴더를 재사용하고, Rig가 만든 정상 형태의 폴더만 최근 50개까지 유지합니다. 이름이나 내부 파일이 다른 사용자 폴더는 정리 대상에 넣지 않습니다.

## 5. 모니터링과 중단

- 런처 workflow 마지막에 상태 monitor 블록을 넣으면 실행 결과 JSON에 함께 저장됩니다.
- 별도 monitor-only workflow를 주기 실행하면 클릭/입력 없이 상태만 읽습니다.
- Master의 `긴급 중단`은 실행 중 workflow가 다음 stop 확인 시점에 중단되도록 요청합니다.
- 화면이 꼭 필요할 때만 `전체 화면 보기`를 눌러 최신 캡처를 요청합니다.

실제 SK Commander 성공 SEQ와 로그를 확보하기 전에는 직접 COM Agent가 동일하게 작동한다고 가정하지 않습니다. 운영은 이 런처 backend를 사용하고, 수집한 timing/prompt/error evidence를 바탕으로 직접 COM backend를 단계적으로 검증합니다.
