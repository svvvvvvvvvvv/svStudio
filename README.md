# svStudio

**工作台。** 只管"给人用"，不管算法。

```
svFilm       纯引擎（没有界面、没有弹窗）      喂一张图 + 一组参数 → 出一张图
   ↑ 调用（本机 HTTP，常驻）
svStudio     工作台（这里）                    人在这儿挑图、挑卷、调参、出片
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

## 本机路径

```bash
# ★ 引擎必须用 spektrafilm 那个 venv（default 缺 colour 库，一 render 就 ModuleNotFoundError）
PY_ENGINE=C:/Users/user/.workbuddy/binaries/python/envs/spektrafilm/Scripts/python.exe
PY=C:/Users/user/.workbuddy/binaries/python/envs/default/Scripts/python.exe   # 普通脚本用这个
GIT=C:/Users/user/.workbuddy/binaries/PortableGit/versions/1.2.0/cmd/git.exe  # 本机 git 用 WorkBuddy 自带那份
# 引擎在 ../svFilm
```

## 现状

- [x] 目录 + git
- [x] svFilm 常驻服务（`/scan` `/load` `/render` `/stocks` `/bases` `/params`）
- [x] **搬 pickstation 的选片台进来**（Electron 33 + 原生 HTML/JS）
- [x] **调色台并进同一个窗口**（顶栏 tab / 分屏 / 按主题存参数 / 引擎自启）
- [ ] 出片（把调好的参数写成一条命令，交引擎批量跑全主题）
- [ ] 打包（模型随包、依赖可复现）

## ⚠ 启动 svStudio 的一个坑（Electron 开发者注意）

本机 shell 里如果带着 `ELECTRON_RUN_AS_NODE=1`，Electron 会**退化成普通 node**，
`require('electron')` 拿到的是 npm shim 的字符串路径（不是 API 对象）⇒
主进程第一行就 `Cannot read properties of undefined (reading 'whenReady')`。
双击 `.bat` 不受影响（那是干净环境）；在终端里手动起要 `set ELECTRON_RUN_AS_NODE=` 先清掉。
