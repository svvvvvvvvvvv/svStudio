# -*- coding: utf-8 -*-
"""设计层（v0.3，2026-11-01 整理）。

唯一设计真源：`design/设计真源.md`；纪律：`design/纪律.md`。
一句话：**选卡 → 引擎给介质基线 ① → LR 对齐卡 ③ → 人目检**。

模块地图
--------
匹配 A（机内 JPG，不渲 ①′）
  contentmatch.py   读 RAW 内嵌 JPG → 测量 → 内容匹配 → 候选页
  card_content.py   目录内容卡：读 / 打分 / 理由
  matchpage.py      内容匹配页（给人拍板）
  matchcard.py      卡注册表（cards / raw_card / card_samples）

引擎（介质级）
  flow.py           渲一次 ①（固定 PE、交付尺寸）+ 余量报告
  card_render.py    卡 → 介质块
  medium.py         每目录一份介质（media/<卡名>.json）
  texture.py        质感参数（引擎链换算）
  stocks.py / profile_table.py   卷/纸事实（查 profile）

LR（图级）
  lr_plan.py        LRPlan 数据模型（WB/Basic/PointColor/局部/曲线）
  lr_solve.py       WB → 曝光/明暗（Basic 为主）→ 肤色（蒙版里的 Point Color）→ 局部
  lr_response.py    Basic 滑杆实测一阶响应（规划起点）
  curve_design.py   曲线补形状（PCHIP，单调）
  lr_exec.py / lrsink.py / lr_bridge.py   XMP + 桥（肤色蒙版）
  peer.py           逐片 peer（同构图/同场景）
  batch_guard.py    批级护栏（只报告，不下发公共曲线）

收尾
  review.py         ③ 目检页 + 测量（**不判过/不过**）
  measure_all.py    唯一尺子；measure.py 读记录 + 口径 sha
  batch.py          批次：渲 + 交付域测量
  cli.py            命令行：run / match-jpg / page / xmp / review / cards / stocks

命令
----
    python -m design.cli cards
    python -m design.cli match-jpg --raf <RAW目录> --out <输出目录>
    python -m design.cli run --raf <RAW目录> --out <输出目录> --card <卡名> [--export]
    python -m design.cli xmp  <①测量.json> --card <卡名> --out <输出目录>
    python -m design.cli review <①测量.json> <③测量.json> --out <输出目录>
"""

__all__ = ['batch', 'batch_guard', 'calibration', 'card_content', 'card_render',
           'cli', 'color_math', 'contentmatch', 'curve', 'curve_design',
           'flow', 'lr_bridge', 'lr_exec', 'lr_plan', 'lr_response', 'lr_solve',
           'lr_xmp', 'lrsink', 'matchcard', 'matchpage', 'measure', 'measure_all',
           'medium', 'peer', 'photometry', 'review', 'stocks', 'texture',
           'profile_table']
