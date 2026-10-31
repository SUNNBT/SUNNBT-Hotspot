# -*- coding: utf-8 -*-
"""
SUNNBT Hotspot GUI
仿 VAPE 风格的 Tkinter 界面，两个进度条全部对应真实操作：
  1) 启用所有可见及隐藏的禁用网卡（Enable-NetAdapter，轮询确认状态）
  2) 配置热点名称/密码（WinRT NetworkOperatorTetheringManager）并启动、轮询核实
PowerShell 以 "@@UI " + JSON 行回传真实事件，本文件不做任何假进度。
"""

import ctypes
import codecs
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
from tkinter import messagebox
from logging.handlers import RotatingFileHandler

from PIL import Image, ImageTk

SSID = "SUNNBT111"
PASSWORD = "SUNNBT111"
VIDEO_FILENAME = Path("Videos") / "SC12000_01_Idle_01.webm"
VIDEO_POSTER_FILENAME = Path("Videos") / "SC12000_01_Idle_01_preview.png"
VIDEO_FRAME_SIZE = (800, 506)
VIDEO_FPS = 12

# ---------------- 视觉常量（VAPE 风：近黑底 + 荧光绿） ----------------
BG = "#0a0f0b"          # 窗口底色
PANEL = "#0e1611"       # 面板色
TRACK = "#122a1c"       # 进度条轨道
GREEN = "#3dff86"       # 主强调色
GREEN_BRIGHT = "#a6ffc9"
GREEN_DIM = "#1d7a47"
WARNING = "#ffd166"
TEXT = "#d6f7e3"
TEXT_DIM = "#6fa386"
RED = "#ff5c55"
RED_DIM = "#7a2622"

POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Emit($obj) {
    Write-Output ('@@UI ' + ($obj | ConvertTo-Json -Compress -Depth 5))
}

try {
    # ============ 阶段 1：启用禁用的网卡 ============
    $failedAdapters = @()
    $adapterScanWarning = $null
    $disabled = @()
    try {
        $allAdapters = @(Get-NetAdapter -IncludeHidden -ErrorAction Stop)
        $disabled = @($allAdapters | Where-Object { $_.Status -eq 'Disabled' })
        Emit @{ ev = 'stage1_total'; n = $disabled.Count }

        if ($disabled.Count -eq 0) {
            Emit @{ ev = 'stage1_none' }
        } else {
            $idx = 0
            foreach ($adapter in $disabled) {
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
                    if (-not $enabled) { throw '启用请求已发送，但网卡仍处于禁用状态' }
                    $idx++
                    Emit @{ ev = 'adapter_done'; name = [string]$check.Name; idx = $idx; total = $disabled.Count }
                } catch {
                    $failedAdapters += [string]$adapter.Name
                    Emit @{ ev = 'adapter_fail'; name = [string]$adapter.Name; msg = [string]$_.Exception.Message }
                }
            }
        }
    } catch {
        $adapterScanWarning = [string]$_.Exception.Message
        Emit @{ ev = 'adapter_warning'; msg = "无法枚举网卡，将继续启动热点：$adapterScanWarning" }
    }

    # ============ 阶段 2：配置并启动移动热点 ============
    $null = Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
    $tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]

    $connectionProfile = $networkInformation::GetInternetConnectionProfile()
    if ($null -eq $connectionProfile) {
        throw 'Windows 没有检测到可用于共享的 Internet 连接。'
    }
    Emit @{ ev = 'profile'; name = [string]$connectionProfile.ProfileName }

    $tetheringManager = $tetheringManagerType::CreateFromConnectionProfile($connectionProfile)
    $accessPoint = $tetheringManager.GetCurrentAccessPointConfiguration()
    $cfgMatch = (
        $accessPoint.Ssid -ceq '__SSID__' -and
        $accessPoint.Passphrase -ceq '__PASSWORD__'
    )
    Emit @{ ev = 'config_check'; match = [bool]$cfgMatch; ssid = [string]$accessPoint.Ssid }

    if (-not $cfgMatch) {
        if ([string]$tetheringManager.TetheringOperationalState -eq 'On') {
            Emit @{ ev = 'stopping' }
            $null = $tetheringManager.StopTetheringAsync()
            $stopped = $false
            for ($attempt = 0; $attempt -lt 20; $attempt++) {
                Start-Sleep -Milliseconds 500
                $st = [string]$tetheringManager.TetheringOperationalState
                Emit @{ ev = 'stop_wait'; attempt = ($attempt + 1); max = 20; state = $st }
                if ($st -eq 'Off') { $stopped = $true; break }
            }
            if (-not $stopped) { throw '需要修改热点配置，但 Windows 移动热点未能先停止。' }
        }

        Emit @{ ev = 'configuring' }
        $accessPoint.Ssid = '__SSID__'
        $accessPoint.Passphrase = '__PASSWORD__'
        $null = $tetheringManager.ConfigureAccessPointAsync($accessPoint)

        $configured = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 500
            $cur = $tetheringManager.GetCurrentAccessPointConfiguration()
            Emit @{ ev = 'config_wait'; attempt = ($attempt + 1); max = 20 }
            if (
                $cur.Ssid -ceq '__SSID__' -and
                $cur.Passphrase -ceq '__PASSWORD__'
            ) { $configured = $true; break }
        }
        if (-not $configured) { throw 'Windows 移动热点名称或密码未能设置为请求的值。' }
    }

    $finalCfg = $tetheringManager.GetCurrentAccessPointConfiguration()
    Emit @{ ev = 'ssid'; ssid = [string]$finalCfg.Ssid }

    if ([string]$tetheringManager.TetheringOperationalState -eq 'On') {
        Emit @{ ev = 'already_on' }
    } else {
        Emit @{ ev = 'starting' }
        $null = $tetheringManager.StartTetheringAsync()
    }

    $hotspotStarted = $false
    $tetheringState = [string]$tetheringManager.TetheringOperationalState
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        $tetheringState = [string]$tetheringManager.TetheringOperationalState
        Emit @{ ev = 'start_wait'; attempt = ($attempt + 1); max = 40; state = $tetheringState }
        if ($tetheringState -eq 'On') { $hotspotStarted = $true; break }
    }
    if (-not $hotspotStarted) { throw "Windows 移动热点未启动，当前状态: $tetheringState" }

    Emit @{ ev = 'state'; state = $tetheringState }
    if ($failedAdapters.Count -gt 0) {
        Emit @{ ev = 'adapter_summary'; count = $failedAdapters.Count }
    }
    Emit @{ ev = 'done'; ok = $true }
} catch {
    Emit @{ ev = 'fatal'; msg = [string]$_.Exception.Message }
    exit 1
}
""".replace("__SSID__", SSID).replace("__PASSWORD__", PASSWORD)


STOP_POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
function Emit($obj) {
    Write-Output ('@@UI ' + ($obj | ConvertTo-Json -Compress -Depth 5))
}

try {
    $null = Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $networkInformation = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
    $tetheringManagerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
    $connectionProfile = $networkInformation::GetInternetConnectionProfile()
    if ($null -eq $connectionProfile) {
        throw 'Windows 没有检测到可用于管理热点的 Internet 连接。'
    }

    $tetheringManager = $tetheringManagerType::CreateFromConnectionProfile($connectionProfile)
    $state = [string]$tetheringManager.TetheringOperationalState
    if ($state -ne 'Off') {
        Emit @{ ev = 'stopping' }
        $null = $tetheringManager.StopTetheringAsync()
    }

    $stopped = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        $state = [string]$tetheringManager.TetheringOperationalState
        Emit @{ ev = 'stop_wait'; attempt = ($attempt + 1); max = 40; state = $state }
        if ($state -eq 'Off') {
            $stopped = $true
            break
        }
    }
    if (-not $stopped) {
        throw "Windows 移动热点未能停止，当前状态: $state"
    }

    Emit @{ ev = 'state'; state = $state }
    Emit @{ ev = 'done'; ok = $true; action = 'stop' }
} catch {
    Emit @{ ev = 'fatal'; msg = [string]$_.Exception.Message }
    exit 1
}
"""


