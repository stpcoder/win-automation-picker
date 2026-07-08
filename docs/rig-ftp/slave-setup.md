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
3. `This PC Agent` 탭을 엽니다.
4. `Node` 값이 이 PC의 node id인지 확인합니다.
5. `Start agent`를 누릅니다.
6. 창을 닫지 않고 유지합니다.

## 한 번만 체크

대기 중인 job이 있는지 한 번만 확인하려면 `Check once`를 누릅니다.

## 중지

agent loop를 멈추려면 `Stop agent`를 누릅니다.

## Stop signal 해제

master에서 emergency stop을 보낸 뒤 해당 PC의 stop signal을 지우려면 `Clear stop`을 누릅니다.

## 주의

화면 캡처와 UI 자동화는 interactive desktop session에서 실행되어야 합니다. Windows service session 0에서 실행하면 실제 사용자 화면을 캡처하지 못할 수 있습니다.
