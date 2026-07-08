# 상태 모니터링과 캡처

## 상태 새로고침

1. master PC에서 `RigFtpCommander.exe`를 실행합니다.
2. `Monitor & Run` 탭을 엽니다.
3. `Slave Monitor > Refresh status`를 누릅니다.
4. 상태표의 `Alias`, `Node`, `State`, `Current job`, `Updated`, `Message`를 확인합니다.

## 결과 로그 보기

1. `Slave Monitor > Node`에 node id 또는 alias를 입력합니다.
2. `Refresh results`를 누릅니다.
3. 하단 log에서 최신 결과를 확인합니다.

## screenshot 요청

방법 1:

1. 상태표에서 slave row를 더블클릭합니다.
2. slave가 전체 화면을 캡처해 FTP에 올립니다.
3. master가 PNG를 열어 보여줍니다.

방법 2:

1. `Slave Monitor`에서 node를 선택하거나 입력합니다.
2. `View screenshot`을 누릅니다.

## Excel export

1. `Slave Monitor > More`를 엽니다.
2. `Export Excel`을 누릅니다.
3. `.xlsx` 저장 위치를 선택합니다.

## 오래된 파일 정리

1. `Slave Monitor > More`를 엽니다.
2. `Clean old files`를 누릅니다.

retention 개수는 `Connection Setup`의 `Keep results`, `Keep logs`, `Keep archive`, `Keep screens`에서 조정합니다.

## Emergency stop

1. `Run on Slaves > Target`에 대상 slave를 입력합니다.
2. `Emergency stop`을 누릅니다.
3. 확인창에서 승인합니다.

stop signal을 해제하려면 `Run on Slaves > More > Clear stop`을 사용합니다.
