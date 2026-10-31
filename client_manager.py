# -*- coding: utf-8 -*-
"""Windows 10 Mobile Hotspot client monitor."""

import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk


BG = "#0a0f0b"
PANEL = "#0e1611"
GREEN = "#3dff86"
TEXT = "#d6f7e3"
TEXT_DIM = "#8baa98"
RED = "#ff5c55"
POLL_INTERVAL_MS = 1200

QUERY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$null = Add-Type -AssemblyName System.Runtime.WindowsRuntime
$networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$profile = $networkInformation::GetInternetConnectionProfile()
if ($null -eq $profile) {
    throw 'Windows 没有检测到可用于管理热点的 Internet 连接。'
}
$manager = $tetheringManagerType::CreateFromConnectionProfile($profile)
$clients = @()
$neighbors = @{}
$hotspotAdapter = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceDescription -match 'Wi-Fi Direct Virtual Adapter' } |
    Sort-Object @{ Expression = { if ($_.Status -eq 'Up') { 0 } else { 1 } } } |
    Select-Object -First 1
if ($null -ne $hotspotAdapter) {
    foreach ($neighbor in @(Get-NetNeighbor -InterfaceIndex $hotspotAdapter.InterfaceIndex -ErrorAction SilentlyContinue)) {
        $key = ([string]$neighbor.LinkLayerAddress -replace '[-:.]', '').ToUpperInvariant()
        if ($key) { $neighbors[$key] = [string]$neighbor.IPAddress }
    }
}
foreach ($client in @($manager.GetTetheringClients())) {
    $mac = [string]$client.MacAddress
    $key = ($mac -replace '[-:.]', '').ToUpperInvariant()
    $names = @()
    foreach ($hostName in $client.HostNames) {
        if ($hostName.DisplayName) { $names += [string]$hostName.DisplayName }
    }
    $clients += [pscustomobject]@{
        mac = $mac
        hostname = ($names -join ', ')
        ip = if ($neighbors.ContainsKey($key)) { $neighbors[$key] } else { '' }
    }
}
$received = $null
$sent = $null
if ($null -ne $hotspotAdapter) {
    $networkInterface = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.Name -eq $hotspotAdapter.Name } |
        Select-Object -First 1
    if ($null -eq $networkInterface) {
        throw "无法找到热点网卡的流量计数器：$($hotspotAdapter.Name)"
    }
    $statistics = $networkInterface.GetIPStatistics()
    $received = [long]$statistics.BytesReceived
    $sent = [long]$statistics.BytesSent
}
$result = @{
    state = [string]$manager.TetheringOperationalState
    profile = [string]$profile.ProfileName
    clients = @($clients)
    adapter = if ($null -ne $hotspotAdapter) { [string]$hotspotAdapter.Name } else { '' }
    received_bytes = $received
    sent_bytes = $sent
}
[Console]::WriteLine(($result | ConvertTo-Json -Compress -Depth 5))
"""

STOP_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$null = Add-Type -AssemblyName System.Runtime.WindowsRuntime
$networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
$tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
$profile = $networkInformation::GetInternetConnectionProfile()
if ($null -eq $profile) {
    throw 'Windows 没有检测到可用于管理热点的 Internet 连接。'
}
$manager = $tetheringManagerType::CreateFromConnectionProfile($profile)
if ([string]$manager.TetheringOperationalState -ne 'Off') {
    $null = $manager.StopTetheringAsync()
}
$stopped = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 250
    $state = [string]$manager.TetheringOperationalState
    if ($state -eq 'Off') {
        $stopped = $true
        break
    }
}
if (-not $stopped) { throw "热点未能停止，当前状态：$state" }
[Console]::WriteLine('STOPPED')
"""


def app_root() -> Path:
    executable_dir = Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False) and executable_dir.name.lower() == "client_manager":
        if executable_dir.parent.name.lower() == "tools":
            return executable_dir.parent.parent
        return executable_dir.parent
    if getattr(sys, "frozen", False):
        return executable_dir
    return Path(__file__).resolve().parent


def configure_logging() -> None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    candidates = (
        app_root() / "Logs" / "client_manager.log",
        local_app_data / "SUNNBT_Hotspot" / "Logs" / "client_manager.log",
    )
    for log_path in candidates:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError:
            continue
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    if getattr(sys, "frozen", False):
        target = str(Path(sys.executable).resolve())
        params = ""
    else:
        target = sys.executable
        params = subprocess.list2cmdline([str(Path(__file__).resolve())])
    try:
        working_directory = app_root()
        if not os.access(working_directory, os.W_OK):
            working_directory = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params, str(working_directory), 1
        )
    except OSError:
        logging.exception("无法请求管理员权限。")
        return False
    return result > 32


def run_powershell(script: str, timeout: int = 12) -> str:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=0x08000000,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"PowerShell 退出码 {completed.returncode}")
    return completed.stdout.strip()


