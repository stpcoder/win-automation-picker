# 화면 구성

## Win Automation Picker

### 상단 작업 영역

| 영역 | 역할 |
| --- | --- |
| `Capture` | `Inspect`, `Click block`, `Type block`, `Cancel` |
| `Input` | 입력 텍스트, 기존 값 삭제 여부, 입력 방식 |
| `Run` | `Run once`, `Run rows`, `Stop`, row 실행 옵션 |
| `More` | 저장/로드/export, selector 테스트, wait/key 추가, JSON 적용 |

### Target Setup

| 필드 | 입력 내용 |
| --- | --- |
| `Name` | agent나 Python 코드에서 부를 이름. 예: `start_button`, `message_input` |
| `Type` | `button`, `input`, `menu`, `checkbox`, `text` 등 |
| `Note` | 사람이 읽는 설명 |
| `Window match` | 같은 프로그램 창이 여러 개일 때 구분할 텍스트. 예: `CH1`, `CH11` |

### 좌측 패널

| 탭 | 설명 |
| --- | --- |
| `Target Detail` | 현재 selector JSON |
| `Recipe JSON` | 전체 workflow JSON |
| `Data Rows` | Excel/Google Sheets에서 복사한 반복 실행 데이터 |

### 우측 패널

| 탭 | 설명 |
| --- | --- |
| `Inspect` | XPath-like path와 Python snippet |
| `Build` | 블록 기반 매크로 디자인 |
| `Dashboard` | 모니터링 보드 설계 |
| `Sequence` | step 순서 목록 |
| `Targets` | agent용 element 목록 |
| `Run Log` | 녹화/실행 상태 로그 |
| `Capture` | 캡처 품질 상세 정보 |
| `Windows` | 창 후보 디버그 |
| `Deploy` | FTP 서버로 현재 매크로 업로드 |

## Rig FTP Commander

| 탭 | 역할 |
| --- | --- |
| `Monitor & Run` | macro 업로드, 실행, slave 상태 모니터링 |
| `This PC Agent` | 현재 PC를 slave agent로 실행 |
| `Connection Setup` | FTP 계정, node id, poll interval, retention 설정 |
