"""UTF-8 输出引导 —— 被 import 时立刻生效，不等 __main__。

为什么需要这个文件
----------------
CI 实测（2026-11-01，GitHub Actions windows-latest / py3.10）：
    ERROR: test_guard_actually_fires (test_guards.TestAssetGuard)
    UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-8

根因：Windows runner 的 sys.stdout 编码是 **cp1252**（不是 UTF-8），
而守卫脚本里的 print() 含中文，一输出就抛异常。

更隐蔽的是：这次崩溃发生在**被测试 import 之后**调用 selftest() 时。
守卫脚本的 UTF-8 设置原本只写在 `if __name__ == "__main__"` 分支里
（甚至根本没写），所以：
    · 命令行直接跑  → 走 __main__ → 若那里设了就对
    · 被 import 调用 → 不走 __main__ → 输出仍是 cp1252 → 崩

所以必须在**模块导入那一刻**就设置，而不是等入口判断。

这里用一个被三个守卫 import 的小模块，副作用是「导入即修好编码」。
"""
from __future__ import annotations

import sys


def ensure_utf8_stdout() -> None:
    """把 stdout/stderr 切到 UTF-8；已设过则幂等无副作用。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # StringIO / 已被替换的流：无法 reconfigure，忽略
            pass


# ★ 导入即执行。这是本模块存在的全部意义。
ensure_utf8_stdout()
