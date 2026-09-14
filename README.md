# svStudio

**工作台。** 只管"给人用"，不管算法。

```
svFilm       纯引擎（没有界面、没有弹窗）      喂一张图 + 一组参数 → 出一张图
   ↑ 调用（本机 HTTP，常驻）
svStudio     工作台（这里）                    人在这儿挑图、挑卷、调参、出片
```

## 仓库结构

`svFilm` 引擎用 **git subtree** 合在本仓库的 `svFilm/` 子目录里 ⇒ **clone 一次全下来**，
不用再单独拉一个仓库。

```
svStudio/
├─ main.js  preload.js   Electron 主进程 / 安全桥（preload 里的 IPC 通道是契约，别乱改）
├─ src/                  React 界面（Vite lib 模式打成 IIFE）
├─ renderer/index.html   手写的入口页（不经 Vite —— 原因见 src 里的注释）
├─ _check/               自检：静态检查 + 真浏览器布局检查
├─ svFilm/               ← 引擎（subtree，来自独立的 svFilm 仓库）
│    ├─ svFilm/          Python 包本体（pip 不用装，靠 cwd 里 `python -m svFilm.service`）
│    ├─ LICENSE          MIT（+ 关于 spektrafilm 依赖的提示）
│    └─ README.md        引擎自己的文档
├─ LICENSE               MIT
└─ 启动svStudio.bat      双击开台子
```

### 从零跑起来

```bash
npm install                 # 装依赖（Electron / Vite / React ...）
npm run ui:build            # 构建界面产物（renderer/dist/）
双击 启动svStudio.bat        # 选片台直接能用；调色台点「渲染」会自己把引擎拉起来
```

引擎需要一份装了这些的 Python（`colour` / `rawpy` / `numpy` / `Pillow` / `opencv-python`）：

```bash
pip install colour-science rawpy numpy Pillow opencv-python
```

- 真卷引擎 **spektrafilm 已经随仓库带在 `svFilm/_tools/spektrafilm/`**，不用自己装；
  想用别处的，设 `SPEKTRAFILM_ROOT=<...>/spektrafilm/src` 覆盖。
- 有了这份 Python，**真卷**（portra400 等）才能渲；只有 `neutral` 不需要它。
- 用哪份 Python 见下面「引擎用哪份 Python」。

### 改完怎么自检

```bash
npm run verify   # tsc 类型检查 + 构建 + 静态检查 + 真浏览器布局检查（本机 Edge）
```

## 两个台（同一个窗口，顶栏切换）

| 台 | 干什么 |
|---|---|
| **选片台** | 浏览主题、看大图、打星、按星级归位 |
| **调色台** | 挑胶片卷 + 成色基准、调参数、**左右分屏看原图 vs 渲染**、出片 |

**两个台共用同一套布局与操作**：左栏 = 切图库（拍摄主题）｜中间 = 大图｜右栏 = 参数
（选片台放**照片参数**，调色台放**调色参数**）｜底部 = 缩略图列 +（调色台）渲染按钮。

### 怎么开
- **`启动svStudio.bat`** ← 双击这个。选片台直接就能用。
- 调色台第一次点「渲染」时，**台子会自己把 svFilm 引擎拉起来**（后台，不弹窗），
  起了之后这台机器上就一直常驻了。
- 要单看引擎日志 / 单独重启引擎：双击 **`启动调色台.bat`**。

## 调色的参数存在哪

**按主题存**（`config.grades[主题名]`）：换主题再回来，那套卷/基准/滑杆还在原地。
右栏底部两个按钮：**恢复默认**（回到 Portra 400 + 全对齐基准 + 出厂滑杆值）、
**存到主题**（把当前这套钉进本主题）。

## 边界（写死，别再缠在一起）

- **`svFilm` 不许有界面** —— 不许弹窗、不许读配置 UI、不许假设"用户在看"
- **`svStudio` 不许有算法** —— 影调/颜色/颗粒**一行都不许在这里**，全走引擎
- **`svStudio` 不许直接读 RAW 做处理** —— 解码是引擎的事（它才持有缓存）

