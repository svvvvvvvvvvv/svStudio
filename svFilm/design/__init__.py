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
cards.py       「卡」= 一卷+一纸+质感档位+风格档（唯一被匹配/选用/渲染的单位）
cards_match.py 现有卡之间的排序
match.py       卷层排序（值得做新卡的参考）+ 需求向量
texture.py     质感：换算 / 规格（查引擎卷表）/ 档位；禁用项
flow.py        选卡 → 质感 → 渲一次（引擎自己的测光）→ 量余量 → 交 LR
               ★ 引擎不管脸、不管落点（那是 LR 的事）
lr.py          LR 侧七步求解（一次算一次验，不闭环）
lrmodel.py     LR 的数值模型（只收查得到的；查不到就明说）
curve.py       曲线：只修不变量 + 预算；PCHIP 插值；采样点含锚点
states.py      三态：①→② 用 LUT 自算；②→③ 算不了（点颜色无公开函数）
intent.py      意图（接口定义，非物理断言）
measure.py     测量桥：读 measure_all 记录 + 口径 sha
lrsink.py      LRPlan → XMP 作业单 + manifest（**不驱动 LR**）
cli.py         命令行：stocks/cards/check/plan/curve/match/flow/texture/xmp/states

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

__all__ = ['invariants', 'stocks', 'cards', 'cards_match', 'match', 'texture',
           'flow', 'lr', 'lrmodel', 'curve', 'states', 'intent', 'measure',
           'lrsink', 'cli']
