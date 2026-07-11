# Slave 세팅

## 파일 배치

각 slave PC의 같은 폴더에 다음 파일을 둡니다.

```text
RigFtpCommander.exe
rig-ftp.info
```

`rig-ftp.info`의 `runtime.node_id`는 slave마다 고유해야 합니다.

## Agent 시작

1. slave PC에서 `RigFtpCommander.exe`를 실행합니다.
2. 상단에서 config가 제대로 로드됐는지 확인합니다.
3. `이 PC Agent` 탭을 엽니다.
4. `이 PC Node ID` 값이 이 PC의 node id인지 확인합니다.
5. `Agent 시작`을 누릅니다.
6. 창을 닫지 않고 유지합니다.

## 한 번만 체크

대기 중인 job이 있는지 한 번만 확인하려면 `한 번 확인`을 누릅니다.

## 중지

agent loop를 멈추려면 `Agent 중지`를 누릅니다.

## Stop signal 해제

master에서 긴급 중단을 보낸 뒤 해당 PC의 stop signal을 지우려면 `중단 신호 해제`를 누릅니다.

FTP가 일시적으로 끊기면 Agent는 종료되지 않고 `Reconnecting` 상태로 바뀐 뒤 최대 60초 backoff로 다시 연결합니다.

## 주의

화면 캡처와 UI 자동화는 interactive desktop session에서 실행되어야 합니다. Windows service session 0에서 실행하면 실제 사용자 화면을 캡처하지 못할 수 있습니다.

Win Automation Picker가 만든 workflow는 Agent의 내장 엔진으로 실행되므로 별도 Python 설치가 필요하지 않습니다. 직접 만든 일반 Python 스크립트를 실행할 때만 `연결 설정 > 외부 Python (고급)`을 올바르게 지정합니다.
