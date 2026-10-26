#!/usr/bin/env bash
# 在 macOS 上构建 CodeTyper.app（需在 Mac 本机执行）
# 用法:  bash build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
echo "==> 使用 Python: $($PY --version)"

# 1. 独立虚拟环境
if [ ! -d .venv-mac ]; then
  "$PY" -m venv .venv-mac
fi
source .venv-mac/bin/activate
python -m pip install -U pip wheel
pip install -r requirements-mac.txt pyinstaller

# 2. 把 PNG 图标转成 macOS 的 .icns
if [ ! -f app_icon.icns ]; then
  echo "==> 生成 app_icon.icns"
  rm -rf app_icon.iconset && mkdir -p app_icon.iconset
  sips -z 16 16     app_icon.png --out app_icon.iconset/icon_16x16.png      >/dev/null
  sips -z 32 32     app_icon.png --out app_icon.iconset/icon_16x16@2x.png    >/dev/null
  sips -z 32 32     app_icon.png --out app_icon.iconset/icon_32x32.png       >/dev/null
  sips -z 64 64     app_icon.png --out app_icon.iconset/icon_32x32@2x.png    >/dev/null
  sips -z 128 128   app_icon.png --out app_icon.iconset/icon_128x128.png     >/dev/null
  sips -z 256 256   app_icon.png --out app_icon.iconset/icon_128x128@2x.png  >/dev/null
  sips -z 256 256   app_icon.png --out app_icon.iconset/icon_256x256.png     >/dev/null
  sips -z 512 512   app_icon.png --out app_icon.iconset/icon_256x256@2x.png  >/dev/null
  iconutil -c icns app_icon.iconset -o app_icon.icns
fi

# 3. 打包（--windowed 在 macOS 上会生成 .app 包）
echo "==> PyInstaller 打包"
pyinstaller --noconfirm --clean --onefile --windowed \
  --name CodeTyper --icon app_icon.icns \
  --exclude-module backend_win \
  code_typer.py

# 4. ad-hoc 签名（arm64 必须有签名，否则会被系统判为"已损坏"）
codesign --force --deep -s - dist/CodeTyper.app || echo "签名跳过"

# 5. 压缩（ditto 能保留可执行权限，直接 zip 会丢）
ditto -c -k --sequesterRsrc --keepParent dist/CodeTyper.app dist/CodeTyper-macos.zip
echo "==> 完成: dist/CodeTyper-macos.zip"
