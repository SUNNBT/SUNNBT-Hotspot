import ctypes
import codecs
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys


SSID = "SUNNBT111"
PASSWORD = "SUNNBT111"


def application_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


class Utf8BomRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write("\ufeff")
            stream.flush()
        return stream


def configure_logging() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    candidates = (
        application_path().parent / "Logs" / "start_hotspot.log",
        local_app_data / "SUNNBT_Hotspot" / "Logs" / "start_hotspot.log",
    )
    log_path = None
    file_handler = None
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            if candidate.exists():
                existing_log = candidate.read_bytes()
                if not existing_log.startswith(codecs.BOM_UTF8):
                    candidate.write_bytes(codecs.BOM_UTF8 + existing_log)
            file_handler = Utf8BomRotatingFileHandler(
                candidate,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
        except OSError:
            continue
        log_path = candidate
        break
    if log_path is None or file_handler is None:
        raise OSError("无法在发行目录或用户数据目录中创建日志文件。")

    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    return log_path


POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$failedAdapters = @()
$adapterScanWarning = $null
$disabledAdapters = @()
try {
    $disabledAdapters = @(Get-NetAdapter -IncludeHidden -ErrorAction Stop |
        Where-Object { $_.Status -eq 'Disabled' }
    )
}
catch {
    $adapterScanWarning = [string]$_.Exception.Message
    Write-Output "[警告] 无法枚举网卡，将继续启动热点: $adapterScanWarning"
}

foreach ($adapter in $disabledAdapters) {
    try {
        Enable-NetAdapter -InterfaceIndex $adapter.InterfaceIndex -Confirm:$false -ErrorAction Stop
        $enabled = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 250
            $check = Get-NetAdapter -InterfaceIndex $adapter.InterfaceIndex -IncludeHidden -ErrorAction Stop
            if ($check.Status -ne 'Disabled') {
                $enabled = $true
                break
            }
        }
        if (-not $enabled) {
            throw '启用请求已发送，但网卡仍处于禁用状态'
        }
        Write-Output "已启用网卡并确认状态: $($check.Name) ($($check.Status))"
    }
    catch {
        $failedAdapters += "$($adapter.Name): $($_.Exception.Message)"
    }
}

if ($disabledAdapters.Count -eq 0) {
    Write-Output '没有发现处于禁用状态的网卡。'
}

$null = Add-Type -AssemblyName System.Runtime.WindowsRuntime
$networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$connectionProfile = $networkInformation::GetInternetConnectionProfile()
if ($null -eq $connectionProfile) {
    throw 'Windows 没有检测到可用于共享的 Internet 连接。'
}

$tetheringManager = $tetheringManagerType::CreateFromConnectionProfile($connectionProfile)
$accessPointConfiguration = $tetheringManager.GetCurrentAccessPointConfiguration()
$configurationMatches = (
    $accessPointConfiguration.Ssid -ceq '__SSID__' -and
    $accessPointConfiguration.Passphrase -ceq '__PASSWORD__'
)

if (-not $configurationMatches) {
    if ([string]$tetheringManager.TetheringOperationalState -eq 'On') {
        $null = $tetheringManager.StopTetheringAsync()
        $stopped = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 500
            if ([string]$tetheringManager.TetheringOperationalState -eq 'Off') {
                $stopped = $true
                break
            }
        }
        if (-not $stopped) {
            throw '需要修改热点配置，但 Windows 移动热点未能先停止。'
        }
    }

    $accessPointConfiguration.Ssid = '__SSID__'
    $accessPointConfiguration.Passphrase = '__PASSWORD__'
    $null = $tetheringManager.ConfigureAccessPointAsync($accessPointConfiguration)

    $configurationMatches = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $currentConfiguration = $tetheringManager.GetCurrentAccessPointConfiguration()
        if (
            $currentConfiguration.Ssid -ceq '__SSID__' -and
            $currentConfiguration.Passphrase -ceq '__PASSWORD__'
        ) {
            $configurationMatches = $true
            break
        }
    }
}

if (-not $configurationMatches) {
    throw 'Windows 移动热点名称或密码未能设置为请求的值。'
}
$currentConfiguration = $tetheringManager.GetCurrentAccessPointConfiguration()
$connectionProfileName = $connectionProfile.ProfileName
Write-Output "共享 Internet 连接: $connectionProfileName"
Write-Output "移动热点名称已核实: $($currentConfiguration.Ssid)"
Write-Output '移动热点密码已核实（不记录密码内容）。'

if ([string]$tetheringManager.TetheringOperationalState -ne 'On') {
    $null = $tetheringManager.StartTetheringAsync()
}

$hotspotStarted = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    $tetheringState = [string]$tetheringManager.TetheringOperationalState
    if ($tetheringState -eq 'On') {
        $hotspotStarted = $true
        break
    }
}
Write-Output "移动热点状态: $tetheringState"
if (-not $hotspotStarted) {
    throw "Windows 移动热点未启动，当前状态: $tetheringState"
}

if ($adapterScanWarning) {
    Write-Output '[警告] 网卡枚举未完成，但热点已成功启动。'
}
if ($failedAdapters.Count -gt 0) {
    Write-Output '[警告] 以下网卡未能启用；不会阻止热点继续运行:'
    $failedAdapters | ForEach-Object { Write-Output "  $_" }
}

Write-Output 'Windows 移动热点已启动并核实。'
""".replace("__SSID__", SSID).replace("__PASSWORD__", PASSWORD)


def main() -> int:
    try:
        log_path = configure_logging()
    except OSError as error:
        print(f"无法创建日志文件: {error}")
        return 1

    logging.info("程序启动，日志文件: %s", log_path)

    if os.name != "nt":
        logging.error("此程序只能在 Windows 上运行。")
        return 1

    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except OSError:
        logging.exception("无法检查管理员权限。")
        return 1

    if not is_admin:
        executable_path = str(application_path())
        arguments = ""
        if not getattr(sys, "frozen", False):
            arguments = subprocess.list2cmdline([executable_path])
        logging.info("当前不是管理员，正在请求管理员权限。")
        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                sys.executable if not getattr(sys, "frozen", False) else executable_path,
                arguments,
                str(Path(executable_path).parent),
                1,
            )
        except OSError:
            logging.exception("请求管理员权限失败。")
            return 1
        if result <= 32:
            logging.error(
                "无法请求管理员权限（ShellExecute 返回值: %s）。"
                "请手动以管理员身份运行。",
                result,
            )
            return 1
        logging.info("已提交管理员权限请求；请在 UAC 弹窗中确认。")
        return 0

    logging.info("已获得管理员权限，开始启用网卡并配置 Windows 移动热点。")
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                POWERSHELL_SCRIPT,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        logging.exception("无法启动 PowerShell。")
        return 1

    for line in result.stdout.splitlines():
        logging.info("PowerShell: %s", line)
        print(line)
    for line in result.stderr.splitlines():
        logging.error("PowerShell: %s", line)
        print(line, file=sys.stderr)

    if result.returncode != 0:
        logging.error("网络配置失败，PowerShell 退出代码: %s", result.returncode)
    else:
        logging.info("网络配置命令执行成功。")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
