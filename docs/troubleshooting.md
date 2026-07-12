# 문제 해결

## 클릭이 녹화되지 않음

확인할 것:

1. `클릭 녹화` 또는 `입력 녹화`를 먼저 눌렀는지 확인합니다.
2. Win Automation Picker 내부가 아니라 대상 프로그램을 클릭했는지 확인합니다.
3. `실행 기록`에 error가 있는지 확인합니다.
4. 대상 프로그램이 관리자 권한이면 Win Automation Picker도 관리자 권한으로 실행합니다.

연속 녹화라면 상단 상태가 `녹화 중`인지도 확인합니다. Picker 자체의 클릭은 의도적으로 제외됩니다.

## 연속 녹화에서 입력이 빠짐

1. 타이핑하기 전에 대상 입력칸을 한 번 클릭합니다. recorder는 클릭한 UIA input component에 키 입력을 연결합니다.
2. 타임라인에 클릭 대상이 `Edit`, `Document`, `ComboBox`, `Spinner` 계열로 표시되는지 확인합니다.
3. 자체 렌더링 control이라 최종 값 읽기가 불가능하면 수동 `입력 녹화`로 대상을 잡고 오른쪽에서 값을 지정합니다.
4. 한글 IME는 키 문자 대신 입력칸의 최종 UIA 값을 사용합니다. 녹화 정지 직후 값이 다르면 다음 component를 한 번 클릭한 뒤 정지해 보십시오.
5. 비밀번호 필드는 값이 빠진 것이 정상입니다. 빈 필수 변수로 생성됩니다.

## 입력 녹화 블록이 입력하지 않음

`입력 녹화`는 녹화 중 바로 입력하지 않습니다. 실행 시 입력합니다.

확인할 것:

1. 상단 `수동 입력값` 또는 오른쪽 `입력할 텍스트`에 값이 있는지 확인합니다.
2. `기존값 지우기` 여부가 맞는지 확인합니다.
3. 붙여넣기가 막힌 프로그램이면 방식을 `keys`로 바꿉니다.
4. 작업실 위 캡처 품질에서 대상이 input control인지 확인합니다.

## CH1과 CH11이 헷갈림

`Window match` mode를 바꿉니다.

| mode | 사용 상황 |
| --- | --- |
| `contains` | 빠르게 포함 여부만 볼 때 |
| `equals` | 정확히 같은 텍스트만 허용할 때 |
| `regex` | `CH 11`, `Ch11` 같은 변형까지 처리할 때 |

정규식 예:

```text
\bch\s*11\b
```

## 팝업 버튼을 못 찾음

1. 팝업이 열린 상태에서 `선택 대상 다시 잡기`로 다시 캡처합니다.
2. 버튼 중앙을 클릭합니다.
3. `데이터 / 고급 > 창 후보`에서 후보 창을 확인합니다.
4. `대상 고급 설정`의 창 구분 값에 팝업 내부 고유 텍스트를 넣습니다.

## 블록을 반복/조건 안으로 넣을 수 없음

1. 블록을 끌 때 표시되는 14px 하늘색 결합 영역을 확인합니다.
2. 컨테이너의 빈 안쪽 `여기에 블록 놓기` 가까이에서 놓습니다.
3. AND/OR 묶음에는 일반 클릭/입력 블록을 넣을 수 없습니다.
4. 컨테이너를 자기 자신 안으로 넣는 동작은 차단됩니다.

## 자동 모니터링 값이 갱신되지 않음

1. `모니터링` 탭에 텍스트/색상 규칙이 하나 이상 있는지 확인합니다.
2. 규칙 행의 `최근 읽은 값`이 `ERROR`인지 확인합니다.
3. `현재 값 읽기`로 selector가 여전히 유효한지 확인합니다.
4. 프로그램이 관리자 권한이면 Picker도 같은 권한으로 실행합니다.
5. 화면 테마가 바뀌었다면 색상 규칙을 다시 샘플링합니다.

## FTP status가 안 보임

1. slave PC에서 `이 PC Agent > Agent 시작`이 실행 중인지 확인합니다.
2. master와 slave의 `root_dir`이 같은지 확인합니다.
3. FTP 계정이 `status/`, `results/`, `logs/`에 쓰기 권한이 있는지 확인합니다.
4. `1 오늘 작업 > PC · CH 상태 > 새로고침`을 누릅니다.

