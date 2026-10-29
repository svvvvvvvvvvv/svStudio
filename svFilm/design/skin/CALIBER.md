# 肤色口径（v7）—— 一个词只有一个意思

## 一句话

**肤色 = 脸颊 ROI 内、排除镜面与阴影之后、L\*/a\*/b\* 各自的中位。**
附带一个文献标准的单值：**ITA° = arctan((L\*−50)/b\*) × 180/π**。

## 四步与出处

| 步 | 做什么 | 出处 |
|---|---|---|
| 1 掩膜 | `selfie_multiclass` 第 3 类（脸皮肤）—— 排除手（第 2 类）、头发（第 1 类）、衣服、背景 | MediaPipe 官方模型（仓库自带 `_models/selfie_multiclass_256x256.tflite`） |
| 2 ROI | 眼-嘴中点外移 0.15 脸宽，椭圆半径 0.13 脸宽 | 皮肤色测量惯例：颊/额 ROI；ROI 不许压眉/发（[综述](https://duepublico2.uni-due.de/servlets/MCRFileNodeServlet/duepublico_derivate_00081430/Diss_Wuerich.pdf)） |
| 3 剔除 | L 上 20% 分位（**镜面**）· 下 10% 分位（阴影）· 偏离中位 ΔE>10（离群） | "镜面分量反映的是光源色、比皮肤亮 ⇒ 剔掉"（[arXiv 2104.02926](https://export.arxiv.org/pdf/2104.02926)） |
| 4 统计 | **分量中位**（component median），不是均值、不是单像素 | [JP7539917B2](https://patents.google.com/patent/JP7539917B2/en) 的 component median approach |
| 5 弃权 | 有效像素 < 300，或脸宽 < 40 px（1080 域） | 工程值：300 px 才能让中位稳到 ±0.5 L\* |

## ITA° 分级（文献常用）

```
>55 很白 · 41~55 白 · 28~41 中 · 10~28 棕 · −30~10 深 · <−30 黑
```
出处：[J-STAGE 皮肤色与 ITA](https://www.jstage.jst.go.jp/article/bioimages/28/0/28_1/_pdf)、
[US20090196475A1](https://patentimages.storage.googleapis.com/cd/ea/2c/09e2078121523b/US20090196475A1.pdf)

## ★ 一条限制（必须记住）

数码影像的 L\*/b\* 与色度计有系统差（[数码 vs 色度计](https://www.jstage.jst.go.jp/article/jsapmeeting/2018.1/0/2018.1_960/_pdf/-char/en)）。
我们**没有色卡** ⇒

> **绝对 L\* 只能在本链路内横比（① vs ③、这批 vs 那批），不能拿文献的 ITA 分级当靶。**

用它判"脸够不够亮、血色够不够"是合法的；用它判"这人的皮肤属于 ITA 哪一档"不合法。

## 与 v6 的关系

v6（四条证明单点）留在 `_corpus/kurai/full/skin_point.py::sample_skin_v6`，**只做 A/B 与回退**。
两法在"脸大小适中、光比较小"的图上差 ≤2.5 L\*；在**阳光直照**的图上系统差 5~14 L\*：

- v6 取"最平整的一块皮肤"（避开最亮 8%）→ 像"皮肤的固有色"
- v7 取"整块受光颊的中位" → 像"人眼看到的这张脸多亮"

判"脸够不够亮"用 v7；比"换算法前后"必须同法，不许混。
