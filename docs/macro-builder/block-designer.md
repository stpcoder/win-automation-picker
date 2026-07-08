# 블록 디자인

`Build` 탭은 Scratch처럼 블록을 쌓아 매크로를 설계하는 화면입니다.

## Add Blocks

| 그룹 | 버튼 | 역할 |
| --- | --- | --- |
| `Capture` | `Click block` | 대상 버튼/메뉴 클릭 블록 생성 |
| `Capture` | `Type block` | 대상 입력칸 입력 블록 생성 |
| `Action` | `Wait` | 대기 시간 추가 |
| `Action` | `Press Enter` | `{ENTER}` 키 입력 |
| `Action` | `Custom key` | `{TAB}`, `^s`, `{ESC}` 같은 key sequence 입력 |
| `Action` | `Repeat selected` | 선택 블록을 반복 컨테이너로 감쌈 |
| `Logic` | `If selected exists` | 대상이 있을 때만 실행 |
| `Logic` | `If selected text` | 대상 텍스트가 조건을 만족할 때만 실행 |
| `Logic` | `If selected color` | 대상 위치의 색상이 조건을 만족할 때만 실행 |
| `Logic` | `Monitor text` | 텍스트 상태를 OK/FAIL로 기록 |
| `Logic` | `Monitor color` | 색상 상태를 OK/FAIL로 기록 |
| `Logic` | `Group AND` | 여러 조건을 모두 만족해야 OK |
| `Logic` | `Group OR` | 여러 조건 중 하나만 만족해도 OK |

## Macro Workspace

녹화된 블록은 `Macro Workspace`에 번호가 붙은 컬러 블록으로 표시됩니다.

- 노란 테두리: 현재 선택된 블록
- 숫자 배지: 실행 순서
- 블록 제목: block name 또는 자동 생성 이름
- 부제: 대상 selector, 조건, 반복 횟수 등 요약

## Selected Block

블록을 선택한 뒤 다음 값을 바꿀 수 있습니다.

| 필드 | 설명 |
| --- | --- |
| `Name` | 사람이 읽는 블록명 |
| `Color` | 고정 블록 색상 |
| `Color by` | `event` 또는 `window` 기준 자동 색상 |
| `Repeat x` | 반복 횟수 |
| `Apply changes` | 변경 저장 |
| `Wrap repeat` | 선택 블록을 반복 블록으로 감쌈 |
| `Unwrap` | repeat/if 컨테이너를 풀고 내부 블록 유지 |

## Monitor Mapping

모니터링 보드에 표시할 블록은 `Monitor Mapping`을 채웁니다.

| 필드 | 예시 | 의미 |
| --- | --- | --- |
| `Board` | `SK Commander` | 어느 보드/탭에 표시할지 |
| `CH` | `CH1`, `CH11` | 어느 채널/장비인지 |
| `State` | `RUNNING`, `PASS`, `FAIL` | 어떤 상태 칸인지 |

값을 입력한 뒤 `Apply mapping`을 누릅니다.

## 추천 설계 방식

1. `Click block`과 `Type block`으로 기본 실행 흐름을 만듭니다.
2. 실행 중 기다려야 하는 구간에 `Wait`를 넣습니다.
3. 특정 상태에서만 실행해야 하면 `If selected text` 또는 `If selected color`로 감쌉니다.
4. 상태 확인용 블록은 `Monitor text` 또는 `Monitor color`로 만듭니다.
5. CH와 상태를 `Monitor Mapping`에 입력합니다.
6. `Dashboard`에서 보드 구조를 확인합니다.
