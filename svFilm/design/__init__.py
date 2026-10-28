# -*- coding: utf-8 -*-
"""新设计框架（2026-10-29 起）。

设计文档（唯一真源）：`E:\\ds\\photo\\分工设计_引擎与LR_1029.md`

一句话：
    引擎交一张「带余量、不偏色、有胶片质感的底片」；
    LR 决定这张底片最终多亮、什么颜色；
    判据只有「不变量 + 意图」，没有「该到多少」。

模块地图
--------
invariants.py  T0~T5 / S1~S5 判定（纯函数，可单测）
stocks.py      从引擎 profile 读卷的性格五轴 + 卷↔纸配对（卷决定纸）
engine.py      引擎侧：选卷纸 + T0/T3 校验（**不逐图**）
lr.py          LR 侧：七步求解（一次算一次验，不闭环）
curve.py       曲线：只搬形状、不动 p50、每锚点 ≤3 L* 预算
intent.py      意图：亮/暖/浓/软（−1..+1），不许破不变量
measure.py     测量桥：读 measure_all 记录 + 口径 sha
lrsink.py      LR 执行端：LRPlan → XMP → 借桥出图
pipeline.py    三态编排 + 回归检查
cli.py         命令行：stocks / check / plan / curve

命令行
------
    python -m design.cli stocks
    python -m design.cli check <recs.json> --stage engine --stem "_engine\\.tif$"
    python -m design.cli plan  <recs.json> --stem "_engine\\.tif$" --intent denser=0.2
    python -m design.cli curve <src.json> <dst.json>

★ 重开的两条已关闭决议（AGENTS §五），基础变了 —— 写明原因：
  1. 「曝光层机制 = pe + 动态 PE」→ 现在曝光归 LR。
     依据变了：引擎出片的 T0 实测 9/9 不过（p99≈97、clip 最高 16.7%），
     说明当年 pe 闭环补的是**底片余量**，不是"曝光本身必须在引擎里"。
  2. 「卷/纸 = portra_400 + portra_endura 固定」→ 卷纸变成离散的风格选择。
     依据变了：profile 自带 `target_print`（卷决定纸），三组阵营明确；
     且卷纸不再用于追数值，只用于定风格。

归档
----
`E:\\svStudio\\svFilm\\_archive_1029\\`  本次清理掉的旧实现（可 git 追回）
`E:\\ds\\photo\\_corpus\\kurai\\full\\_archive_1029\\`  旧链 64 个脚本
"""

__all__ = ['invariants', 'stocks', 'engine', 'lr', 'curve', 'intent',
           'measure', 'lrsink', 'pipeline', 'cli']
