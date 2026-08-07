# -*- coding: utf-8 -*-
"""期货量化 CTA — 顶层入口。

默认走：参数寻优 + 保证金/相关性/VaR 仓位流水线。
"""

from cta.run_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
