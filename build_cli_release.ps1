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
$appDir = Join-Path $distRoot "SUNNBT_Hotspot"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 Python。请在项目根目录创建 .venv，或将 python.exe 加入 PATH。"
}

Push-Location $sourceRoot
try {
    $buildArgs = @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", $distRoot,
        "--workpath", (Join-Path $projectRoot "build_cli"),
        (Join-Path $sourceRoot "SUNNBT_Hotspot.spec")
    )
    & $python @buildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "命令行版打包失败，退出码: $LASTEXITCODE"
    }

    Copy-Item -LiteralPath (Join-Path $sourceRoot "Docs\CLI发行说明.txt") `
        -Destination (Join-Path $appDir "发行说明.txt") -Force

    foreach ($relativePath in @("SUNNBT_Hotspot.exe", "_internal", "发行说明.txt")) {
        if (-not (Test-Path -LiteralPath (Join-Path $appDir $relativePath))) {
            throw "命令行发行文件缺失: $relativePath"
        }
    }
    if (-not (Get-ChildItem -LiteralPath (Join-Path $appDir "_internal") -Filter "python*.dll")) {
        throw "命令行发行包缺少 Python 运行库。"
    }
    Write-Output "CLI_RELEASE_READY: $appDir"
}
finally {
    Pop-Location
}
