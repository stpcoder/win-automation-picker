# 버튼/메뉴 사전

## 상단

| 버튼 | 설명 |
| --- | --- |
| `불러오기` | workflow JSON 열기 |
| `저장` | workflow JSON 저장 |
| `Python 내보내기` | 실행 가능한 Python 생성 |
| `실행` | 매크로 1회 실행 |
| `데이터 실행` | 데이터 행별 실행 |
| `중지` | 실행 중단 요청 |
| `연속 녹화 시작` | 외부 프로그램 조작 세션 녹화 |
| `녹화 정지` | 녹화 종료 및 블록 변환 |
| `대상 확인` | 블록 없이 component 조사 |
| `클릭 녹화` | 클릭 블록 생성 |
| `입력 녹화` | 입력 블록 생성 |
| `캡처 취소` | 대상 선택 대기 취소 |
| `대상 고급 설정` | element id, 역할, 창 구분 패널 열기 |

## 블록 작업실

| 버튼 | 설명 |
| --- | --- |
| `되돌리기` | 마지막 편집 취소 |
| `다시 실행` | 취소한 편집 복구 |
| `변경 적용` | 선택 블록의 오른쪽 필드 저장 |
| `대상 다시 선택` | 선택 블록 selector 교체 |
| `현재 값 읽기` | 현재 텍스트/색상을 읽어 조건값 갱신 |
| `선택 블록 시험` | 선택한 블록만 실행/평가 |
| `복제` | 선택 블록과 자식 전체 복제 |
| `풀기` | 반복/조건 컨테이너 제거, 자식 유지 |
| `삭제` | 선택 블록과 자식 제거 |
| `선택 입력을 PC별 변수로` | 타임라인 입력을 `${변수}`로 변경 |
| `선택 입력을 고정값으로` | 타임라인 입력을 녹화 문자열로 고정 |
| `목록 비우기` | 타임라인 표시만 비움; 생성 블록은 유지 |

## 모니터링

| 버튼 | 설명 |
| --- | --- |
| `한 번 확인` | 모니터 규칙 1회 평가 |
| `자동 시작` | 지정 주기로 반복 평가 |
| `중지` | 자동 평가 중단 |
| `텍스트 규칙 추가` | 대상 캡처 후 텍스트 규칙 생성 |
| `색상 규칙 추가` | 대상 캡처 후 색상 규칙 생성 |
| `선택 규칙 AND 묶기` | 선택 규칙을 모두 만족 조건으로 묶음 |
| `선택 규칙 OR 묶기` | 선택 규칙을 하나 이상 만족 조건으로 묶음 |
| `선택 규칙에 적용` | 보드와 CH 목록을 선택 규칙에 배정 |
| `CH 비우기` | 선택 규칙의 CH 제거 |
| `화면 적용` | 보드 행/열/순서 저장 |
| `자동 구성` | 현재 규칙으로 보드 레이아웃 자동 생성 |

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
| `PC별 매크로 실행표` | `설정 PC 불러오기` | config의 slave를 실행 행으로 추가 |
| `PC별 매크로 실행표` | `대상 추가` | Target의 PC를 실행 행으로 추가 |
| `PC별 매크로 실행표` | `실행표 전송` | PC별 macro와 variables를 각각 제출 |
| `Run on Slaves` | `Emergency stop` | stop signal 전송 |
| `Run on Slaves > More` | `Ask for screenshot` | screenshot job 제출 |
| `Slave Monitor` | `Refresh status` | slave heartbeat 읽기 |
| `Slave Monitor` | `Refresh results` | 결과 로그 읽기 |
| `Slave Monitor` | `View screenshot` | screenshot 요청/표시 |
| `Slave Monitor > More` | `Export Excel` | 상태표 `.xlsx` 저장 |
| `This PC Agent` | `Start agent` | slave polling 시작 |
| `This PC Agent` | `Check once` | pending job 1회 확인 |
| `This PC Agent` | `Stop agent` | slave polling 중지 |
