# _tools —— 随仓库带的第三方依赖

## spektrafilm

**这是什么**：一套物理的胶片模拟引擎（真的胶片相机 + 放大机模型，不是"套滤镜"）。
svFilm 的**卷**（`data/presets/*.json` 那 9 条，09-23 起）就是靠它算的。

| | |
|---|---|
| 上游 | https://github.com/andreavolpato/spektrafilm |
| 版本 | **0.3.2**（`src/spektrafilm/` 逐字取自 `E:\spektrafilm-public`） |
| 许可 | **CC BY-SA 4.0** —— 见 `spektrafilm/LICENSE` 与 `spektrafilm/SPEKTRAFILM_LICENSE.txt` |
| 改动 | **无。原样拷的**，只为"clone 完就能跑" |

> ★★ **版本必须和"预设是从哪一版标出来的"一致。** 那 9 条预设是在 **0.3.2** 上标定的
> （`%LOCALAPPDATA%\napari\napari\presets\` 里那份，同一版）。
> 09-24 实测换到 0.3.4 之后：**肤色色相角从 63° 偏到 82°（发黄发绿）**，
> 中位低 2.8、亮部低 3.3、彩度 P90 高 20%。换回 0.3.2 后 11.7/69.5/89.8，与验收版 11.6/69.3/89.8 一致。
> ⚠ 换版本时 **`svFilm/svFilm/presets.py::_apply` 里的颗粒字段名要跟着换**
> （0.3.2 = `agx_particle_*`、0.3.4 = `particle_*`）；写错**不报错**，颗粒会静默失效。
> `selftest.t_presets` 有一条专门盯这个。
>
> ⚠ 0.3.2 的 `src/spektrafilm/data/license/` 下**没有** `SPEKTRAFILM_LICENSE.txt`（0.3.4 才加的）
> ⇒ 换版本时那一条被删了。**许可是好的**：本目录根下的 `LICENSE`（GPL-3）、
> `SPEKTRAFILM_LICENSE.txt`（profiles / LUT 的 CC BY-SA 4.0）与 `CITATION.cff` 都还在。

**为什么直接带进仓库**：目标就是"拉一个仓库全下来"。少了它，真卷直接
`ModuleNotFoundError`；让每个人自己去 clone 一遍上游并不省事。

**引擎怎么找它**（见 `../svFilm/spektra.py` 的 `_sf()`，第一个存在的胜出）：

1. 环境变量 `SPEKTRAFILM_ROOT`
2. **本目录**（`svFilm/_tools/spektrafilm/src`）
3. 仓库**上一级**的 `_tools/spektrafilm/src`（老布局，兼容用）

⇒ 正常情况下**什么都不用配**。

**要升级**：把上游新版本的 tracked 文件重新 `git archive` 拷进来覆盖，更新上表的 commit，
然后跑 `python -m svFilm.selftest` + 端到端出一张真卷图。

⚠ **分发注意**：本目录是 **CC BY-SA 4.0**。单独分发 svStudio 按根目录的 MIT；
**一旦和它打包在一起分发**，那一份组合分发要遵守 CC BY-SA 4.0（署名 + 相同方式共享）。
