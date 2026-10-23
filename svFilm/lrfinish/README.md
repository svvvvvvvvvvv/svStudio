# lrfinish — LR 收尾（`svFilm/lrfinish`）

Lightroom 侧的收尾工序：**主曲线 / 通道曲线 / 分级 / HSL / 肤色护栏**。
不是引擎（引擎在 `svFilm/svFilm/`），也不做磨皮/液化/局部塑形。

> **本目录只放工程代码。**
> 设计、调研、迭代记录、历史实验脚本统一放在仓库外：
> `E:\Debug_svStudio\lr_agent_docs\`（原 `E:\lr_agent` 的 md / research / skills）。
> 旧路径 `E:\lr_agent` 仍是指向本目录的 junction。

## 目录

| 路径 | 作用 |
|---|---|
| `opt/` | 当前求解器：`layer1.py`（主曲线）、`layer2.py`（通道曲线）、`layer5.py`（分级）、`band_read.py`、`_probe_clip.py`、`lr_measure.py`、`apply_layer2.py` 等 |
| `prototype/` | LR 桥客户端（`bridge_client.py`）、启动（`lr_boot.py`）、插件部署（`deploy_plugin.py`）；其余为历史实验脚本，待拆 |
| `lrplugin/SvFilmBridge.lrplugin/` | LR 插件（Lua），部署到 `%APPDATA%\Adobe\Lightroom\Modules` |
| `lr_cmd/` | 桥的文件队列（运行时） |
| `out/` | 运行日志（不进 git） |

## 运行（当前原型）

```powershell
$py='C:\Users\psw99\.workbuddy\binaries\python\envs\spektrafilm\Scripts\python.exe'
$env:PYTHONPATH='E:\lr_agent\_pylibs'
# 新批次先入库（否则 SELECT/EXPORT 静默失败）
& $py -u opt/_add_batch.py <底片目录>
# 逐层跑（每层自己 boot LR）
& $py -u opt/layer1.py --batch <底片目录>
$env:LR_ROOT='E:\Debug_svStudio\lr_agent_out\<批次>'
& $py -u opt/layer2.py
& $py -u opt/layer5.py
```

## 约定

- 曲线点数 **≤20**（LR 静默截断，25 点画面塌陷）；通道曲线用 12 点平台。
- 曝光键必须 `Exposure2012`（`Exposure` 是死键，读回会撒谎）。
- 写完必须**导出复量**，不看读回值。
- 不动作要记账（`zero_action` / `scene_given` / `upstream`）。
- 多预设（鹿井 / 滨田）目标：`profiles/<id>/profile.json`，加预设只加数据；当前代码还没迁到 profile，见外部设计文档。
