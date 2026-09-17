"""PyInstaller 的冻结入口。

为什么单独写一个文件、而不是直接冻结 pip 生成的 jancode-desktop 命令：
那个命令脚本带着 pip 的启动包装，PyInstaller 顺着它分析会把一堆无关代码拖进来。
这里只 import 真正要跑的函数，打出来的东西干净得多。
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # Windows 上不加这一行，冻结出来的 exe 在启动子进程时会反复自我重启。
    multiprocessing.freeze_support()
    from jancode_agent.desktop import main

    sys.exit(main())
