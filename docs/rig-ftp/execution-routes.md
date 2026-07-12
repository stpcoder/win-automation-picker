# SK Commander 연동과 직접 COM 운용

AE Workbench는 같은 SEQ 패키지를 두 실행 경로로 운용합니다. 실행표의 각 CH마다 방식을
다르게 선택할 수 있으며, Master에서 시작했는지 실장기 연결 PC에서 직접 시작했는지도
별도로 기록합니다.

![직접 COM과 SK Commander를 CH별로 선택하는 실행표](../assets/screenshots/01-today-work.png)

## 공통 상태 계약

두 경로 모두 `PC / CH 실행 보드`에 다음 값을 올립니다.

| 값 | 예 |
| --- | --- |
| 운용 방식 | `직접 COM`, `SK Commander` |
| 시작 위치 | `Master`, `현장 PC` |
| 실행 단계 | 준비, 실행 중, 외부 실행 중, 완료, 정지, 실패, 감시 중 |
| 시험 식별 | Campaign, Test, SEQ, attempt, 자재 |
| 진행 | 현재 Grid, 완료 Grid/전체 Grid |
| 판정 | pending, pass, fail, stopped |
| 증거 | 결과 JSON, Grid/console ZIP 또는 monitor 결과 |

![운용 방식과 시작 위치를 함께 표시하는 캠페인 보드](../assets/screenshots/04-campaign-monitoring.png)

`직접 COM · Master`, `SK Commander · 현장 PC`처럼 표시되므로 같은 CH가 수동으로
시작됐는지 원격으로 시작됐는지를 구별할 수 있습니다. 이 필드는 Excel의 `CH Inventory`
시트에도 `Execution Route`, `Execution Origin`, `Execution Phase`로 저장됩니다.

## 경로 A: 기존 SK Commander 사용

SK Commander가 COM, Grid 실행과 자체 로그 저장을 담당합니다. AE Workbench는 녹화한
Windows UI workflow로 SEQ 경로를 넣고 Load/Start를 누르며, 별도 monitor workflow로
화면 상태를 읽습니다.

### 런처 만들기

Picker의 `유형 / 역할`에서 다음 역할을 지정합니다.

| 역할 | 필수 | 대상 |
| --- | --- | --- |
| `sk_seq_path` | 예 | `${seq_path}`를 입력하는 SEQ 경로 칸 |
| `sk_load` | 예 | SEQ Load/Open 버튼 |
| `sk_start` | 예 | 시험 Start 버튼 |
| `sk_stop` | 선택 | SK Commander Stop 버튼 |
| `sk_reset` | 선택 | Reset 버튼 |
| `sk_power_reset` | 선택 | Power Reset 버튼 |
| `sk_grid_status` | 감시 시 권장 | `7/12 GRID_08` 같은 진행 component |
| `sk_serial_monitor` | 감시 시 권장 | 상단 serial/console 상태 component |

SEQ 런처에는 `sk_seq_path > sk_load > sk_start`만 넣는 것이 안전합니다. Stop, Reset,
Power Reset은 각각 별도 workflow로 만들어 자동화 라이브러리에서 명시적으로 실행합니다.
한 workflow에 Start와 Reset을 함께 넣으면 런처 실행 때 두 동작이 모두 수행됩니다.

업로드된 런처는 실행표 위에서 다음처럼 검사됩니다.

- `SEQ/Load/Start 준비 완료`: 필수 역할이 모두 있음
- `준비 미완료`: 표시된 역할을 다시 지정해야 함
- `호환 모드`: 이전 버전 workflow이며 실행은 허용하지만 역할 기반 검사는 하지 못함

같은 제목의 SK Commander가 네 개라면 창 내부 CH component를 window marker로 지정하고
`${channel}`을 사용합니다. CH가 없는 창은 장비 Serial, Slot 또는 다른 고유 component를
marker로 사용합니다.

### 장시간 상태 감시

런처는 Start 직후 종료하고 채널 상태는 `외부 실행 중`으로 남습니다. 장시간 시험을 런처
workflow 안에서 wait로 붙잡아 두지 않습니다. 클릭/입력이 없는 monitor workflow를 별도로
만들어 Grid, PASS/FAIL, serial 상태를 읽습니다.

Master가 시작한 시험은 monitor job으로 읽을 수 있습니다. 현장에서 사용자가 SK Commander의
Start를 직접 누른 경우에는 Slave 화면에서 다음 순서로 감시합니다.

![현장 수동 실행을 읽는 Slave Agent의 SK 감시](../assets/screenshots/11-slave-agent.png)

1. `3 Rig 설정 > 이 PC Agent`에서 `Agent 시작`을 누릅니다.
2. `현장 SK 감시`에서 읽기 전용 monitor workflow를 선택합니다.
3. 간격은 기본 `15초`, 최소 `5초`로 둡니다.
4. `감시 시작`을 누릅니다.
5. 상태가 바뀌면 결과 이력이 한 건 생성되고 Master 보드가 갱신됩니다.

변화가 없으면 결과 파일을 계속 만들지 않습니다. 상태 변화는 즉시, 같은 상태의 heartbeat는
최대 60초마다 발행하여 FTP와 Windows UI Automation 부하를 제한합니다.

!!! warning "SK Commander 자체 로그 경계"
    monitor workflow는 화면 component와 판정을 구조화해 기록하지만 SK Commander가 저장하는
    원본 Grid serial log를 대신 만들지는 않습니다. 원본 로그는 SK Commander의 검증된 저장
    경로를 유지해야 합니다. AE Workbench가 Grid별 raw response까지 직접 소유해야 하면 아래
    직접 COM 경로를 사용합니다.

## 경로 B: SK Commander 없이 직접 COM

