$ErrorActionPreference = "Stop"
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $sourceRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = $pythonCommand.Source
    }
}
$distRoot = Join-Path $projectRoot "BUILD"
$appDir = Join-Path $distRoot "SUNNBT_Hotspot_GUI"
$toolsDir = Join-Path $appDir "Tools"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 Python。请在项目根目录创建 .venv，或将 python.exe 加入 PATH。"
}

Push-Location $sourceRoot
try {
    $mainBuildArgs = @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", $distRoot,
        "--workpath", (Join-Path $projectRoot "build_gui\portable_main_work"),
        (Join-Path $sourceRoot "SUNNBT_Hotspot_GUI.spec")
    )
    & $python @mainBuildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "主程序打包失败，退出码: $LASTEXITCODE"
    }

    $videosDir = Join-Path $appDir "Videos"
    $docsDir = Join-Path $appDir "Docs"
    New-Item -ItemType Directory -Force -Path $videosDir, $docsDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot "Videos\SC12000_01_Idle_01.webm") -Destination $videosDir -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "Videos\SC12000_01_Idle_01_preview.png") -Destination $videosDir -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "Docs\发行说明.txt") -Destination $docsDir -Force

    foreach ($internalCategory in @("Videos", "Docs")) {
        $internalPath = Join-Path $appDir "_internal\$internalCategory"
        if (Test-Path -LiteralPath $internalPath) {
            Remove-Item -LiteralPath $internalPath -Recurse -Force
        }
    }

    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $managerBuildArgs = @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", $toolsDir,
        "--workpath", (Join-Path $projectRoot "build_gui\portable_manager_work"),
        (Join-Path $sourceRoot "SUNNBT_Client_Manager.spec")
    )
    & $python @managerBuildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "设备管理器打包失败，退出码: $LASTEXITCODE"
    }

    $requiredPaths = @(
        "SUNNBT_Hotspot_GUI.exe",
        "_internal",
        "Videos\SC12000_01_Idle_01.webm",
        "Videos\SC12000_01_Idle_01_preview.png",
        "Docs\发行说明.txt",
        "Tools\client_manager\SUNNBT_Client_Manager.exe",
        "Tools\client_manager\_internal"
    )
    foreach ($relativePath in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $appDir $relativePath))) {
            throw "发行文件缺失: $relativePath"
        }
    }
    if (-not (Get-ChildItem -LiteralPath (Join-Path $appDir "_internal") -Filter "python*.dll")) {
        throw "发行包缺少 Python 运行库。"
    }
    Write-Output "PORTABLE_RELEASE_READY: $appDir"
}
finally {
    Pop-Location
}
