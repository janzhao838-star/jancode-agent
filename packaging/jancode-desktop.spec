# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把桌面版冻成双击就能用的应用。

有一条最容易漏：界面文件必须显式带进来。web/index.html 是普通数据文件，
PyInstaller 只跟着 import 关系走，看不见它——漏掉的话打出来的 app 能启动、
窗口也打开，但界面是 404。这种错只有真装一次才会发现。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP_NAME = "JanCode 智能体"

# macOS 发 .app 目录；Windows 发单文件 exe（用户下一个文件就能用，不用解压）。
IS_MAC = sys.platform == "darwin"
ICON = str(ROOT / "packaging" / ("icon.icns" if IS_MAC else "icon.ico"))

a = Analysis(
    [str(ROOT / "packaging" / "desktop_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src" / "jancode_agent" / "web" / "index.html"), "jancode_agent/web"),
    ],
    hiddenimports=[
        "webview.platforms.cocoa" if IS_MAC else "webview.platforms.edgechromium",
        "webview.platforms.winforms" if not IS_MAC else "webview.platforms.gtk",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(
        pyz, a.scripts, [], exclude_binaries=True,
        name=APP_NAME, console=False, icon=ICON,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
    app = BUNDLE(
        coll,
        name=APP_NAME + ".app",
        icon=ICON,
        bundle_identifier="cn.janzhao.jancode-agent",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleName": APP_NAME,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="JanCode-Agent", console=False, icon=ICON,
        bootloader_ignore_signals=False, strip=False, upx=False,
    )