Slave Agent가 각 CH의 COM을 직접 열어 SEQ command를 보냅니다. 같은 PC의 최대 네 CH가
서로 다른 COM을 사용할 때 병렬 실행됩니다.

### Master에서 시작

1. 실행표에서 `SEQ 방식`을 `직접 COM`으로 선택합니다.
2. CH, COM, baud, SEQ, 자재와 attempt를 확인합니다.
3. `실행 시작`을 누릅니다.
4. Slave가 Grid 경계마다 heartbeat를 갱신합니다.
5. 완료 후 결과 행의 `더보기 > 선택 결과 증거 ZIP 저장`으로 증거를 받습니다.

### 실장기 PC에서 직접 시작

![현장 직접 실행과 운영 기록값을 함께 입력하는 4채널 콘솔](../assets/screenshots/12-four-channel-console.png)

1. 실장기 PC에서 `2 자동화 준비 > 실장기 제어 · Binary > 4채널 콘솔`을 엽니다.
2. 실행 CH를 연결하고 SEQ를 선택합니다.
3. `Master 상태 공유`를 켭니다.
4. 시험명, 필요 시 온도/VDD, attempt를 입력합니다.
5. `동시 실행`을 누릅니다.

실행 중에는 Grid 전환과 60초 heartbeat가 기록됩니다. 앱이나 PC가 비정상 종료되어 heartbeat가
끊기면 Agent가 남아 있는 로컬 `running` 스냅샷을 약 3분 뒤 `stale/interrupted`로 전환하므로
Master 화면에 영구 실행 중으로 남지 않습니다.

SEQ Generator 패키지의 Recipe가 있으면 Grid별 Corner, Temp, VDD와 주파수를 Recipe에서
가져옵니다. 로컬 raw `.seq`만 선택한 경우에는 `#105_0.99_...` 같은 Grid 이름에서 추출하고,
화면에 입력한 온도/VDD를 fallback으로 사용합니다. 값을 추측할 수 없으면 빈 값으로 남깁니다.

### 직접 COM 결과 폴더

```text
work_dir/serial-results/{job_id}/
  manifest.json
  console.log
  grids/
    001__HH__T105C__VDD0.99V__GRID_NAME.log
    002__HL__T105C__VDD0.91V__GRID_NAME.log
```

`manifest.json`은 `rig-test-run/v2` 형식이며 실행 경로/시작 위치, CH/실장기/COM,
SEQ/Test/attempt, 현재·완료 Grid, command 판정과 Grid log SHA-256을 포함합니다.

- `console.log` 기본 상한: 8 MB
- Grid log 기본 상한: Grid당 2 MB
- Grid log 합계 상한: 실행당 32 MB. 이후 Grid는 manifest에 `log_omitted=true`로 기록
- FTP 증거 ZIP 기본 상한: 16 MB
- Slave 로컬 실행 폴더 기본 보관: 최근 40개
- FTP 증거 ZIP 기본 보관: PC당 최근 40개
- FTP 업로드: 실행 종료 시 CH당 한 번
- 실시간 serial stream: FTP로 전송하지 않음
- 보존: `로그 보관 개수`만큼, Workbench가 만든 manifest 형식의 폴더만 정리

상한과 보관 개수는 `3 Rig 설정 > 고급 정책`의 `실행 로그 상한(MB)`,
`증거 ZIP 상한(MB)`, `로컬 실행 보관 개수`, `FTP 증거 ZIP 보관 개수`에서 조정합니다.
`FTP 증거 ZIP 보관 개수`를 `0`으로 두면 상태·결과 JSON은 유지하면서 증거 ZIP 업로드만 끕니다.
ZIP에는 manifest와 Grid log를 우선 넣고, 용량이 남을 때 console을 넣습니다.

## 경로 전환 전 확인

| 확인 | SK Commander | 직접 COM |
| --- | --- | --- |
| COM 소유 | SK Commander가 소유 | SK Commander/QTTY/PuTTY 연결 해제 후 Agent가 소유 |
| Start/Stop/Reset | 녹화한 명시적 workflow | SEQ 정지와 serial 명령 |
| Grid 진행 | monitor component가 읽음 | 실행 엔진이 직접 계산 |
| raw Grid log | SK Commander 자체 로그 | Workbench가 Grid별 생성 |
| 현장 수동 시작 | `현장 SK 감시` 필요 | `Master 상태 공유` 사용 |
| Master 긴급 중단 | 실행 중 workflow 중단. 이미 시작된 시험은 별도 Stop workflow 필요 | 다음 stop 확인에서 command 전송 중단 |

한 COM은 두 프로그램이 동시에 열 수 없습니다. SK Commander를 끄지 않고 직접 COM을
선택하면 포트 열기 단계에서 실패하는 것이 정상입니다. 반대로 SK Commander 경로에서는
Agent 4채널 콘솔의 해당 COM 연결을 먼저 해제합니다.

## 현장 승인 체크

- [ ] 실제 SK Commander에서 SEQ/Load/Start selector를 한 CH씩 재생했다.
- [ ] CH marker가 네 창을 서로 바꾸어 찾지 않는다.
- [ ] Stop/Reset/Power Reset workflow는 launcher와 분리했다.
- [ ] monitor workflow에는 클릭과 입력 블록이 없다.
- [ ] 직접 COM은 성공 이력이 있는 SEQ와 prompt/timing으로 한 CH dry-run했다.
- [ ] 실패 marker와 command timeout이 실제 장비 로그와 맞는다.
- [ ] 현장 시작 시험이 Master 보드에 `현장 PC`로 표시된다.
- [ ] 결과 ZIP에서 Grid별 Temp/VDD와 raw response를 열어 확인했다.
