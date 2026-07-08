# 조건과 모니터링 블록

## If 블록

If 블록은 실행 흐름을 제어합니다.

| 버튼 | 조건 |
| --- | --- |
| `If selected exists` | 선택한 대상이 현재 화면에서 찾히면 실행 |
| `If selected text` | 선택한 대상의 텍스트가 기대값과 맞으면 실행 |
| `If selected color` | 선택한 대상 위치의 색상이 기대 색상과 가까우면 실행 |

사용 순서:

1. 먼저 대상 버튼/상태칸을 `Click block` 또는 `Inspect`로 잡습니다.
2. `Sequence` 또는 `Macro Workspace`에서 블록을 선택합니다.
3. `Logic > If selected text` 같은 조건 버튼을 누릅니다.
4. 조건값을 입력합니다.
5. 선택 블록이 조건 컨테이너 안으로 들어갔는지 확인합니다.

## Monitor 블록

Monitor 블록은 실행 흐름을 바꾸지 않고 상태만 기록합니다.

| 버튼 | 기록 내용 |
| --- | --- |
| `Monitor text` | 대상 텍스트가 조건을 만족하는지 |
| `Monitor color` | 대상 위치 색상이 기대 색상과 가까운지 |

예: SK Commander 상태등 모니터링

1. `Inspect`로 상태 표시 component를 클릭합니다.
2. 파란색 실행중 상태라면 `Monitor color`를 누릅니다.
3. 기대 색상에 `#0000FF` 또는 실제 추출한 색상을 입력합니다.
4. 허용 오차를 입력합니다.
5. `Monitor Mapping`에 `Board=SK Commander`, `CH=CH1`, `State=RUNNING`을 입력합니다.
6. `Apply mapping`을 누릅니다.

## Group AND / OR

여러 조건을 하나의 모니터링 블록처럼 묶을 수 있습니다.

| 버튼 | 의미 |
| --- | --- |
| `Group AND` | 모든 조건이 맞아야 OK |
| `Group OR` | 하나 이상 맞으면 OK |

예:

1. CH 식별용 조건 블록을 만듭니다.
2. 상태 색상 조건 블록을 만듭니다.
3. `Sequence`에서 두 조건 블록을 선택합니다.
4. `Group AND`를 누릅니다.
5. 그룹 블록에 `Monitor Mapping`을 설정합니다.

## CH 번호가 겹칠 때

`CH1`이 `CH11`에 잘못 매칭되면 `Window match` mode를 `equals` 또는 `regex`로 바꿉니다.

예:

```text
\bch\s*11\b
```

이 정규식은 `CH11`, `Ch 11`, `ch   11`처럼 공백과 대소문자가 섞인 경우를 구분합니다.
