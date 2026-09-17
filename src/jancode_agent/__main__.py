# -*- coding: utf-8 -*-
"""python -m jancode_agent 入口。

pyproject 声明的命令是 jancode / jancode-agent，但很多人
（尤其源码目录里跑的）习惯 python -m jancode_agent——
少了 __main__.py 的话这个习惯直接报「cannot be directly executed」。
转发给 cli.main，退出码原样透传。
"""

import sys

from .cli import main

sys.exit(main())
