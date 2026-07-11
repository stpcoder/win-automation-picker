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
- 녹화된 변수 기본값
- helper API

## 실행

Windows PC에서 패키지를 설치한 뒤 실행합니다.

```powershell
python -m pip install -e .
python .\exported_workflow.py
```

PC별 값을 직접 덮어쓸 수 있습니다. 명령행 값은 녹화 기본값과 데이터 행보다 우선합니다.

```powershell
python .\exported_workflow.py --vars-json '{"sequenceinput_value":"Seq 2","channel":"CH11"}'
python .\exported_workflow.py --vars-file .\pc02-values.json
```

FTP slave는 `pass_variables` 작업을 실행할 때 slave `.info`의 변수와 master가 보낸 작업 변수를 합쳐 `--vars-json`으로 자동 전달합니다. 작업 변수가 같은 이름의 slave 기본값을 덮어씁니다.

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
