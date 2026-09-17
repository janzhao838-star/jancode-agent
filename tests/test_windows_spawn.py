# -*- coding: utf-8 -*-
"""Windows 命令拼装的验证。

Windows 侧没法在真机上跑（手上没有 Windows 机器），但那条平台分支
「拼出来的命令对不对」是可以查的——真机上出问题往往就是这一步拼错了：
npx 是 .cmd 批处理文件，CreateProcess 直接执行会报
「不是有效的 Win32 应用程序」，必须用 cmd /c 包一层。
"""

from jancode_agent.mcp import spawn_command


def test_非_windows_原样返回():
    assert spawn_command("npx", ["-y", "pkg"], plat="posix") == ["npx", "-y", "pkg"]
    assert spawn_command("node", ["server.js"], plat="posix") == ["node", "server.js"]


def test_windows_上_npx_要用_cmd_包一层():
    got = spawn_command("npx", ["-y", "pkg"], plat="nt", which=lambda c: None)
    assert got == ["cmd", "/c", "npx", "-y", "pkg"]


def test_windows_上_npm_和_pnpm_也要包():
    for name in ("npm", "pnpm"):
        got = spawn_command(name, ["run", "start"], plat="nt", which=lambda c: None)
        assert got[:3] == ["cmd", "/c", name], got


def test_windows_上解析到_cmd_路径也要包():
    got = spawn_command("C:/tools/npx.cmd", ["-y", "pkg"], plat="nt",
                        which=lambda c: "C:/Tools/npx.CMD")
    assert got[:2] == ["cmd", "/c"], got
    assert got[2].endswith("npx.cmd")


def test_windows_上真正的可执行文件不包():
    # 真正的可执行文件不该被 cmd 包，包了反而多一层、参数转义也变复杂
    got = spawn_command("node", ["server.js"], plat="nt", which=lambda c: "C:/node.exe")
    assert got == ["node", "server.js"]


def test_没有参数也不出错():
    assert spawn_command("npx", None, plat="nt", which=lambda c: None) == ["cmd", "/c", "npx"]
    assert spawn_command("node", [], plat="posix") == ["node"]