★ 判断标准很简单：**"换个前端还要不要它？"** —— 要，就是引擎的；不要，就是这里的。

## 实测速度（2026-09-14，暗房特效全开）

| 动作 | 耗时 |
|---|---|
| 引擎冷启（含 import 模型） | 几秒 ~ 几十秒 |
| 抖一张 RAW 进引擎（解码 + 入口） | **~2.3 s** |
| 换一次卷 / 改一次参数出图 | **~6.5~7 s** |

⚠ 比早先记录的 1.45 s 慢 —— 因为**暗房 6 组特效现在全开**（印相曲线变形 / 柔光 /
高光增亮 / 黑白位校正 / 像差模糊 / 扫描锐化），换卷时要重跑卷积。
**效果多 = 换卷慢**，这个取舍是有意的。

## 引擎用哪份 Python

**不写死在仓库里**（每人机器不一样）。按顺序找：

1. 环境变量 `SVFILM_PY`（推荐，设一次然后重开窗口）：
   `setx SVFILM_PY "D:\Python\venvs\svfilm\Scripts\python.exe"`
2. 配置里的 `enginePy`（在用户目录的 `config.json` 里，**不进仓库**）
3. PATH 上的 `python`

那份 Python 得有：`colour-science` / `rawpy` / `numpy` / `Pillow` / `opencv-python`，
以及 `pip install -e spektrafilm`。
**用错环境的表现**：引擎起得来、`/health` 也正常，但一 `/render` 就
`ModuleNotFoundError: No module named 'colour'`。

> 作者本机的做法：在 `%APPDATA%\svStudio\config.json` 里写
> `"enginePy": "<venv>\Scripts\python.exe"`。

## 现状

- [x] 目录 + git
- [x] svFilm 常驻服务（`/scan` `/load` `/render` `/base` `/stocks` `/bases` `/params`）
- [x] **界面重写为 React + Radix Themes**（Vite lib 模式；旧的 `renderer/app.js`、`renderer/style.css` 已删）
- [x] 左栏固定图库目录 / 五段布局 / 大图 / 详情右栏（全部 EXIF）/ 底栏缩略图（虚拟滚动 + 悬浮预览）
- [x] **调色台并进同一个窗口**（顶栏 tab / 分屏 / 按主题存参数 / 渲染条 / 引擎自启）
- [x] **svFilm 用 git subtree 合进 `svFilm/`** ⇒ clone 一次全下来
- [x] 自检工具（`npm run verify`：类型 + 构建 + 静态 + 真浏览器布局）
- [ ] 出片（把调好的参数写成一条命令，交引擎批量跑全主题）
- [ ] 打包（模型随包、依赖可复现）

## 许可证

| 目录 | 许可 | 说明 |
|---|---|---|
| 根目录（svStudio 工作台） | **MIT** | 见 `LICENSE` |
| `svFilm/`（引擎） | **MIT** | 见 `svFilm/LICENSE` |
| `svFilm/_tools/spektrafilm/`（引擎的第三方依赖，随仓库带上） | **CC BY-SA 4.0** | 见它自己的 `LICENSE` / `SPEKTRAFILM_LICENSE.txt`，**原样引用、未改动** |

⇒ 单独用 svStudio 按 MIT。**一旦把 spektrafilm 和它打包在一起分发**，
那一份组合分发要遵守 CC BY-SA 4.0（署名 + 相同方式共享）。

`svFilm/_models/` 下两个模型来自 MediaPipe（Apache-2.0）。

## ⚠ 启动 svStudio 的一个坑（Electron 开发者注意）

本机 shell 里如果带着 `ELECTRON_RUN_AS_NODE=1`，Electron 会**退化成普通 node**，
`require('electron')` 拿到的是 npm shim 的字符串路径（不是 API 对象）⇒
主进程第一行就 `Cannot read properties of undefined (reading 'whenReady')`。
双击 `.bat` 不受影响（那是干净环境）；在终端里手动起要 `set ELECTRON_RUN_AS_NODE=` 先清掉。
