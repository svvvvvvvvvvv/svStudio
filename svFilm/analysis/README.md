# analysis —— 测量/分析侧的可复现部分

> **分析产物，不是设计文档。** 设计唯一真源是 `svFilm/design/设计真源.md`。

> 这里是**从工作目录搬进来的、值得进版本库的部分**：共享库、测量脚本、测量结果。
> 图片（大师成片、渲染输出）**不进来**。
> ⚠ **2026-10-30：工作目录 `E:\ds\photo` 已整体删除（36.6 GB）**——这里就是唯一真源。
> 脚本里残留的 `E:\ds\photo\...` 绝对路径是历史路径：图片在 `E:\ds\大师成片`，测量 json 在本目录。

## 结构

```
lib/                 ← E:\ds\photo\_lib（render / measure / cache / bands / verdict / label / jsonsafe / codecheck）
corpus/kurai/        ← E:\ds\photo\_corpus\kurai\full 里的 *.py 与关键 json
                        measure_all.py · skin_point.py · split_b.py · cards_v4.json · m_*_v4.json · split_b_*.json
                        v0.3：card_content.json（64 目录内容卡）· card_content_manual.json（5 人标）
                              photos_tone_v04.json（765 张，1080 交付域 + 颗粒诊断）
                              photos_tone_v03.json（旧尺子，只留档）
                              build_card_content.py · build_photos_tone.py · build_cards_v6.py
                              texture_probe.py（颗粒度量/pilot）· sync_grain_from_stock.py
                              cards_v6.json（从 v04 重建）· cards_v5.json（旧 v7 尺子，回滚用）
                              media/（每卡介质）· media_grain_backup.json（rms 同步前备份）
data/                ← E:\ds\photo\_run_1030_card004 里的条件表与对照测量
                        cond_table_*.json · cond_bands_v2.json · m_ours_v17_v4.json
```

## 规矩

```
· 代码/测量结果的**真源就在这里**；工作目录里的是运行副本
· 图片、缓存、日志、一次性的 *.err/*.log/*.html 一律不进
· 换了尺子（measure_sha 变）⇒ 三态一起重测，把新 json 覆盖进来
```

## 和 `design/` 的分工

```
design/     设计（怎么想、写成规则）
analysis/   测量（怎么量、量出什么数）
```
