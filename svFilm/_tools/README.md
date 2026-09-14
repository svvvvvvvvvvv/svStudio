# _tools —— 随仓库带的第三方依赖

## spektrafilm

**这是什么**：一套物理的胶片模拟引擎（真的胶片相机 + 放大机模型，不是"套滤镜"）。
svFilm 的**真卷**（portra400 / fuji_c200 / pro400h / ektar100 / cinestill800t）就是靠它算的。

| | |
|---|---|
| 上游 | https://github.com/andreavolpato/spektrafilm |
| 版本 | commit `3bb2c2d2801ff68b92019cf1dbcbb133d60832bc`（2026-09-14 取） |
| 许可 | **CC BY-SA 4.0** —— 见 `spektrafilm/LICENSE` 与 `spektrafilm/SPEKTRAFILM_LICENSE.txt` |
| 改动 | **无。原样拷的**（上游 tracked 文件 428 个，`git archive` 出来的），只为"clone 完就能跑" |

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
