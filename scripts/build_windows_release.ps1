param(
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

function Invoke-PyInstallerBuild {
    param(
        [string]$Name,
        [string]$EntryPoint,
        [string]$Icon,
        [switch]$Console,
        [string[]]$HiddenImports = @()
    )

    $arguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        $(if ($Console) { "--console" } else { "--windowed" }),
        "--name", $Name,
        "--distpath", $OutputDirectory,
        "--icon", $Icon,
        "--add-data", "src/win_automation_picker/assets;win_automation_picker/assets"
    )
    foreach ($hiddenImport in $HiddenImports) {
        $arguments += @("--hidden-import", $hiddenImport)
    }
    $arguments += $EntryPoint

    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed for $Name with exit code $LASTEXITCODE"
    }
}

function Assert-GuiStarts {
    param([string]$Path)

    $process = Start-Process $Path -PassThru
    try {
        Start-Sleep -Seconds 5
        if ($process.HasExited) {
            throw "$Path exited during startup with code $($process.ExitCode)"
        }
    }
    finally {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }
    }
}

Remove-Item -Recurse -Force $OutputDirectory -ErrorAction SilentlyContinue

$rigHiddenImports = @("serial", "serial.tools.list_ports")
Invoke-PyInstallerBuild `
    -Name "AEWorkbench" `
    -EntryPoint "src/win_automation_picker/ae_workbench.py" `
    -Icon "src/win_automation_picker/assets/rig_commander.ico" `
    -HiddenImports $rigHiddenImports
Invoke-PyInstallerBuild `
    -Name "AutomationBuilder" `
    -EntryPoint "src/win_automation_picker/__main__.py" `
    -Icon "src/win_automation_picker/assets/win_automation_picker.ico"
Invoke-PyInstallerBuild `
    -Name "FixtureControlCli" `
    -EntryPoint "src/win_automation_picker/rig_commander.py" `
    -Icon "src/win_automation_picker/assets/rig_commander.ico" `
    -Console `
    -HiddenImports $rigHiddenImports
Invoke-PyInstallerBuild `
    -Name "FixtureCommunication" `
    -EntryPoint "src/win_automation_picker/rig_ftp_commander.py" `
    -Icon "src/win_automation_picker/assets/rig_commander.ico" `
    -HiddenImports $rigHiddenImports
Invoke-PyInstallerBuild `
    -Name "FixtureCommunicationCli" `
    -EntryPoint "src/win_automation_picker/rig_ftp_cli.py" `
    -Icon "src/win_automation_picker/assets/rig_commander.ico" `
    -Console

$executables = @(
    "AEWorkbench.exe",
    "AutomationBuilder.exe",
    "FixtureControlCli.exe",
    "FixtureCommunication.exe",
    "FixtureCommunicationCli.exe"
)
foreach ($name in $executables) {
    $path = Join-Path $OutputDirectory $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Expected Windows executable was not produced: $path"
    }
}

Assert-GuiStarts (Join-Path $OutputDirectory "AEWorkbench.exe")
Assert-GuiStarts (Join-Path $OutputDirectory "AutomationBuilder.exe")
Assert-GuiStarts (Join-Path $OutputDirectory "FixtureCommunication.exe")

& (Join-Path $OutputDirectory "FixtureControlCli.exe") --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "FixtureControlCli.exe --help failed" }
& (Join-Path $OutputDirectory "FixtureControlCli.exe") device system-check
if ($LASTEXITCODE -ne 0) { throw "FixtureControlCli.exe device system-check failed" }
& (Join-Path $OutputDirectory "FixtureControlCli.exe") firmware --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "FixtureControlCli.exe firmware --help failed" }
& (Join-Path $OutputDirectory "FixtureControlCli.exe") device raw-write --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "FixtureControlCli.exe device raw-write --help failed" }
& (Join-Path $OutputDirectory "FixtureCommunicationCli.exe") --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "FixtureCommunicationCli.exe --help failed" }

$sourceCommit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { (git rev-parse HEAD).Trim() }
$fileRows = foreach ($name in $executables) {
    $path = Join-Path $OutputDirectory $name
    $item = Get-Item -LiteralPath $path
    [ordered]@{
        name = $name
        size = $item.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    }
}
$manifest = [ordered]@{
    schema = "ae-workbench-windows-release/v1"
    source_commit = $sourceCommit
    architecture = "x86_64"
    files = @($fileRows)
    external_firmware_tools = [ordered]@{
        bundled = $false
        contract_scope = "cli-parser-dry-run"
        qdl = [ordered]@{
            repository = "https://github.com/linux-msm/qdl"
            source_commit = "a00d81bc639908875862582f0d3cb0775d92e269"
            expected_version = "v2.7-44-ga00d81b"
        }
        genio_tools = [ordered]@{
            repository = "https://gitlab.com/mediatek/aiot/bsp/genio-tools"
            source_commit = "3c6e7de6605fb58bb2cae4acf65a148a979b58e1"
            expected_version = "1.7.1"
        }
    }
}
$manifestPath = Join-Path $OutputDirectory "windows-release-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

$archivePath = Join-Path $OutputDirectory "AEWorkbench-Windows-x64.zip"
$archiveInputs = @($executables | ForEach-Object { Join-Path $OutputDirectory $_ })
$archiveInputs += $manifestPath
Compress-Archive -LiteralPath $archiveInputs -DestinationPath $archivePath -CompressionLevel Optimal

Write-Output "Windows release bundle: $archivePath"
