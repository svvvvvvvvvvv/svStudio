# lrfinish — LR 收尾（`svFilm/lrfinish`）

Lightroom 侧的收尾工序：**主曲线 / 通道曲线 / 分级 / HSL / 肤色护栏**。
不是引擎（引擎在 `svFilm/svFilm/`），也不做磨皮/液化/局部塑形。

> **本目录只放工程代码。**
> 设计、调研、迭代记录、历史实验脚本统一放在仓库外：
> `E:\Debug_svStudio\lr_agent_docs\`。
> 旧路径 `E:\lr_agent` 仍是指向本目录的 junction。

## 预设是统一的：一个目录 = 胶片 + 收尾

预设和胶片预设放在一起，不另起一套：

```
svFilm/svFilm/data/presets/
├─ 鹿井预设/
│  ├─ preset.json      胶片/介质参数（引擎读；原来的 鹿井预设.json）
│  ├─ finish.json      LR 收尾参数（本工具读）
│  ├─ bands.json       五段靶带
│  └─ calibration/     分级关系 / 轮增益
├─ 滨田预设/
│  ├─ preset.json
│  ├─ finish.json       （draft：靶带已量，tone/grading 未标定）
│  ├─ bands.json
│  └─ calibration/
└─ xhs预设.json         旧格式：只有胶片，没有 finish
```

- 名字是中文（`鹿井预设`），id 是 ASCII（`kurai`/`hamada`）；`--profile` 两种都认。
- 加一个新预设 = 加一个目录 + `finish.json`，**不改代码**。
- 引擎 `svFilm.presets` 同时认单文件（旧）和目录（新）。

## 目录

| 路径 | 作用 |
|---|---|
| `run.py` | 统一入口：`--profile` / `--batch` / `--out-root` / `--stages` |
| `paths.py` | 所有路径的唯一入口（环境变量覆盖，代码里不写绝对路径） |
| `profile_io.py` | 读预设：按 `finish.id` 找目录，取 bands/calibration |
| `opt/` | `basic.py`（LR Basic 落点）、`look_stage.py`（curve + HSL + 分级）、测量/写库 |
| `prototype/` | LR 桥（`bridge_client.py`）、启动（`lr_boot.py`）、插件部署 |
| `lrplugin/SvFilmBridge.lrplugin/` | LR 插件（Lua） |
| `lr_cmd/`、`out/`、`_masktest/`、`_pylibs/`、`_wheels/` | 运行时/媒体，不进 git |

## 运行

```powershell
$py='C:\Users\psw99\.workbuddy\binaries\python\envs\spektrafilm\Scripts\python.exe'

# 鹿井
& $py -u svFilm\lrfinish\run.py --profile kurai --batch <底片目录> --out-root <产物根>

# 滨田（同一批底片，只换预设；draft 会警告）
& $py -u svFilm\lrfinish\run.py --profile hamada --batch <底片目录> --out-root <产物根>

# 只跑某一层 / 先看命令
& $py -u svFilm\lrfinish\run.py --profile kurai --batch <dir> --stages basic,look_stage --dry-run
```

## 约定

- 曲线点数 **≤20**（LR 静默截断，25 点画面塌陷）；通道曲线用 12 点平台。
- 曝光键必须 `Exposure2012`（`Exposure` 是死键，读回会撒谎）。
- 写完必须**导出复量**，不看读回值。
- 不动作要记账（`zero_action` / `scene_given` / `upstream`）。
- 路径只走 `paths.py`；换机器只改环境变量（`LRFINISH_DEBUG`、`LRFINISH_LR_EXE` 等）。
