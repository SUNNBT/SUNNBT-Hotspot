本项目由SUNNBT和AI共同开发
本项目可以自动开启热点，目前为V1版本，可能不太好用
已下文档为AI写的



# SUNNBT Hotspot 源码

本目录包含 GUI 版、纯命令行版的 Python 源码、PyInstaller 配置、构建脚本和 GUI 资源。发行包输出到项目根目录的 `BUILD`：

- `BUILD\SUNNBT_Hotspot`：纯命令行发行包
- `BUILD\SUNNBT_Hotspot_GUI`：图形界面发行包

## 构建环境

- 64 位 Windows 10/11
- Python 3.10 或更新版本，并包含 Tk（GUI 版需要）
- PowerShell

在项目根目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\YUANMA\requirements-build.txt
.\YUANMA\build_cli_release.ps1
.\YUANMA\build_portable_release.ps1
```

也可以分别运行两个构建脚本，只生成需要的版本。脚本优先使用项目根目录下的 `.venv`；没有该虚拟环境时，会使用 PATH 中的 `python.exe`。

GUI 包含背景视频、首帧预览图和设备管理工具；CLI 包只有控制台热点启动程序。两种发行包均为文件夹版，目标电脑无需安装 Python，但必须保留发行目录中的 `_internal` 等文件。

## 运行要求

程序使用 Windows 移动热点功能，需要支持该功能的无线网卡和驱动，并会请求管理员权限。请先阅读相应发行包中的 `发行说明.txt`。

## 许可证与资源

源码使用项目根目录中的 MIT 许可证。`Videos` 中的背景素材及其预览图不包含在该授权声明内；发布公开仓库前，请确认拥有这些素材的分发权，或替换为明确允许再分发的素材。
