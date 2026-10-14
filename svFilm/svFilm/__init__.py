# -*- coding: utf-8 -*-
r"""svFilm —— 数码仿胶片管线。

★ 10-14：本 docstring 与 `config.py` 顶部那句「一句话现状」、`pipeline.py` 的文件头
  **同一口径**（改任何模块前先读那两处）。

## 一句话现状

**一条链**：`解码 + 白平衡 + 护栏 → 判场景 → 胶片引擎(spektrafilm 0.3.4) → 出图`
  ⇒ **引擎出图 = 成片**。**整条链上只有引擎在动像素**（`selftest.t_contract` 有断言钉着）。

**已经删掉的层**（**整段删代码，不是关开关**；`selftest.t_dropped_layers` 钉着不许回来）：
  · **09-29** `drop-tone-and-skin`：影调层 `tone.py` · 肤色层 `grade.skin()` ·
    认人认脸 `face.py` / `facegain.py` / `region.py`；
  · **10-10** `drop-grade`：**颜色层** `grade.py`（混色 + 分色）—— 理由见 `pipeline.run_from` 里那段注释。

⚠ **本包现在【没有】L0~L4 那五层。** 旧版这份 docstring 写的
  「L0 analyze / L1 correct / L2 style / L3 local / L4 guard」**已随上面两次 drop 全部作废**
  （`stocks.py` / `spatial.py` 等模块当时一并删除）。
  ⇒ 现在只剩「入口 + 引擎 + `_post_scan`（**扫描段**后处理）」。
  ⚠ 那不是"颜色层"：`presets._post_scan` 只做 `scanfx.chroma_blur` 一件事。

## 模块地图

  `config.py`    全部可调参数（**一个文件，别处不许写死数**）
  `io.py`        解码 / 存盘 —— **机器差异只关在这个关口里**
  `scene.py`     判场景（`AXES` 三轴 + 光位）
  `person.py`    「人在哪」粗掩膜（**只服务光位与测光**，不是认人认脸）
  `exposure.py`  逐图算印相曝光（`ev' = (1-w)·ev + b`）
  `presets.py`   读预设 JSON -> 引擎参数；`_post_scan` = **扫描段**后处理
  `targets.py`   `_scene_engine` / `_stock_engine` 两层取参
  `scanfx.py`    扫描段补偿（`chroma_blur` / `auto_levels`）
  `spektra.py`   vendored 胶片引擎（0.3.4）的适配层
  `lighttype.py` 光位标签
  `pipeline.py`  编排 —— **一条链跑完**
  `service.py`   常驻服务（**唯一**开出图缓存的地方）
  `selftest.py`  自检（`python -m svFilm.selftest`）
  `color.py` / `hist.py` / `cameras.py` / `cli.py` / `paths.py` / `scorecard.py`  工具与入口

另外两条（**不变**）：
  * 机器差异只关在 `io.py`（入口一个关口）里，其余模块必须与机型无关。
  * 一切可调参数只在 `config.py`，其它文件不许出现魔法数字。
"""
from . import config  # noqa: F401
from .pipeline import run, Result  # noqa: F401

__version__ = config.VERSION
__all__ = ['run', 'Result', 'config', '__version__']