## PC별 실행표에 변수 열이 안 보임

1. Win Automation Picker에서 입력 블록이 `${variable}` 형식인지 확인합니다.
2. 현재 workflow를 다시 업로드해 package metadata를 갱신합니다.
3. `1 오늘 작업 > 실행 > 자동화 새로고침` 후 해당 macro를 다시 선택합니다.
4. 예전 package나 임의 Python 파일은 변수 metadata가 없을 수 있습니다. 이때는 `운영 도구 열기`의 `입력값`을 사용하거나 Picker에서 다시 등록합니다.

## screenshot이 안 뜸

1. slave agent가 interactive desktop에서 실행 중인지 확인합니다.
2. `min_screenshot_interval_seconds` 때문에 skip된 것은 아닌지 master log를 확인합니다.
3. FTP의 `screenshots/{node}/` 폴더 권한을 확인합니다.

## FTP 서버 부하가 걱정됨

이 도구는 FTP를 socket처럼 고속 polling하지 않는 구조입니다.

- 기본 polling interval을 5초 이상으로 둡니다.
- monitor loop는 최소 10초 간격으로 제한됩니다.
- screenshot은 수동 요청 위주로 사용합니다.
- 오래된 결과와 screenshot은 retention 설정으로 정리합니다.

## 4채널 콘솔에서 COM 연결 실패

1. `PC 환경` 또는 `RigCommander.exe device system-check`를 실행합니다.
2. `Rig 설정 > 원격 PC · CH`의 COM과 baud가 Device Manager 값과 같은지 확인합니다.
3. PuTTY, QTTY, SK Commander 등 같은 COM을 연 프로그램을 닫습니다.
4. 실시간 콘솔은 Master가 아니라 COM이 연결된 Slave PC에서 엽니다.
5. USB가 재인식됐다면 `CH 새로고침` 후 실제 COM 번호를 다시 저장합니다.

## 명령이 일부만 입력됨

1. `글자 지연 ms`를 5~30부터 올려 봅니다.
2. 명령은 출력 가능한 ASCII만 입력합니다. Enter와 Ctrl 키는 `제어 키`를 사용합니다.
3. CH의 line ending이 장비 사양과 같은지 `rig-commander.config.json`에서 확인합니다.
4. 주기 Enter가 시험 command와 충돌하면 값을 0으로 끕니다.

## 직접 COM 실행표가 제출되지 않음

- `직접 COM 실행에는 COM 포트가 필요`이면 해당 CH의 Console COM을 저장합니다.
- `same COM twice`이면 같은 PC의 두 CH가 같은 COM을 가리키므로 Device Manager와 설정을 바로잡습니다.
- 같은 PC/Campaign/attempt에 5행 이상이면 최대 네 CH씩 attempt 또는 실행 체크를 나눕니다.
- `whitespace after ';'`이면 `cmd1;cmd2;`처럼 세미콜론 뒤 공백을 제거합니다.
- `must end with ';'`이면 각 command line의 마지막 세미콜론을 추가하고 Generator Validate를 다시 실행합니다.

실행 중 한 CH만 FAIL이면 `work_dir/serial-results/{job-CH}/manifest.json`에서 실패 command,
timeout과 최근 response를 확인합니다. 다른 CH 결과는 같은 batch 안에서도 별도로 보존됩니다.

## Binary 사전점검이 BLOCKED

결과의 첫 `BLOCK` 행을 먼저 해결합니다.

- `execution_allowlist`: 장치 도구의 실제 실행 허용이 꺼져 있음
- `cli_evidence`/`result_rules`: CLI 근거 또는 성공/실패 문구 누락
- `xml_sha256`: Seq Generator export 뒤 XML이 변경됨
- `tool_vendor`: CH Vendor와 Downloader Vendor 불일치
- `qc_physical_switch`: Qualcomm 물리 Download 스위치 미확인
- `mtk_preloader_exit`: MTK preloader 종료 미확인
- `format_confirmation`: `FORMAT node:CH` 확인문 불일치
- `ADB target`: 여러 장치 중 고정 serial 누락 또는 offline

Downloader가 이미 시작된 상태에서는 프로세스를 강제 종료하지 말고 도구 로그가 끝날 때까지
기다립니다. 다음 CH 작업은 긴급 중단으로 시작 전에 차단할 수 있습니다.
