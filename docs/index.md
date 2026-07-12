# AE Workbench 사용 매뉴얼

AE Workbench는 Mobile DRAM AE가 프로그램별 자동화를 미리 준비하고, 여러 원격 Rig PC에
PC/CH별 시험값을 배정한 뒤 한 Master 화면에서 실행·모니터링하는 도구입니다.

## 업무 주기

<div class="workflow-grid">
  <div class="workflow-card">
    <strong>1. 자동화 준비</strong>
    <span>프로그램이나 시험 절차가 바뀔 때 SEQ와 Scratch 매크로를 한 번 작성합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>2. Rig 설정</strong>
    <span>PC를 추가할 때 FTP, Node, 자유 CH, Slave Agent를 한 번 설정합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>3. 오늘 실행</strong>
    <span>저장된 자동화를 선택하고 PC/CH별 변수만 확인해 일괄 실행합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>4. 모니터링·반복</strong>
    <span>Grid, PASS/FAIL, 화면과 로그를 확인하고 실패 행만 다시 실행합니다.</span>
  </div>
</div>

가장 먼저 [Mobile DRAM AE 업무 흐름](daily-workflow.md)을 읽습니다. 화면별 모든 기능을
외우는 대신 본인이 지금 `준비`, `Rig 설정`, `일일 실행`, `모니터링` 중 어디에 있는지만
판단하면 됩니다.

## 어떤 프로그램을 쓰면 되나

| 프로그램 | 용도 |
| --- | --- |
| `AEWorkbench.exe` | SEQ, Scratch, 검증, 업로드, 실행표와 모니터링을 연결한 권장 통합 앱 |
| `WinAutomationPicker.exe` | 매크로 생성, 블록 디자인, Python export, FTP 업로드 |
| `RigFtpCommander.exe` | GUI 기반 master/slave 운영, 상태 모니터링, screenshot 요청 |
| `RigFtpCli.exe` | 고급 사용자용 FTP master/slave CLI |
| `RigCommander.exe` | 터미널 기반 실장기/COM/원격 명령 실행 CLI |

## 추천 순서

1. [업무 흐름](daily-workflow.md)에서 최초 설정과 일일 업무를 구분합니다.
2. 프로그램 자동화 담당자는 [기본 매크로 만들기](macro-builder/basic-flow.md)와
   [블록 디자인](macro-builder/block-designer.md)을 확인합니다.
3. Rig 담당자는 [FTP master/slave 개요](rig-ftp/overview.md)를 확인합니다.
4. 일일 운영자는 `오늘 작업`과 [상태 모니터링](rig-ftp/monitoring.md)만 사용합니다.
5. 문제가 생기면 [문제 해결](troubleshooting.md)을 확인합니다.

현재 구현 범위와 남은 기능은 [UX 완성도와 지원 범위](ux-readiness.md)에서 확인할 수 있습니다.

!!! warning "중요"
    이 도구는 native Windows UI Automation 대상에 맞춰져 있습니다. 브라우저 DOM, 게임, canvas/custom rendering UI는 UIA 정보가 부족할 수 있습니다.
