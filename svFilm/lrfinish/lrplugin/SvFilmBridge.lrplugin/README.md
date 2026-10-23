# svFilm Bridge（Lightroom Classic 插件，草案）

读 `*.svfilm.lr.txt`，把 `lr.settings` 应用到选中的照片；可选导出 JPEG 到 `lr_export/`。

> **状态：未在 Lightroom 里实际加载/运行过**（本机 LR 15.1 已装，但没有在本次会话里启动）。
> 先用虚拟副本/测试目录试，不要直接对原片批量跑。

## 安装

1. 把整个 `SvFilmBridge.lrplugin` 文件夹复制到：
   `%APPDATA%\Adobe\Lightroom\Modules\`
2. 重启 Lightroom Classic。
3. `File → Plug-in Manager` → 左侧应出现 **svFilm Bridge** → 确认启用。
4. 在 Library 里选中 `*_flat.tif`（同目录要有 `*.svfilm.lr.txt`）。
5. 菜单 `Library → Plug-in Extras → svFilm: Apply metadata to selected photos`。

## 两个菜单

| 菜单 | 做什么 |
|---|---|
| **Apply metadata** | 只应用 settings，不导出 |
| **Apply + Export JPEG** | 应用后导出 JPEG（92 质量 / 长边 2048 / sRGB）到源目录 `lr_export/` |

## 元数据从哪来

`svFilm.cli lrbase` 会为每张图生成：

```
DSCF1150_svFilm_flat.tif
DSCF1150_svFilm.svfilm.lr.txt   ← 插件读这个
DSCF1150_svFilm.svfilm.json
DSCF1150_svFilm.xmp
DSCF1150_svFilm.lrpreset.xmp
```

插件按 `*_flat.tif` → `*_flat` 去掉 `_flat` → `*.svfilm.lr.txt` 查找。
找不到就跳过并报“缺元数据”。

## 格式

`*.svfilm.lr.txt` 是逐行 `key=value`：

```
schema=svfilm.lr/1
mode=flat
preset_name=svFilm Portra400 Endura base
ToneCurveName2012=Custom
ToneCurvePV2012=0,0,17,10,...
Exposure2012=0.0
...
```

- `ToneCurve*` = 逗号分隔的扁平 `x,y,x,y,...`（0–255）。
- 其它数值走 `tonumber`；`schema/mode/preset_name/ToneCurveName2012` 当字符串。
- 插件不解析 JSON、不执行 Lua。

## 注意

- `lr.settings` 是**最终扁平表**：基底曲线 + 大师曲线已经 compose 好；**不要**再套另一个胶片 preset。
- 颗粒/光晕已经烘进平基底片；`GrainAmount=0` 是故意的。
- `applyDevelopSettings` 是 SDK 的 experimental API；LR 升级后键名可能变。
- 插件没有做快照/虚拟副本保护；批量前请自己备份或先选虚拟副本。
- 导出设置写死在 `ApplyAndExport.lua`，要改长边/质量/格式直接改那几行。
