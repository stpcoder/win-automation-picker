# SEQ·COM·중단 복구

## SEQ 오류

다음을 먼저 확인합니다.

- 명령 사이에 `;`가 있는지
- 한 줄 형식에서 `;` 뒤에 불필요한 공백이 없는지
- Bootloader에서 BL2, LK, OS로 이동하기 위한 `exit` 횟수가 맞는지
- 필요한 `clk.sh`, 온도, VDD 설정이 명령보다 먼저 있는지
- Grid를 나타내는 `#` 구간이 누락되지 않았는지
- SEQ 이름이 테스트 명명 규칙과 맞는지

`오류 검사 · 내보내기`가 PASS여도 실제 SoC 명령 지원 여부는 연결된 실장기에서 한 대씩 먼저 확인합니다.

## COM을 열 수 없음

한 COM은 한 프로그램만 열 수 있습니다.

1. 직접 COM 방식이면 해당 실장기의 SK Commander Console 연결을 닫습니다.
2. SK Commander 방식이면 별도 Console 프로그램과 직접 COM 연결을 닫습니다.
3. `이 PC COM 대조`로 현재 COM 번호를 확인합니다.
4. USB를 다시 연결한 경우 HWID 또는 USB Serial이 같은지 확인합니다.
5. Baud rate를 확인합니다.

SK Commander 사용 방식과 직접 COM 방식을 같은 실장기에서 동시에 실행하지 않습니다.

![직접 COM 확인 화면](../assets/screenshots/12-four-channel-console.png)

## 중지 후 새 테스트가 시작되지 않음

1. 실행 기록에서 중지 시점을 확인합니다.
2. `이 실장기 PC > 더보기 > 긴급 중단 신호 해제`를 누릅니다.
3. 직접 COM 사용 중이었다면 Console에서 Enter를 보내 현재 프롬프트를 확인합니다.
4. SK Commander 사용 중이었다면 Stop 상태와 버튼 활성 상태를 확인합니다.
5. 필요한 경우 Reset 또는 Power Reset을 수행한 뒤 BL1/BL2/LK/OS를 확인합니다.
6. 실행표를 새로 불러온 뒤 다시 시작합니다.

## 텍스트 입력이 되지 않음

- 입력 블록의 대상 유형이 입력 칸인지 확인합니다.
- `기존값 지우기` 설정을 확인합니다.
- `한 번에 입력`이 불안정하면 입력 방법을 `한 글자씩 입력`으로 바꿉니다.
- 영문과 숫자만 허용되는 Console에는 한 글자씩 전송하는 직접 COM 방식을 사용합니다.
- 실장기별 입력값이 빈 값인지 실행표에서 확인합니다.

## Binary 업데이트가 시작되지 않음

- Qualcomm은 물리 Download 스위치 준비 여부를 확인합니다.
- MediaTek은 Preloader 진입 명령과 반복 횟수를 확인합니다.
- Format + Download는 별도 확인 절차를 거친 뒤 실행합니다.
- XML과 참조 파일 전체 검사가 PASS인지 확인합니다.
- Binary 업데이트 중에는 해당 실장기의 Console과 다른 다운로드 프로그램을 함께 사용하지 않습니다.

![Binary 업데이트 확인](../assets/screenshots/13-binary-update.png)
