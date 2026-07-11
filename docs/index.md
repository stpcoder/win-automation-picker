# Win Automation Picker 사용 매뉴얼

Win Automation Picker는 Windows UI Automation 기반으로 사내 Windows 프로그램의 버튼, 입력칸, 상태 표시 컴포넌트를 잡아 매크로로 만들고, Python 스크립트 또는 FTP master/slave 운영 흐름으로 배포하기 위한 도구입니다.

이 문서는 GitBook처럼 좌측 목차와 검색을 기준으로 읽도록 구성되어 있습니다.

## 전체 작업 흐름

<div class="workflow-grid">
  <div class="workflow-card">
    <strong>1. 대상 잡기</strong>
    <span><code>대상 확인</code>, <code>클릭 녹화</code>, <code>입력 녹화</code>로 Windows UIA 요소를 캡처합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>2. 블록으로 설계</strong>
    <span><code>매크로 만들기</code>에서 블록을 끌어 반복, 조건, 모니터링 흐름을 조합합니다.</span>
  </div>
  <div class="workflow-card">
    <strong>3. 실행/내보내기</strong>
    <span><code>실행</code>, <code>데이터 실행</code>, <code>Python 내보내기</code>로 실행 가능한 흐름을 만듭니다.</span>
  </div>
  <div class="workflow-card">
    <strong>4. 원격 배포</strong>
    <span><code>RigFtpCommander</code>로 FTP를 통해 slave PC에 매크로를 배포하고 상태를 모니터링합니다.</span>
  </div>
</div>

## 어떤 프로그램을 쓰면 되나

| 프로그램 | 용도 |
| --- | --- |
| `WinAutomationPicker.exe` | 매크로 생성, 블록 디자인, Python export, FTP 업로드 |
| `RigFtpCommander.exe` | GUI 기반 master/slave 운영, 상태 모니터링, screenshot 요청 |
| `RigFtpCli.exe` | 고급 사용자용 FTP master/slave CLI |
| `RigCommander.exe` | 터미널 기반 실장기/COM/원격 명령 실행 CLI |

## 추천 순서

1. [설치와 실행](getting-started.md)을 보고 실행 파일을 준비합니다.
2. [기본 매크로 만들기](macro-builder/basic-flow.md)로 클릭/입력 블록을 만들어 봅니다.
3. [블록 디자인](macro-builder/block-designer.md)에서 반복/조건/모니터링 구조를 잡습니다.
4. [FTP master/slave 개요](rig-ftp/overview.md)를 보고 여러 PC 배포 구조를 설정합니다.
5. 문제가 생기면 [문제 해결](troubleshooting.md)을 확인합니다.

!!! warning "중요"
    이 도구는 native Windows UI Automation 대상에 맞춰져 있습니다. 브라우저 DOM, 게임, canvas/custom rendering UI는 UIA 정보가 부족할 수 있습니다.
