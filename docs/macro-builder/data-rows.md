# 데이터 행과 템플릿

`Data Rows`는 같은 workflow를 여러 행에 대해 반복 실행할 때 사용합니다.

## 데이터 붙여넣기

Excel 또는 Google Sheets에서 범위를 복사해 `Data Rows` 탭에 붙여넣습니다.

예:

```tsv
name	message	channel
Alice	Hello Alice	CH1
Bob	Hello Bob	CH2
```

## 템플릿 변수

`Input > Text`에는 다음 변수를 사용할 수 있습니다.

| 변수 | 의미 |
| --- | --- |
| `${name}` | 헤더가 `name`인 열 |
| `${message}` | 헤더가 `message`인 열 |
| `${channel}` | 헤더가 `channel`인 열 |
| `${col1}` | 첫 번째 열 |
| `${col2}` | 두 번째 열 |
| `${row}` | 현재 행 번호 |

## Headers 옵션

첫 줄이 헤더이면 `Run > Headers`를 켭니다.

첫 줄부터 데이터라면 `Headers`를 끄고 `${col1}`, `${col2}` 방식으로 사용합니다.

## 행별 실행

1. `Data Rows`에 데이터를 붙여넣습니다.
2. `Input > Text`에 `${message}` 같은 템플릿을 입력합니다.
3. `Type block`으로 입력칸을 잡습니다.
4. `Run > Run rows`를 누릅니다.

## Row delay

`Run > Delay`는 행과 행 사이 대기 시간입니다.

대상 프로그램이 느리거나 화면 갱신 시간이 긴 경우 값을 늘립니다.