def application_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """提交 UAC 提权请求并重启自身。成功提交返回 True。"""
    if getattr(sys, "frozen", False):
        target = str(application_path())
        params = ""
    else:
        target = sys.executable
        params = subprocess.list2cmdline([str(application_path())])
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params,
            str(application_path().parent), 1,
        )
    except OSError:
        return False
    return ret > 32


class Utf8BomRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write("\ufeff")
            stream.flush()
        return stream


def configure_file_logging() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    candidates = (
        application_path().parent / "Logs" / "start_hotspot.log",
        local_app_data / "SUNNBT_Hotspot" / "Logs" / "start_hotspot.log",
    )
    last_error = None
    for log_path in candidates:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if log_path.exists():
                existing_log = log_path.read_bytes()
                if not existing_log.startswith(codecs.BOM_UTF8):
                    log_path.write_bytes(codecs.BOM_UTF8 + existing_log)

            handler = Utf8BomRotatingFileHandler(
                log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        except OSError as exc:
            last_error = exc
            continue

        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        return log_path
    raise OSError(f"无法创建日志文件: {last_error}")


def bundled_resource_path(filename: str) -> Path:
    relative_path = Path(filename)
    candidates = [application_path().parent / relative_path]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / relative_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def auto_close_settings_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return local_app_data / "SUNNBT_Hotspot" / "settings.json"


def load_auto_close_preference(default: bool = True) -> bool:
    settings_path = auto_close_settings_path()
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError):
        logging.warning("无法读取 GUI 设置文件: %s", settings_path, exc_info=True)
        return default

    value = settings.get("auto_close_after_start") if isinstance(settings, dict) else None
    if not isinstance(value, bool):
        logging.warning("GUI 设置文件中的自动关闭选项无效，将使用默认值。")
        return default
    return value


