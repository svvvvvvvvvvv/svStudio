# ⚠⚠ 09-28 瘦身：**入口成形（entry_tone）已整段删除** ⇒ 本文件里给入口用的那几个函数
#   （ /  /  / ）**现在没人调用了**。
#   原因：A/B/C 实测 —— 关掉入口成形输出逐位不变 ⇒ 落点/跨度已被引擎
#   （camera.auto_exposure + H&D 显影 + scanner 黑白点）接管。
#   ⚠ **标定数据先留着不删**（是实测标定数据，将来若把落点搬进引擎还会用到）。

# -*- coding: utf-8 -*-
r"""IDT 机型表 —— **加机型不改代码**，只在这里加一行。

为什么要这张表：
  相机厂商**故意让 RAW 欠曝**，把"把中点抬回来"这一步留给转换器做。
  这一步在 DNG 规范里叫 **baseline exposure**；Adobe 系（ACR / Lightroom）都是"静默"做掉的
  （滑块显示 0，其实已经抬过）。RawTherapee 官方文档：**目的就是让亮度匹配机内 JPEG**。
  LibRaw 作者 Alex Tutubalin 的原话：相机灰点通常只在满量程的 7~12%，转换器要把它抬回 18%，
  「0.7~1.5EV gray point move is usual」，而且**要用一条自定义 tone curve 把高光压下去**。

表里存两样东西，**别混**：
  1) `baseline_ev`：与该**机型**绑定的常数基线。
  2) 富士的 DR 档额外欠曝 —— 这一项**每张都可能不同**，逐张从 RAF 的 MakerNote 里读
     （09-30：`rawmeta` 已随那条入口路一起删除；本表现在只填报告的机型名）。
     最终补量 = baseline_ev + FUJI_DR_BIAS[dr]；若机身写了精确的
     `0x9650 RawExposureBias`，**直接用那个值覆盖**（它是含基础偏移的总量）。

实测记录（2026-09-13）：
  园岭 32 张全是 X-T30 III + DR400 ⇒ 补 +2.72EV。补完底与机内 JPEG 对齐
  （DSCF1010 中位 59 vs 66、DSCF1032 71 vs 79；批量 16 张中位差从 −3.8 档收到 −0.84 档）。
  ⚠ 同一台机身 DR 会变：整库 759 张里 **DR100 118 张 / DR200 66 张 / DR400 575 张** ⇒
    逐张读。按机型写死 DR400 会错掉那 184 张。
  ⚠ 旧记录「X-T30 III 解码本身已含 0.18 中灰标定，baseline_ev = 0」是**错的**：
    当时只看了两张"标准曝光"的样张，恰好是 DR100 或恰好落在 0.18，样本太少把结论带偏了。

实测记录（2026-09-13 夜，X100VI）：
  `D:\PhotoLib\爆光修复\x100vi` 6 组同名 RAW+机内 JPG（全 DR400，跨 8/31、9/6、9/7 三批）
  ⇒ 量出 **DR400 曝光零点 +2.18EV**（X-T30 III 是 +2.184，两台富士对上），
  **高光端增益从 ×9.79 滚回 ×1.0**（自带肩部）。补量后 X100VI 不再退化成 +2.72EV 的平直增益。

怎么填 `baseline_ev`：
  用 `python -m svFilm.cli calib <RAW+JPG 同名的若干张>` 会打印建议值。
  优先拿"曝光标准"的样张（中灰落在 18% 上的那种），别拿夜景。
"""
from __future__ import annotations

# key = (make, model) 小写；只写 model 也能命中
# 0.72 = 富士的基础偏移（darktable：even at 100DR there is an EV shift −0.72）。
TABLE = {
    'x-t30 iii': dict(baseline_ev=0.72, note='富士基底 0.72；DR 额外量逐张读 tag'),
    'x-t30': dict(baseline_ev=0.72, note='同 X-T30 III，待复核'),
    'x100vi': dict(baseline_ev=0.72, note='40MP；富士基底 0.72，DR 逐张读'),
    'ilce-7m3': dict(baseline_ev=0.0, note='▲ 未标定。LibRaw 说多数机身偏 0.7~1.5EV，别猜，跑 calib'),
    'ilce-7m4': dict(baseline_ev=0.0, note='▲ 未标定。同上'),
}

DEFAULT = dict(baseline_ev=0.0, note='未知机型 ⇒ 不猜，常数项按 0')

# 富士「动态范围」档位的**额外**欠曝（不含那 0.72 的基底）。
# 来源：darktable 官方 lua 脚本 fujifilm_dynamic_range 的实测值
#   RawExposureBias 总量：100 DR -> −0.72 EV ／ 200 DR -> −1.72 EV ／ 400 DR -> −2.72 EV
# 减去基底 0.72 ⇒ 额外量就是 0 / 1 / 2 EV。
# 同口径：RapidRAW issue #711（DevelopmentDynamicRange 100/200/400 -> 0/+1/+2 EV）。
FUJI_DR_BIAS = {100: 0.0, 200: 1.0, 400: 2.0}



def lookup(make=None, model=None):
    keys = []
    if make and model:
        keys.append(('%s %s' % (make, model)).strip().lower())
    if model:
        keys.append(str(model).strip().lower())
    for k in keys:
        if k in TABLE:
            return dict(TABLE[k], key=k, hit=True)
    return dict(DEFAULT, key=None, hit=False)
