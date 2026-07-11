# Python export

`Python 내보내기`는 현재 workflow를 실행 가능한 `.py` 파일로 저장합니다.

## 내보내기

1. 매크로를 녹화하고 `매크로 만들기`에서 중첩 구조와 실행 전 오류를 확인합니다.
2. 상단 `Python 내보내기`를 누릅니다.
3. 저장할 `.py` 파일 경로를 선택합니다.

## export 파일에 포함되는 것

- click/type/wait/key/repeat/condition/monitor step
- named element metadata
- `데이터 행`
- 첫 행 헤더 설정
- 행 사이 대기 설정
- helper API

## 실행

Windows PC에서 패키지를 설치한 뒤 실행합니다.

```powershell
python -m pip install -e .
python .\exported_workflow.py
```

## agent용 helper API

export된 script는 agent가 호출할 수 있는 helper 함수를 포함합니다.

```python
print(list_elements())
click_element("start_button")
type_into("message_input", "hello", clear=True)
press_key("{ENTER}")

if element_exists("optional_ok_button"):
    click_element("optional_ok_button")
```

## 주의

export된 Python도 Windows UI Automation을 사용하므로 Windows interactive desktop session에서 실행해야 합니다.