def format_speed(bytes_per_second: float | None) -> str:
    if bytes_per_second is None:
        return "计算中…"
    value = max(0.0, bytes_per_second)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B/s"


class ClientManager(tk.Tk):
    def __init__(self, selftest: bool = False):
        super().__init__()
        self.title("SUNNBT 热点设备管理")
        self.geometry("920x600")
        self.minsize(760, 500)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.last_sample: tuple[float, int, int] | None = None
        self.download_speed: float | None = None
        self.upload_speed: float | None = None
        self._build_ui()
        self.after(100, self._poll_events)
        if selftest:
            self.after(200, self._run_selftest)
        else:
            self.after(200, self.refresh)
            self.after(POLL_INTERVAL_MS, self._schedule_refresh)

    def _run_selftest(self) -> None:
        self._show_snapshot(
            {
                "state": "On",
                "profile": "Self-test",
                "adapter": "Wi-Fi Direct Virtual Adapter",
                "clients": [
                    {
                        "mac": "00-11-22-33-44-55",
                        "hostname": "Self-test device",
                        "ip": "192.168.137.2",
                    }
                ],
                "received_bytes": 1000,
                "sent_bytes": 2000,
            }
        )
        self.last_sample = (time.monotonic() - 1, 0, 0)
        self._update_speeds(
            {"received_bytes": 1024, "sent_bytes": 4096}
        )
        logging.info("设备管理 GUI 自检通过（未执行任何网络操作）。")
        self.after(500, self.destroy)

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill=tk.X, padx=20, pady=(18, 8))
        tk.Label(
            header,
            text="SUNNBT  /  热点设备管理",
            bg=BG,
            fg=GREEN,
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        self.status = tk.Label(
            header, text="正在读取热点状态…", bg=BG, fg=TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
        )
        self.status.pack(anchor="w", pady=(4, 0))

        summary = tk.Frame(self, bg=PANEL)
        summary.pack(fill=tk.X, padx=20, pady=8)
        self.download_label = self._metric(summary, "热点总下载", "计算中…")
        self.upload_label = self._metric(summary, "热点总上传", "计算中…")
        self.clients_label = self._metric(summary, "在线设备", "0")

        table_frame = tk.Frame(self, bg=BG)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 0))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#17251c",
            foreground=GREEN,
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#1d7a47")])
        columns = ("hostname", "ip", "mac", "download", "upload")
        self.devices = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "hostname": ("设备名称", 180),
            "ip": ("IP 地址", 130),
            "mac": ("MAC 地址", 180),
            "download": ("设备下载速度", 150),
            "upload": ("设备上传速度", 150),
        }
        for column, (heading, width) in headings.items():
            self.devices.heading(column, text=heading)
            self.devices.column(column, width=width, minwidth=90, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.devices.yview
        )
        self.devices.configure(yscrollcommand=scrollbar.set)
        self.devices.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        note = tk.Label(
            self,
            text=(
                "Windows 10 未提供单设备踢出/封禁、限速或设备级实时流量计数接口；"
                "表格中的设备速度因此不可用，上方为热点网卡总流量。"
            ),
            bg=BG,
            fg=TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
            wraplength=860,
            justify=tk.LEFT,
        )
        note.pack(fill=tk.X, padx=20, pady=(12, 4))

        controls = tk.Frame(self, bg=BG)
        controls.pack(fill=tk.X, padx=20, pady=(8, 18))
        self.refresh_button = tk.Button(
            controls, text="立即刷新", command=self.refresh,
            bg=PANEL, fg=GREEN, activebackground="#17251c",
            activeforeground=GREEN, relief=tk.FLAT, padx=14, pady=8,
            cursor="hand2",
        )
        self.refresh_button.pack(side=tk.LEFT)
        self.stop_button = tk.Button(
            controls, text="停止热点（断开全部设备）", command=self.stop_hotspot,
            bg="#351713", fg=RED, activebackground="#57231e",
            activeforeground=RED, relief=tk.FLAT, padx=14, pady=8,
            cursor="hand2",
        )
        self.stop_button.pack(side=tk.LEFT, padx=(10, 0))
        self.time_label = tk.Label(
            controls, text="", bg=BG, fg=TEXT_DIM,
            font=("Microsoft YaHei UI", 8),
        )
        self.time_label.pack(side=tk.RIGHT)

    @staticmethod
    def _metric(parent: tk.Widget, title: str, value: str) -> tk.Label:
        frame = tk.Frame(parent, bg=PANEL)
        frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14, pady=10)
        tk.Label(
            frame, text=title, bg=PANEL, fg=TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        label = tk.Label(
            frame, text=value, bg=PANEL, fg=TEXT,
            font=("Consolas", 14, "bold"),
        )
        label.pack(anchor="w", pady=(3, 0))
        return label

    def refresh(self) -> None:
        if self.busy:
            return
        self._start_worker("refresh", QUERY_SCRIPT)

    def _schedule_refresh(self) -> None:
        self.refresh()
        self.after(POLL_INTERVAL_MS, self._schedule_refresh)

    def stop_hotspot(self) -> None:
        if self.busy:
            return
        if not messagebox.askyesno(
            "确认停止热点",
            "此操作会停止整个移动热点并断开所有设备，不是单设备踢出。",
            parent=self,
        ):
            return
        self._start_worker("stop", STOP_SCRIPT)

    def _start_worker(self, operation: str, script: str) -> None:
        self.busy = True
        self.refresh_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.DISABLED)
        if operation == "stop":
            self.status.configure(text="正在停止热点…", fg=TEXT_DIM)
        threading.Thread(
            target=self._worker, args=(operation, script), daemon=True
        ).start()

    def _worker(self, operation: str, script: str) -> None:
        try:
            output = run_powershell(script, timeout=15 if operation == "stop" else 12)
            payload: object
            if operation == "refresh":
                payload = json.loads(output)
            else:
                payload = output
            self.events.put((operation, payload))
        except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            logging.exception("热点管理操作失败: %s", operation)
            self.events.put(("error", f"{operation}: {exc}"))

    def _poll_events(self) -> None:
        try:
            while True:
                operation, payload = self.events.get_nowait()
                self.busy = False
                self.refresh_button.configure(state=tk.NORMAL)
                self.stop_button.configure(state=tk.NORMAL)
                if operation == "refresh":
                    self._show_snapshot(payload)
                elif operation == "stop":
                    self.last_sample = None
                    self.download_speed = None
                    self.upload_speed = None
                    self.status.configure(text="热点已停止，所有设备已断开。", fg=GREEN)
                    self.download_label.configure(text="—")
                    self.upload_label.configure(text="—")
                    self.clients_label.configure(text="0")
                    for item in self.devices.get_children():
                        self.devices.delete(item)
                else:
                    self.status.configure(text=f"操作失败：{payload}", fg=RED)
        except queue.Empty:
            pass
        self.after(150, self._poll_events)

    def _show_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, dict):
            self.status.configure(text="热点返回了无法识别的数据。", fg=RED)
            return
        state = snapshot.get("state", "Unknown")
        clients = snapshot.get("clients", [])
        if not isinstance(clients, list):
            self.status.configure(text="热点客户端列表格式无效。", fg=RED)
            return
        profile = snapshot.get("profile") or "未知连接"
        adapter = snapshot.get("adapter") or "未找到 Wi-Fi Direct 网卡"
        self.status.configure(
            text=f"热点状态：{state}  |  共享连接：{profile}  |  热点网卡：{adapter}",
            fg=GREEN if state == "On" else TEXT_DIM,
        )
        self._update_speeds(snapshot)
        self.clients_label.configure(text=str(len(clients)))
        existing = set(self.devices.get_children())
        current = set()
        for index, client in enumerate(clients):
            if not isinstance(client, dict):
                continue
            mac = str(client.get("mac") or "")
            item_id = mac or f"unknown-{index}"
            current.add(item_id)
            values = (
                client.get("hostname") or "未知设备",
                client.get("ip") or "—",
                mac or "—",
                "系统未提供",
                "系统未提供",
            )
            if item_id in existing:
                self.devices.item(item_id, values=values)
            else:
                self.devices.insert("", tk.END, iid=item_id, values=values)
        for item_id in existing - current:
            self.devices.delete(item_id)
        self.time_label.configure(text=f"最后更新：{time.strftime('%H:%M:%S')}")

    def _update_speeds(self, snapshot: dict) -> None:
        received = snapshot.get("received_bytes")
        sent = snapshot.get("sent_bytes")
        now = time.monotonic()
        if isinstance(received, int) and isinstance(sent, int):
            if self.last_sample is not None:
                last_time, last_received, last_sent = self.last_sample
                elapsed = now - last_time
                if elapsed > 0:
                    self.upload_speed = max(0, received - last_received) / elapsed
                    self.download_speed = max(0, sent - last_sent) / elapsed
            self.last_sample = (now, received, sent)
            self.download_label.configure(text=format_speed(self.download_speed))
            self.upload_label.configure(text=format_speed(self.upload_speed))
        else:
            self.last_sample = None
            self.download_speed = None
            self.upload_speed = None
            self.download_label.configure(text="不可用")
            self.upload_label.configure(text="不可用")


def main() -> int:
    configure_logging()
    selftest = "--selftest" in sys.argv
    if os.name != "nt":
        messagebox.showerror("不支持的系统", "热点设备管理仅支持 Windows。")
        return 1
    if not selftest and not is_admin():
        if not relaunch_as_admin():
            messagebox.showerror(
                "权限不足",
                "无法发起管理员权限请求，请以管理员身份运行。",
            )
            return 1
        return 0
    try:
        app = ClientManager(selftest=selftest)
        app.mainloop()
    except tk.TclError:
        logging.exception("设备管理窗口初始化失败。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
