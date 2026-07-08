# 버튼/메뉴 사전

## Win Automation Picker

| 위치 | 버튼/메뉴 | 설명 |
| --- | --- | --- |
| `Capture` | `Inspect` | 클릭 위치 selector만 확인 |
| `Capture` | `Click block` | 클릭 step 생성 |
| `Capture` | `Type block` | 입력 step 생성 |
| `Capture` | `Cancel` | 캡처 대기 취소 |
| `Input` | `Clear` | 기존 입력값 삭제 후 입력 |
| `Run` | `Run once` | workflow 1회 실행 |
| `Run` | `Run rows` | `Data Rows` 행별 반복 실행 |
| `Run` | `Stop` | 실행 중지 요청 |
| `More > Actions` | `Save workflow` | workflow JSON 저장 |
| `More > Actions` | `Load workflow` | workflow JSON 불러오기 |
| `More > Actions` | `Export Python` | Python script 내보내기 |
| `More > Actions` | `Test current click` | 현재 selector 클릭 테스트 |
| `More > Actions` | `Test current type` | 현재 selector 입력 테스트 |
| `More > Actions` | `Add wait block` | wait step 추가 |
| `More > Actions` | `Add Enter block` | Enter key step 추가 |
| `More > Actions` | `Add custom key block` | custom key step 추가 |
| `Target Setup` | `Apply` | selected step metadata 저장 |
| `Target Setup` | `Apply match` | current selector에 window match 적용 |
| `Target Setup` | `Test windows` | 창 후보 디버그 |

## Build 탭

| 위치 | 버튼 | 설명 |
| --- | --- | --- |
| `Add Blocks > Capture` | `Click block` | 클릭 블록 추가 |
| `Add Blocks > Capture` | `Type block` | 입력 블록 추가 |
| `Add Blocks > Action` | `Wait` | 대기 블록 추가 |
| `Add Blocks > Action` | `Press Enter` | Enter 입력 |
| `Add Blocks > Action` | `Custom key` | 키 시퀀스 입력 |
| `Add Blocks > Action` | `Repeat selected` | 선택 블록 반복 |
| `Add Blocks > Logic` | `If selected exists` | 존재 조건 |
| `Add Blocks > Logic` | `If selected text` | 텍스트 조건 |
| `Add Blocks > Logic` | `If selected color` | 색상 조건 |
| `Add Blocks > Logic` | `Monitor text` | 텍스트 모니터링 |
| `Add Blocks > Logic` | `Monitor color` | 색상 모니터링 |
| `Add Blocks > Logic` | `Group AND` | 조건 묶기 AND |
| `Add Blocks > Logic` | `Group OR` | 조건 묶기 OR |

## Rig FTP Commander

| 위치 | 버튼/메뉴 | 설명 |
| --- | --- | --- |
| 상단 | `Browse` | config 선택 |
| 상단 | `Load` | config 로드 |
| 상단 | `Save` | config 저장 |
| 상단 `More` | `Create example config` | 예제 config 생성 |
| `Server Setup` | `Init folders` | FTP spool 폴더 초기화 |
| `Server Setup > More` | `Export slave .info` | slave별 config 생성 |
| `Macro Upload` | `Upload macro` | Python macro 업로드 |
| `Run on Slaves` | `Submit macro` | 선택 macro 실행 요청 |
| `Run on Slaves` | `Emergency stop` | stop signal 전송 |
| `Run on Slaves > More` | `Ask for screenshot` | screenshot job 제출 |
| `Slave Monitor` | `Refresh status` | slave heartbeat 읽기 |
| `Slave Monitor` | `Refresh results` | 결과 로그 읽기 |
| `Slave Monitor` | `View screenshot` | screenshot 요청/표시 |
| `Slave Monitor > More` | `Export Excel` | 상태표 `.xlsx` 저장 |
| `This PC Agent` | `Start agent` | slave polling 시작 |
| `This PC Agent` | `Check once` | 한 번만 pending job 확인 |
| `This PC Agent` | `Stop agent` | slave polling 중지 |