def save_auto_close_preference(enabled: bool) -> None:
    settings_path = auto_close_settings_path()
    temporary_path = settings_path.with_suffix(".tmp")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path.write_text(
        json.dumps({"auto_close_after_start": enabled}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(settings_path)


class BackgroundVideoPreloader:
    def __init__(self, video_path: Path):
        self.video_path = video_path
        self.frames: queue.Queue[Image.Image] = queue.Queue(maxsize=1)
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.decoder: subprocess.Popen | None = None
        self.decoder_lock = threading.Lock()
        self.started_at = time.perf_counter()
        self.first_frame_at: float | None = None
        self.error: str | None = None

    def start(self) -> None:
        if not self.video_path.is_file():
            self.error = f"背景视频不存在: {self.video_path}"
            self.ready.set()
            logging.error(self.error)
            return
        logging.info("已在创建窗口前启动背景视频预加载。")
        threading.Thread(target=self._decode_frames, daemon=True).start()

    def _decode_frames(self) -> None:
        process = None
        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            width, height = VIDEO_FRAME_SIZE
            frame_bytes = width * height * 3
            command = [
                get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-stream_loop",
                "-1",
                "-re",
                "-probesize",
                "32",
                "-analyzeduration",
                "0",
                "-i",
                str(self.video_path),
                "-vf",
                (
                    f"fps={VIDEO_FPS},"
                    "scale=800:506:force_original_aspect_ratio=increase,"
                    "crop=800:506,"
                    "eq=brightness=-0.18:saturation=0.82"
                ),
                "-an",
                "-sn",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
            )
            with self.decoder_lock:
                self.decoder = process
            if self.stopping.is_set():
                process.terminate()
                return
            if process.stdout is None:
                raise RuntimeError("FFmpeg stdout pipe was not created.")

            while not self.stopping.is_set():
                data = self._read_frame(process.stdout, frame_bytes)
                if data is None:
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"视频解码器退出，代码: {process.returncode}"
                        )
                    continue
                frame = Image.frombytes("RGB", VIDEO_FRAME_SIZE, data)
                self._put_latest_frame(frame)
                if self.first_frame_at is None:
                    self.first_frame_at = time.perf_counter()
                    self.ready.set()
                    logging.info(
                        "背景视频首帧预加载完成，耗时 %.0f 毫秒。",
                        (self.first_frame_at - self.started_at) * 1000,
                    )
        except Exception as exc:
            if not self.stopping.is_set():
                self.error = str(exc)
                logging.exception("背景视频预加载失败。")
                self.ready.set()
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                with self.decoder_lock:
                    if self.decoder is process:
                        self.decoder = None

    def _put_latest_frame(self, frame: Image.Image) -> None:
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                pass

    @staticmethod
    def _read_frame(stream, frame_bytes: int) -> bytes | None:
        data = bytearray()
        while len(data) < frame_bytes:
            chunk = stream.read(frame_bytes - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def stop(self) -> None:
        self.stopping.set()
        with self.decoder_lock:
            process = self.decoder
        if process is not None and process.poll() is None:
            process.terminate()


class VideoBackground(tk.Canvas):
    def __init__(self, master, video_source: BackgroundVideoPreloader, **kwargs):
        super().__init__(
            master,
            bg="#050907",
            highlightthickness=0,
            **kwargs,
        )
        self.video_source = video_source
        self._stopping = False
        self.original_frame: Image.Image | None = None
        self._video_photo = None
        self._showing_live_frame = False
        self._preview_logged = False
        self._live_frame_logged = False
        self._video_error_logged = False
        self._video_item = self.create_image(0, 0, anchor=tk.NW)
        poster_path = bundled_resource_path(VIDEO_POSTER_FILENAME)
        try:
            with Image.open(poster_path) as poster:
                self.original_frame = poster.convert("RGB")
            logging.info("已载入背景视频首帧预览图: %s", poster_path)
        except OSError:
            logging.exception("无法载入背景视频首帧预览图: %s", poster_path)
        self.create_text(
            30,
            28,
            anchor=tk.NW,
            text="SUNNBT  /  MOBILE HOTSPOT",
            fill=GREEN_BRIGHT,
            font=("Segoe UI", 13, "bold"),
        )
        self.create_text(
            31,
            52,
            anchor=tk.NW,
            text="WIRELESS CONNECTION CONTROL",
            fill=TEXT_DIM,
            font=("Segoe UI", 8),
        )
        self.bind("<Configure>", self._on_resize)
        self.after(20, self._show_next_frame)

    def _show_video_error(self):
        self.create_text(
            30,
            90,
            anchor=tk.NW,
            text="背景视频解码失败，请查看 start_hotspot.log",
            fill=TEXT_DIM,
            font=("Segoe UI", 10),
        )

    def _on_resize(self, _event):
        if self.original_frame is not None and not self._stopping:
            self._render_frame()

    def _show_next_frame(self):
        frame_updated = False
        try:
            while True:
                self.original_frame = self.video_source.frames.get_nowait()
                frame_updated = True
        except queue.Empty:
            pass
        if frame_updated:
            self._showing_live_frame = True
            self._render_frame()
        elif (
            self.video_source.error is not None
            and not self._video_error_logged
        ):
            self._video_error_logged = True
            self._show_video_error()
        try:
            self.after(20, self._show_next_frame)
        except tk.TclError:
            pass

    def _render_frame(self):
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1 or self.original_frame is None:
            return

        frame = self.original_frame
        if frame.size != (width, height):
            frame = frame.resize((width, height), Image.Resampling.BILINEAR)
        self._video_photo = ImageTk.PhotoImage(frame, master=self)
        self.coords(self._video_item, 0, 0)
        self.itemconfigure(self._video_item, image=self._video_photo)
        if self._showing_live_frame and not self._live_frame_logged:
            panel_x = self.master.control_panel.winfo_x()
            panel_y = self.master.control_panel.winfo_y()
            logging.info(
                "动态背景视频已显示于窗口 %sx%s，右侧控制面板叠放在视频上（%s,%s）。",
                width,
                height,
                panel_x,
                panel_y,
            )
            self._live_frame_logged = True
        elif not self._showing_live_frame and not self._preview_logged:
            logging.info("已显示背景视频首帧预览，等待动态视频解码。")
            self._preview_logged = True

    def stop(self):
        self._stopping = True
        self.video_source.stop()


# ---------------- 自绘圆角进度条 ----------------
class ProgressBar(tk.Canvas):
    def __init__(self, master, **kw):
        super().__init__(master, height=10, bg=PANEL, highlightthickness=0, **kw)
        self.pct = 0.0
        self.failed = False
        self.bind("<Configure>", lambda _e: self._draw())

    @staticmethod
    def _round_poly(x1, y1, x2, y2, r):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def set_value(self, pct, failed=False):
        self.pct = max(0.0, min(100.0, pct))
        self.failed = failed
        self._draw()

    def _draw(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 2:
            return
        self.delete("all")
        self.create_polygon(
            self._round_poly(0, 0, w, h, h / 2),
            smooth=True, fill=TRACK, outline="",
        )
        if self.pct <= 0:
            return
        fw = max(h, w * self.pct / 100.0)
        fill = RED_DIM if self.failed else GREEN_DIM
        self.create_polygon(
            self._round_poly(0, 0, fw, h, h / 2),
            smooth=True, fill=fill, outline="",
        )
        # 内芯亮色，制造灯条质感
        inner_h = max(2, h * 0.32)
        iy = (h - inner_h) / 2
        inner_x2 = max(fw - 3, h / 2)
        inner_fill = RED if self.failed else GREEN
        if inner_x2 > 2:
            self.create_polygon(
                self._round_poly(2, iy, min(fw - 1, inner_x2 + 2), iy + inner_h, inner_h / 2),
                smooth=True, fill=inner_fill, outline="",
            )


# ---------------- 环形状态指示 ----------------
class Spinner(tk.Canvas):
    def __init__(self, master, size=54, **kw):
        super().__init__(master, width=size, height=size, bg=BG, highlightthickness=0, **kw)
        self.size = size
        self.angle = 0
        self.mode = "spin"  # spin / ok / fail
        self._tick()

    def set_mode(self, mode):
        self.mode = mode
        self.angle = 0
        self._draw()

    def _tick(self):
        if self.mode == "spin":
            self.angle = (self.angle + 30) % 360
            self._draw()
        self.after(70, self._tick)

    def _draw(self):
        s = self.size
        cx = cy = s / 2
        self.delete("all")
        if self.mode == "ok":
            self.create_line(cx - 13, cy + 1, cx - 3, cy + 12, fill=GREEN, width=3, capstyle=tk.ROUND)
            self.create_line(cx - 3, cy + 12, cx + 14, cy - 10, fill=GREEN, width=3, capstyle=tk.ROUND)
            return
        if self.mode == "fail":
            self.create_line(cx - 10, cy - 10, cx + 10, cy + 10, fill=RED, width=3, capstyle=tk.ROUND)
            self.create_line(cx + 10, cy - 10, cx - 10, cy + 10, fill=RED, width=3, capstyle=tk.ROUND)
            return
        # 8 段旋转弧，靠角度偏移制造旋转
        import math
        for i in range(8):
            a = math.radians(self.angle + i * 45)
            r1 = s * 0.26
            r2 = s * 0.42
            x1 = cx + r1 * math.cos(a)
            y1 = cy + r1 * math.sin(a)
            x2 = cx + r2 * math.cos(a)
            y2 = cy + r2 * math.sin(a)
            shade = 1.0 - i / 8
            col = self._shade(GREEN, shade)
            self.create_line(x1, y1, x2, y2, fill=col, width=3, capstyle=tk.ROUND)

    @staticmethod
    def _shade(hex_color, k):
        r = int(int(hex_color[1:3], 16) * k)
        g = int(int(hex_color[3:5], 16) * k)
        b = int(int(hex_color[5:7], 16) * k)
        return f"#{r:02x}{g:02x}{b:02x}"


# ---------------- 主窗口 ----------------
class HotspotGUI(tk.Tk):
    WIN_W = 1200
    WIN_H = 760

    def __init__(
        self,
        selftest=False,
        selftest_keep_open=False,
        selftest_launch_manager=False,
        video_source: BackgroundVideoPreloader | None = None,
    ):
        super().__init__()
        self.selftest = selftest
        self.selftest_launch_manager = selftest_launch_manager
        self.q = queue.Queue()
        self.proc = None
        self.worker = None
        self.running = False
        self.action = "start"
        self.operation_succeeded = False
        self.adapter_total = 0
        self.adapter_attempted = 0
        self.adapter_issues = []
        auto_close = False if selftest_keep_open else load_auto_close_preference()
        self.auto_close_var = tk.BooleanVar(value=auto_close)
        self._drag_x = 0
        self._drag_y = 0

        self.ui_font = "Microsoft YaHei UI"
        self.mono_font = "Consolas"

        self.overrideredirect(True)
        self.configure(bg=BG)
        self.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self._center()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if video_source is None:
            video_source = BackgroundVideoPreloader(
                bundled_resource_path(VIDEO_FILENAME)
            )
            video_source.start()
        self.video_background = VideoBackground(self, video_source)
        self.video_background.place(x=0, y=0, relwidth=1, relheight=1)
        self._build_titlebar()
        self._build_body()
        self._poll_queue()

        if selftest:
            self.after(400, self._run_selftest)
        else:
            self.after(300, self.start_work)

    # ---------- 布局 ----------
    def _center(self):
        self.update_idletasks()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(self.WIN_W, screen_width - 40)
        height = min(self.WIN_H, screen_height - 80)
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_titlebar(self):
        bar = tk.Frame(self, bg=BG, height=40)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        title = tk.Label(bar, text="\u25c8  SUNNBT", bg=BG, fg=GREEN,
                         font=(self.ui_font, 11, "bold"))
        title.pack(side=tk.LEFT, padx=14)
        for w in (bar, title):
            w.bind("<ButtonPress-1>", self._start_move)
            w.bind("<B1-Motion>", self._do_move)

        close = tk.Label(bar, text="\u2715", bg=BG, fg=TEXT_DIM,
                         font=(self.ui_font, 11), padx=12, cursor="hand2")
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda _e: self.on_close())
        close.bind("<Enter>", lambda _e: close.configure(fg=RED))
        close.bind("<Leave>", lambda _e: close.configure(fg=TEXT_DIM))

    def _build_body(self):
        body = tk.Frame(self, bg=BG, width=450)
        body.place(
            relx=1,
            x=-14,
            y=48,
            relheight=1,
            height=-60,
            anchor="ne",
            width=450,
        )
        body.pack_propagate(False)
        self.control_panel = body

        # 顶部：环形指示 + 主副标题
        top = tk.Frame(body, bg=BG)
        top.pack(fill=tk.X, pady=(6, 10))
        self.spinner = Spinner(top)
        self.spinner.pack(side=tk.LEFT, padx=(2, 14))
        head = tk.Frame(top, bg=BG)
        head.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(head, text="SUNNBT HOTSPOT", bg=BG, fg=TEXT,
                 font=(self.ui_font, 15, "bold")).pack(anchor="w")
        self.subtitle = tk.Label(head, text="正在初始化…", bg=BG, fg=TEXT_DIM,
                                 font=(self.ui_font, 9))
        self.subtitle.pack(anchor="w", pady=(2, 0))

        # 两个进度区块
        self.bars = []
        self.pct_labels = []
        self.status_labels = []
        names = ("启用网卡", "配置并启动热点")
        for name in names:
            block = tk.Frame(body, bg=PANEL)
            block.pack(fill=tk.X, pady=6)
            inner = tk.Frame(block, bg=PANEL)
            inner.pack(fill=tk.X, padx=14, pady=(10, 0))
            tk.Label(inner, text=name, bg=PANEL, fg=TEXT,
                     font=(self.ui_font, 10, "bold")).pack(side=tk.LEFT)
            pct_lbl = tk.Label(inner, text="0%", bg=PANEL, fg=GREEN,
                               font=(self.mono_font, 10, "bold"))
            pct_lbl.pack(side=tk.RIGHT)
            bar = ProgressBar(block)
            bar.pack(fill=tk.X, padx=14, pady=(6, 0))
            status_lbl = tk.Label(block, text="等待执行", bg=PANEL, fg=TEXT_DIM,
                                  font=(self.ui_font, 8), anchor="w")
            status_lbl.pack(fill=tk.X, padx=14, pady=(6, 10))
            self.bars.append(bar)
            self.pct_labels.append(pct_lbl)
            self.status_labels.append(status_lbl)

        # 真实日志区
        log_frame = tk.Frame(body, bg=PANEL)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.log_box = tk.Text(log_frame, height=5, bg=PANEL, fg=GREEN_DIM,
                               font=(self.mono_font, 8), bd=0, padx=10, pady=8,
                               wrap=tk.WORD, state=tk.DISABLED)
        self.log_box.pack(fill=tk.BOTH, expand=True)

        option_row = tk.Frame(body, bg=BG)
        option_row.pack(fill=tk.X, pady=(8, 0))
        tk.Checkbutton(
            option_row,
            text="启动成功后自动关闭",
            variable=self.auto_close_var,
            command=self._save_auto_close_preference,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=GREEN,
            selectcolor=PANEL,
            font=(self.ui_font, 9),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        # 底部按钮
        btn_row = tk.Frame(body, bg=BG)
        btn_row.pack(fill=tk.X, pady=12)
        self.start_btn = self._make_button(
            btn_row, "启动热点", lambda: self.start_work("start")
        )
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = self._make_button(
            btn_row, "停止热点", lambda: self.start_work("stop")
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.retry_btn = self._make_button(
            btn_row, "重试", lambda: self.start_work(self.action)
        )
        self.retry_btn.pack_forget()
        self._make_button(btn_row, "关闭", self.on_close).pack(side=tk.RIGHT)

    def _make_button(self, master, text, cmd):
        btn = tk.Label(master, text=text, bg=PANEL, fg=GREEN,
                       font=(self.ui_font, 9, "bold"), padx=18, pady=7,
                       cursor="hand2")
        btn.bind("<Button-1>", lambda _e: cmd())
        return btn

    def _save_auto_close_preference(self):
        try:
            save_auto_close_preference(self.auto_close_var.get())
        except OSError:
            logging.exception("无法保存自动关闭选项。")

    # ---------- 拖动 ----------
    def _start_move(self, event):
        self._drag_x = event.x_root
        self._drag_y = event.y_root

    def _do_move(self, event):
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self.geometry(f"+{self.winfo_x()+dx}+{self.winfo_y()+dy}")
        self._drag_x = event.x_root
        self._drag_y = event.y_root

    # ---------- 日志 ----------
    def append_log(self, text):
        logging.info("%s", text)
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, text + "\n")
        row_count = int(self.log_box.index("end-1c").split(".")[0])
        if row_count > 200:
            self.log_box.delete("1.0", f"{row_count - 200}.0")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    # ---------- 进度条更新 ----------
    def set_bar(self, idx, pct, status=None, failed=False, warning=False):
        self.bars[idx].set_value(pct, failed=failed)
        self.pct_labels[idx].configure(text=f"{pct:.0f}%",
                                       fg=RED if failed else WARNING if warning else GREEN)
        if status is not None:
            self.status_labels[idx].configure(
                text=status,
                fg=RED if failed else WARNING if warning else TEXT_DIM,
            )

    # ---------- 工作线程 ----------
    def start_work(self, action="start"):
        if self.running:
            return
        self.running = True
        self.action = action
        self.operation_succeeded = False
        self.adapter_total = 0
        self.adapter_attempted = 0
        self.adapter_issues = []
        self.spinner.set_mode("spin")
        self.subtitle.configure(
            text="正在启动移动热点…" if action == "start" else "正在停止移动热点…"
        )
        for i in range(2):
            self.set_bar(i, 0, "等待执行")
        self.retry_btn.pack_forget()
        self.start_btn.configure(fg=TEXT_DIM)
        self.stop_btn.configure(fg=TEXT_DIM)
        self.worker = threading.Thread(target=self._worker_thread, daemon=True)
        self.worker.start()

    def _worker_thread(self):
        try:
            creationflags = 0x08000000  # CREATE_NO_WINDOW
            self.proc = subprocess.Popen(
                [
                    "powershell.exe", "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-Command",
                    POWERSHELL_SCRIPT if self.action == "start" else STOP_POWERSHELL_SCRIPT,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            assert self.proc.stdout is not None
            for raw in self.proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("@@UI "):
                    try:
                        data = json.loads(line[5:])
                    except json.JSONDecodeError:
                        self.q.put(("log", line))
                    else:
                        self.q.put(("event", data))
                else:
                    self.q.put(("log", line))
            code = self.proc.wait()
        except OSError as exc:
            self.q.put(("fatal", f"无法启动 PowerShell：{exc}"))
            return
        self.q.put(("finished", code))

    # ---------- 队列消费 ----------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "event":
                    self._handle_event(payload)
                elif kind == "log":
                    self.append_log(payload)
                elif kind == "fatal":
                    self._mark_failure(payload)
                elif kind == "finished":
                    self._handle_finish(payload)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _handle_event(self, d):
        ev = d.get("ev")
        if ev in ("fatal", "done"):
            self.append_log(d.get("msg", d.get("reason", "操作完成")))

        if ev == "stage1_total":
            self.adapter_total = d["n"]
            if d["n"] == 0:
                self.set_bar(0, 100, "没有禁用的网卡，无需启用")
        elif ev == "stage1_none":
            self.set_bar(0, 100, "所有网卡均已启用")
        elif ev == "adapter_done":
            self.adapter_attempted += 1
            pct = self.adapter_attempted / d["total"] * 100
            if self.adapter_issues:
                status = (
                    f'已尝试 {self.adapter_attempted}/{d["total"]} 张网卡；'
                    f'有 {len(self.adapter_issues)} 项未完成，将继续启动热点'
                )
                self.set_bar(0, pct, status, warning=True)
            else:
                self.set_bar(0, pct, f'已启用网卡：{d["name"]}（{d["idx"]}/{d["total"]}）')
        elif ev == "adapter_fail":
            self.adapter_attempted += 1
            self.adapter_issues.append(f'{d["name"]}: {d["msg"]}')
            pct = (
                self.adapter_attempted / self.adapter_total * 100
                if self.adapter_total
                else 0
            )
            self.set_bar(
                0,
                pct,
                f'网卡未启用：{d["name"]}；不影响后续热点启动',
                warning=True,
            )
            self.append_log(f'[警告] {d["name"]} 未能启用，将继续：{d["msg"]}')
        elif ev == "adapter_warning":
            self.adapter_issues.append(d["msg"])
            self.set_bar(
                0,
                self.bars[0].pct,
                "网卡枚举遇到问题；仍将继续启动热点",
                warning=True,
            )
            self.append_log(f'[警告] {d["msg"]}')
        elif ev == "adapter_summary":
            self.append_log(
                f'[警告] 有 {d["count"]} 张网卡未能启用；热点已继续启动。'
            )
        elif ev == "profile":
            self.set_bar(1, 10, f'共享 Internet 连接：{d["name"]}')
        elif ev == "config_check":
            if d["match"]:
                self.set_bar(1, 60, f'名称/密码已正确（{d["ssid"]}），无需修改')
            else:
                cur = d["ssid"] or "空"
                self.set_bar(1, 20, f'当前配置不匹配（现名：{cur}），准备修改')
        elif ev == "stopping":
            self.set_bar(1, 22, "正在停止旧热点…")
        elif ev == "stop_wait":
            pct = 20 + 15 * d["attempt"] / d["max"]
            self.set_bar(1, pct, f'等待热点停止 {d["attempt"]}/{d["max"]}（{d["state"]}）')
        elif ev == "configuring":
            self.set_bar(1, 40, f'正在设置热点名称 {SSID} 与新密码…')
        elif ev == "config_wait":
            pct = 40 + 15 * d["attempt"] / d["max"]
            self.set_bar(1, pct, f'核实新配置 {d["attempt"]}/{d["max"]}')
        elif ev == "ssid":
            self.set_bar(1, 60, f'热点名称已核实：{d["ssid"]}')
        elif ev == "already_on":
            self.set_bar(1, 65, "热点已在运行，核实状态中")
        elif ev == "starting":
            self.set_bar(1, 65, "正在启动热点…")
        elif ev == "stopping":
            self.set_bar(1, 30, "正在停止移动热点…")
        elif ev == "stop_wait":
            pct = 30 + 60 * d["attempt"] / d["max"]
            self.set_bar(1, pct, f'等待热点停止 {d["attempt"]}/{d["max"]}（{d["state"]}）')
        elif ev == "start_wait":
            pct = 65 + 30 * d["attempt"] / d["max"]
            self.set_bar(1, pct, f'等待热点启动 {d["attempt"]}/{d["max"]}（{d["state"]}）')
        elif ev == "state":
            self.set_bar(1, 98, f'热点真实状态：{d["state"]}')
        elif ev == "fatal":
            self._mark_failure(d["msg"])
        elif ev == "done":
            if d["ok"]:
                if d.get("action", self.action) == "stop":
                    self.set_bar(0, 100, "网卡未作更改")
                    self.set_bar(1, 100, "热点已停止并核实")
                    self.subtitle.configure(text="移动热点已关闭")
                    self.append_log("[完成] 移动热点已停止并核实。")
                else:
                    if self.adapter_issues:
                        self.set_bar(
                            0,
                            100,
                            f"热点已启动；网卡有 {len(self.adapter_issues)} 项未完成",
                            warning=True,
                        )
                    else:
                        self.set_bar(0, 100, "网卡阶段完成")
                    self.set_bar(1, 100, "热点已启动并核实")
                    subtitle = "移动热点已开启"
                    if self.auto_close_var.get():
                        subtitle += "，窗口将在 3 秒后关闭"
                    self.subtitle.configure(text=subtitle)
                    self.append_log("[完成] 移动热点已启动并核实。")
                    self.operation_succeeded = True
                self.spinner.set_mode("ok")
            else:
                self._mark_failure(d.get("reason", "存在未完成项"))

    def _mark_failure(self, msg):
        self.set_bar(1, self.bars[1].pct, msg, failed=True)
        self.subtitle.configure(text="操作出现问题")
        self.spinner.set_mode("fail")
        self.append_log(f"[错误] {msg}")

    def _handle_finish(self, code):
        self.running = False
        self.proc = None
        self.start_btn.configure(fg=GREEN)
        self.stop_btn.configure(fg=GREEN)
        if code != 0 and self.spinner.mode != "fail":
            self._mark_failure(f"PowerShell 退出码 {code}")
            self.retry_btn.pack(side=tk.LEFT, padx=(8, 0))
        elif self.action == "start" and self.operation_succeeded:
            manager_started = True
            if not self.selftest or self.selftest_launch_manager:
                manager_started = self._launch_client_manager(
                    selftest=self.selftest_launch_manager
                )
            if self.auto_close_var.get() and manager_started:
                logging.info("热点启动成功，窗口将在 3 秒后自动关闭。热点将继续运行。")
                self.after(3000, self.on_close)
            elif manager_started:
                logging.info("热点启动成功，自动关闭选项已关闭；窗口保持打开。")

    def _launch_client_manager(self, selftest=False):
        manager_dir = application_path().parent / "Tools" / "client_manager"
        manager_exe = manager_dir / "SUNNBT_Client_Manager.exe"
        manager_script = application_path().parent / "client_manager.py"
        if getattr(sys, "frozen", False):
            command = [str(manager_exe)]
        else:
            command = [sys.executable, str(manager_script)]
        if selftest:
            command.append("--selftest")
        try:
            subprocess.Popen(
                command,
                cwd=str(application_path().parent),
                close_fds=True,
            )
        except OSError as exc:
            logging.exception("无法启动热点设备管理程序。")
            self.append_log(f"[错误] 无法启动设备管理程序：{exc}")
            self.subtitle.configure(text="设备管理程序启动失败，查看日志")
            self.auto_close_var.set(False)
            messagebox.showerror(
                "设备管理程序启动失败",
                f"热点已启动，但无法启动设备管理程序：\n{exc}",
                parent=self,
            )
            return False
        self.append_log("[完成] 已启动热点设备管理程序。")
        return True

    # ---------- 自检（不执行任何真实操作） ----------
    def _run_selftest(self):
        self.append_log("[自检模式] 未执行真实网络操作，仅用于界面检查。")
        demo = [
            {"ev": "stage1_total", "n": 0},
            {"ev": "stage1_none"},
            {"ev": "profile", "name": "WLAN"},
            {"ev": "config_check", "match": False, "ssid": "OLD_AP"},
            {"ev": "stopping"},
            {"ev": "stop_wait", "attempt": 1, "max": 20, "state": "On"},
            {"ev": "configuring"},
            {"ev": "ssid", "ssid": SSID},
            {"ev": "starting"},
            {"ev": "start_wait", "attempt": 1, "max": 40, "state": "Starting"},
            {"ev": "state", "state": "On"},
            {"ev": "done", "ok": True},
        ]
        for i, data in enumerate(demo):
            self.after(250 * (i + 1), lambda d=data: self._handle_event(d))
        self.after(250 * (len(demo) + 2), self._finish_selftest)

    def _finish_selftest(self):
        self.running = False
        manager_started = True
        if self.selftest_launch_manager:
            manager_started = self._launch_client_manager(selftest=True)
        if self.auto_close_var.get() and manager_started:
            logging.info("界面自检完成，窗口将在 3 秒后自动关闭。")
            self.after(3000, self.on_close)
        elif manager_started:
            logging.info("界面自检完成，自动关闭已关闭；窗口保持打开。")

    # ---------- 关闭 ----------
    def on_close(self):
        self.video_background.stop()
        if self.proc is not None and self.proc.poll() is None:
            pid = self.proc.pid
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, creationflags=0x08000000,
                )
            except OSError:
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.destroy()


def enable_dpi_awareness():
    for setter in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            setter()
            return
        except OSError:
            continue


def main() -> int:
    selftest = "--selftest" in sys.argv
    selftest_keep_open = "--selftest-no-auto-close" in sys.argv
    selftest_launch_manager = "--selftest-launch-manager" in sys.argv
    enable_dpi_awareness()

    try:
        log_path = configure_file_logging()
    except OSError as exc:
        print(f"无法创建日志文件: {exc}", file=sys.stderr)
        return 1
    logging.info("SUNNBT Hotspot GUI 启动，日志文件: %s", log_path)

    if not selftest and os.name == "nt" and not is_admin():
        logging.info("请求 Windows 管理员权限。")
        submitted = relaunch_as_admin()
        if not submitted:
            box = tk.Tk()
            box.withdraw()
            messagebox.showerror("提权失败", "无法发起管理员权限请求，请右键以管理员身份运行。")
            box.destroy()
            return 1
        return 0

    video_source = BackgroundVideoPreloader(
        bundled_resource_path(VIDEO_FILENAME)
    )
    video_source.start()
    try:
        app = HotspotGUI(
            selftest=selftest,
            selftest_keep_open=selftest_keep_open,
            selftest_launch_manager=selftest_launch_manager,
            video_source=video_source,
        )
    except Exception:
        video_source.stop()
        raise
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
