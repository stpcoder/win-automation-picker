# AE Workbench 사용 매뉴얼

AE Workbench는 SEQ Generator, Win Automation Picker와 FTP Rig 운영을 연결합니다. Windows 프로그램의 버튼, 입력칸, 상태 component를 Scratch 매크로로 만들고 검증된 SEQ와 함께 여러 PC/CH에 배포할 수 있습니다.

이 문서는 GitBook처럼 좌측 목차와 검색을 기준으로 읽도록 구성되어 있습니다.

## 전체 작업 흐름

<div class="workflow-grid">
  <div class="workflow-card">
    <strong>1. SEQ 준비</strong>
    <span>목표 Temp/VDD와 Grid를 설정하고 오류 검사 후 Rig package를 빌드합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>2. 매크로 녹화</strong>
    <span><code>연속 녹화 시작</code> 후 SK Commander의 클릭, 입력, 키를 기록합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>3. Scratch 설계</strong>
    <span>블록을 끌어 반복, 조건, 모니터링 흐름을 만들고 이름 붙인 Rig 버튼으로 등록합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>4. 검증과 업로드</strong>
    <span>SEQ/source와 Scratch/Python hash를 확인하고 두 파일을 FTP에 함께 올립니다.</span>
  </div>
  <div class="workflow-card">
    <strong>5. 실행과 모니터링</strong>
    <span>PC/slot/CH별 값을 배정해 전송하고 Grid 진행, 결과, screenshot을 확인합니다.</span>
  </div>
</div>

## 어떤 프로그램을 쓰면 되나

| 프로그램 | 용도 |
| --- | --- |
| `AEWorkbench.exe` | SEQ, Scratch, 검증, 업로드, 실행표와 모니터링을 연결한 권장 통합 앱 |
| `WinAutomationPicker.exe` | 매크로 생성, 블록 디자인, Python export, FTP 업로드 |
| `RigFtpCommander.exe` | GUI 기반 master/slave 운영, 상태 모니터링, screenshot 요청 |
| `RigFtpCli.exe` | 고급 사용자용 FTP master/slave CLI |
| `RigCommander.exe` | 터미널 기반 실장기/COM/원격 명령 실행 CLI |

## 추천 순서

1. 처음에는 [AE Workbench 통합 흐름](ae-workbench.md)을 따라 한 작업을 끝까지 만듭니다.
2. 세부 녹화 방식은 [기본 매크로 만들기](macro-builder/basic-flow.md)를 확인합니다.
3. 반복/조건/모니터링은 [블록 디자인](macro-builder/block-designer.md)을 확인합니다.
4. 여러 PC 운영은 [FTP master/slave 개요](rig-ftp/overview.md)를 확인합니다.
5. 문제가 생기면 [문제 해결](troubleshooting.md)을 확인합니다.

현재 구현 범위와 남은 기능은 [UX 완성도와 지원 범위](ux-readiness.md)에서 확인할 수 있습니다.

!!! warning "중요"
    이 도구는 native Windows UI Automation 대상에 맞춰져 있습니다. 브라우저 DOM, 게임, canvas/custom rendering UI는 UIA 정보가 부족할 수 있습니다.
