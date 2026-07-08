# 기본 매크로 만들기

## 클릭 블록 만들기

1. `WinAutomationPicker.exe`를 실행합니다.
2. `Target Setup > Name`에 이름을 적습니다. 예: `start_button`.
3. `Target Setup > Type`은 `button`으로 둡니다.
4. 같은 프로그램 창이 여러 개면 `Window match`에 창 안의 구분 텍스트를 넣습니다. 예: `CH1`.
5. `Click block`을 누릅니다.
6. 대상 프로그램에서 실제로 누를 버튼을 클릭합니다.
7. `Capture Quality`를 확인합니다.
8. `Build > Macro Workspace`에 새 블록이 생겼는지 확인합니다.

## 입력 블록 만들기

1. `Input > Text`에 입력할 값을 적습니다. 예: `${message}`.
2. 기존 값을 지우고 입력할 경우 `Clear`를 켭니다.
3. 기본 입력 방식은 `paste`입니다.
4. 붙여넣기가 막힌 프로그램이면 `Method`를 `keys`로 바꿉니다.
5. `Target Setup > Name`에 입력칸 이름을 적습니다. 예: `message_input`.
6. `Target Setup > Type`은 `input`으로 둡니다.
7. `Type block`을 누릅니다.
8. 대상 프로그램의 입력칸을 클릭합니다.

!!! info "Type block은 바로 타이핑하지 않습니다"
    `Type block`은 “어느 입력칸에 입력할지”만 녹화합니다. 실제 입력은 `Run once`, `Run rows`, export된 Python 실행 시점에 발생합니다.

## 실행

한 번만 실행:

1. `Run > Run once`를 누릅니다.
2. `Run Log`에서 진행 상태를 확인합니다.

행별 반복 실행:

1. `Data Rows` 탭에 Excel/Google Sheets 데이터를 붙여넣습니다.
2. 첫 줄이 헤더라면 `Run > Headers`를 켭니다.
3. `Run > Run rows`를 누릅니다.

## 저장과 불러오기

| 작업 | 위치 |
| --- | --- |
| workflow 저장 | `More > Actions > Save workflow` |
| workflow 불러오기 | `More > Actions > Load workflow` |
| Python export | `More > Actions > Export Python` |
| selector 복사/저장/로드 | `More > Actions` |

## 잘못 녹화했을 때

1. `Sequence` 탭을 엽니다.
2. 잘못된 step을 선택합니다.
3. `Delete step`을 누릅니다.
4. 순서가 틀렸으면 `Move up`, `Move down`을 사용합니다.
