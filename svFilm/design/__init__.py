# -*- coding: utf-8 -*-
"""新设计框架代码（2026-10-29 起）。

设计文档（唯一真源）：
    E:\\ds\\photo\\分工设计_引擎与LR_1029.md

一句话：
    引擎交一张「带余量、不偏色、有胶片质感的底片」；
    LR 决定这张底片最终多亮、什么颜色；
    判据只有「不变量 + 意图」，没有「该到多少」。

模块
----
invariants.py   T0~T5 / S1~S5 的判定（纯函数，可单测）
stocks.py       从引擎 profile 读出卷的性格（五轴）+ 卷↔纸配对
engine.py       引擎侧：选卷纸 + T0/T3 校验（不逐图）
lr.py           LR 侧：七步求解（一次算一次验，不闭环）
pipeline.py     三态编排 + 回归检查

还没写
------
lrsink.py       把 LRPlan 写成 XMP 作业单并驱动导出（现有实现见
                `E:\\ds\\photo\\_corpus\\kurai\\full\\lr_xmp.py`，待迁入）
measure_bridge 把 measure_all 的记录接进 pipeline（现在是注入式）

归档
----
`E:\\svStudio\\svFilm\\_archive_1029\\`  本次清理掉的旧实现（可 git 追回）
`E:\\ds\\photo\\_corpus\\kurai\\full\\_archive_1029\\`  旧链 64 个脚本
"""

__all__ = ['invariants', 'stocks', 'engine', 'lr', 'pipeline']
