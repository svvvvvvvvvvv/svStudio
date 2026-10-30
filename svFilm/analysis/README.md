# analysis —— 测量/分析侧的可复现部分

> 这里是**从工作目录搬进来的、值得进版本库的部分**：共享库、测量脚本、测量结果。
> 图片（大师成片、渲染输出）**不进来**；工作目录（`E:\ds\photo`）里仍保留原件与缓存。

## 结构

```
lib/                 ← E:\ds\photo\_lib（render / measure / cache / bands / verdict / label / jsonsafe / codecheck）
corpus/kurai/        ← E:\ds\photo\_corpus\kurai\full 里的 *.py 与关键 json
                        measure_all.py · skin_point.py · split_b.py · cards_v4.json · m_*_v4.json · split_b_*.json
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
