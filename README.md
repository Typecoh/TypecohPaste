# 代码输入助手 (Code Typer)

把代码逐字"敲"进不能复制粘贴的网页编辑器，自动抵消换行自动缩进。
支持 **Windows** 和 **macOS**，界面与逻辑完全一致。

## 目录结构

| 文件 | 说明 |
|---|---|
| `code_typer.py` | 入口 + 界面（tkinter，跨平台） |
| `core.py` | 配置、全角转半角、逐字输入引擎（跨平台） |
| `backend_win.py` | Windows 后端：SendInput 注入 + 全局钩子，无第三方依赖 |
| `backend_mac.py` | macOS 后端：Quartz CGEvent 注入 + CGEventTap 监听 |
| `requirements-mac.txt` | macOS 构建依赖 |
| `build_mac.sh` | macOS 一键构建脚本 |
| `.github/workflows/build-mac.yml` | 云端自动构建 macOS 版（Intel + Apple Silicon） |
| `dist/CodeTyper.exe` | 已构建好的 Windows 版 |

## Windows

已构建好：`dist/CodeTyper.exe`（单文件，直接发给别人即可）。

自己重新构建：

```bash
python -m PyInstaller --noconfirm --onefile --windowed --name TypecohCodeTyper --icon app_icon.ico --exclude-module backend_mac code_typer.py
```

> 没有数字签名，第一次运行会被 SmartScreen 拦截：点「更多信息 → 仍要运行」。

## macOS

PyInstaller 不能交叉编译，`.app` 必须在 macOS 上构建，两条路：

### 方式一：有 Mac 就一条命令

```bash
bash build_mac.sh
# 产物: dist/CodeTyper-macos.zip
```

### 方式二：没有 Mac，用 GitHub Actions 免费构建

1. 在 GitHub 新建一个仓库，把本目录所有文件推上去
2. 仓库页 → Actions → 左侧 `build-mac` → 右上 **Run workflow**
3. 跑完后在本次运行页面下载 `CodeTyper-macos-intel`（老 Mac）
   或 `CodeTyper-macos-apple-silicon`（M1/M2/M3/M4）

### macOS 首次运行必看

1. **辅助功能授权**：系统设置 → 隐私与安全性 → 辅助功能 → 勾选 CodeTyper。
   不授权既发不出按键，也收不到中键双击。
2. **绕过 Gatekeeper**：app 未签名（签名要 99 美元/年的开发者账号），
   下载后打不开时：右键 → 打开；或
   `xattr -dr com.apple.quarantine /Applications/CodeTyper.app`
3. 主快捷键是 **⌘⇧V**（Mac 的 F9 默认是媒体键），中键双击同样可用。

## 平台差异速览

| | Windows | macOS |
|---|---|---|
| 注入方式 | SendInput（Unicode 直送，绕输入法） | Quartz CGEvent（Unicode 直送，绕输入法） |
| 开始快捷键 | F9 | ⌘⇧V（F9 备选） |
| 中键单击 | 拦截后 320ms 内补发（防浏览器自动滚动） | 直接放行（Mac 无自动滚动） |
| 额外依赖 | 无 | pyobjc-framework-Quartz |
| 权限 | 目标程序是管理员时需同级权限 | 辅助功能授权 |
| 配置位置 | `%APPDATA%\CodeTyper\config.json` | `~/Library/Application Support/CodeTyper/config.json` |
