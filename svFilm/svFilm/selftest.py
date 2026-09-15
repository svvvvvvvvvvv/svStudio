# -*- coding: utf-8 -*-
"""自检 —— 动完任何一层都要跑。不依赖任何样片，纯合成图 + 不变量。

  python -m svFilm.selftest
"""
import os
import struct
import sys
import tempfile

import numpy as np

from . import (analyze, cameras, color, config as C, denoise, face, film, guard, io, local, metrics,
               pipeline, rawmeta, spatial, spektra, stocks, style, tone)

FAIL = []


def check(name, cond, extra='', why=''):
    """`extra` = 现场（成败都打）；`why` = **红了意味着什么**（只在红时打）。

    ★ 为什么加第 4 个参数：工作台那份自检（`_check/ui_smoke.mjs`）一直是这么写的，
      而"失败信息必须写清**这条红了代表什么坏了**"是本项目反复吃过的亏 ——
      只写 `FAIL ★ 换纸没换画面`，下一个人根本不知道该去改哪儿
      （实测就因为只写了"复位坏了"，差点去改错地方：真因是 `setPointerCapture` 把按钮 click 吃了）。
      引擎这边一直只能把它塞进 `extra`，结果是"绿的时候也在喊狼来了"。
    """
    print(('  ok   ' if cond else '  FAIL ') + name
          + (('   ' + extra) if extra else '')
          + (('   ⇒ ' + why) if (why and not cond) else ''))
    if not cond:
        FAIL.append(name + ((' — ' + why) if why else ''))


class _Cfg:
    """config 的只读副本 + 覆盖几项 —— 给"开关类"测试用，不动全局 config。

    ⚠ 单测隔离（09-13 踩到）：**默认把「基准成色」置成不动**。
    出厂默认改成 `BASE_FULL`（SV 拍板）之后，这里若跟着默认走，
    "contrast=1.0 完全不动""支点 L*=50""neutral 卷 = 恒等"三条会一起变红 ——
    那不是回归，是**测试测错了对象**（测的是"出厂默认"而不是"这一层"）。
    要连着基准一起测，显式传 `_Cfg(BASE='BASE_FULL')`。
    """

    def __init__(self, **kw):
        self._d = {k: getattr(C, k) for k in dir(C) if k.isupper()}
        self._d['BASE'] = 'BASE_NONE'
        # 抽色饱和（CHROMA_ENDS）也是"出厂模型默认"，同样要隔离：
        # 否则 "contrast=1.0 时完全不动" 会因为多跑一次 Lab 往返而红。
        self._d['CHROMA_ENDS'] = 0.0
        # 肤色正向达标（L3 第二件事）也是"出厂默认"，同理隔离：
        # 否则任何一张带肤色像素的测试图都会被它多推一次色度。
        self._d['SKIN_FLOOR'] = False
        # 胶片影调曲线（L2 的 A 档）也是"出厂默认"，同理隔离：
        # 否则 "contrast=1.0 时完全不动" / "neutral 卷 = 恒等" 会因为中灰被抬而红。
        # 要单独测它，显式传 _Cfg(TONE_CURVE=True, TONE_TOE=..., TONE_LIFT=...)。
        # ⚠ 肩部（TONE_SHOULDER）出厂默认=2（09-13 深夜 SV 定「B 档」，由 4 放开），**也必须隔离**：
        #   否则凡是"拿 _tone_lut(...) 算期望值再和 style.apply 比"的用例都会错位。
        self._d['TONE_CURVE'] = False
        self._d['TONE_SHOULDER'] = 0.0
        self._d.update(kw)

    def __getattr__(self, k):
        try:
            return self._d[k]
        except KeyError:
            raise AttributeError(k)


def _span90(disp):
    L = color.to_lab(np.clip(disp, 0, 1))[..., 0]
    return float(np.percentile(L, 95) - np.percentile(L, 5))


def _gray_img(h=240, w=360, gamma=0.45, seed=0, tint=(1.0, 1.0, 1.0)):
    rng = np.random.default_rng(seed)
    y = np.linspace(0.0, 1.0, h)[:, None] * np.ones((1, w))
    y = np.clip(y ** gamma, 1e-4, 1.0)
    img = np.repeat(y[..., None], 3, axis=-1)
    img *= np.asarray(tint)
    img += rng.normal(0, 0.002, img.shape)
    return np.clip(img, 0.0, 1.0)


def t_color():
    print('[color]')
    x = np.linspace(0.0, 1.0, 999)
    e = float(np.max(np.abs(color.l2s(color.s2l(x)) - x)))
    check('sRGB 编解码往返', e < 1e-9, 'max err %.2e' % e)

    rgb = np.random.default_rng(1).random((40, 60, 3))
    e = float(np.max(np.abs(color.from_lab(color.to_lab(rgb)) - rgb)))
    check('Lab 往返', e < 2e-3, 'max err %.2e' % e)

    check('灰度分位单调', color.gray_of(_gray_img(gamma=1.0))[0, 0] < color.gray_of(_gray_img(gamma=1.0))[-1, 0])


def t_analyze():
    print('[L0 analyze]')
    d = _gray_img(gamma=1.0)
    rep = analyze.analyze(color.s2l(d), d, 'jpg')
    a, b = rep['gray_pcts'][C.PCT_BLACK], rep['gray_pcts'][C.PCT_WHITE]
    check('分位有序', a < rep['gray_mid'] < b, '%.3f < %.3f < %.3f' % (a, rep['gray_mid'], b))
    check('决策字段合法', rep['decision'] in ('compress', 'below', 'hold'), rep['decision'])
    check('报告可 json 化', _jsonable(rep))


def _jsonable(o):
    import json
    try:
        json.dumps(o)
        return True
    except Exception:
        return False


def _lin_from_disp(d):
    return color.s2l(d)


def t_tone_mid_target():
    print('[L1 过亮护栏：折中档（09-13）]')
    # ① 只是"偏亮"的正常片（中位 ~0.73）=> 不该动手。
    #    旧行为（护栏线 = TGT_MID 灰阶 140）会把它一路拽到 140 —— 那正是园岭被压闷的病。
    d = _gray_img(gamma=0.45)
    rep = analyze.analyze(_lin_from_disp(d), d, 'jpg')
    gm0 = float(np.percentile(color.gray_of(d), C.PCT_MID))
    check('只是偏亮的正常片 -> hold（不再拽到中灰）',
          rep['decision'] == 'hold', '中位 %.3f / %s' % (gm0, rep['decision']))

    # ② 真的亮得离谱（中位 > 护栏线 0.854）=> 压回来，落点 = 护栏线本身。
    d2 = _gray_img(gamma=0.15)
    rep2 = analyze.analyze(_lin_from_disp(d2), d2, 'jpg')
    gm2 = float(np.percentile(color.gray_of(d2), C.PCT_MID))
    check('真的亮得离谱 -> compress', rep2['decision'] == 'compress', '中位 %.3f' % gm2)
    lin, info = tone.correct(_lin_from_disp(d2), rep2, C)
    out = np.clip(color.l2s(lin), 0, 1)
    gm = float(np.percentile(color.gray_of(out), C.PCT_MID))
    check('敢动手', info['applied'], 'decision=%s' % rep2['decision'])
    if info['applied']:
        check('中灰落到护栏线（不是拽到 0.55）', abs(gm - C.GUARD_MID) < 0.02,
              '%.4f vs 护栏 %.4f（TGT_MID %.4f）' % (gm, C.GUARD_MID, C.TGT_MID))
        gw = float(np.percentile(color.gray_of(out), C.PCT_WHITE))
        # ★ 09-13「开顶」：过曝路径的白点落点从 TGT_WHITE（灰阶 220）改成**上限** WHITE_CEIL（245）。
        #   中灰同时被收到 GUARD_MID（灰阶 218）⇒ 旧写法等于把最亮一截挤成 218~220 的平板。
        check('★ 开顶：过曝片白点落在上限 WHITE_CEIL（不再被拽到灰阶 220）',
              abs(gw - C.WHITE_CEIL) < 0.02 and gw > C.TGT_WHITE + 0.05,
              '%.4f vs 上限 %.4f（旧靶 %.4f）' % (gw, C.WHITE_CEIL, C.TGT_WHITE))
    check('无 NaN/Inf', np.all(np.isfinite(lin)))
    check('取值在 0~1', lin.min() >= -1e-9 and lin.max() <= 1.0 + 1e-9)

    # ★ 09-14：白点现在**只设上限**；且 **L1 不再提亮**（提亮归锚点）
    d3 = _gray_img(gamma=2.2)                       # 偏暗 -> decision == 'below'
    rep3 = analyze.analyze(_lin_from_disp(d3), d3, 'jpg')
    lin3, info3 = tone.correct(_lin_from_disp(d3), rep3, C)
    check('偏暗（below）⇒ L1 一步不动（提亮归锚点）',
          rep3['decision'] == 'below' and not info3['applied'], rep3['decision'])


def t_tone_monotone():
    print('[L1 单调性]')
    y = np.logspace(-5, 0, 4000)
    d = np.clip(color.l2s(y), 0, 1).reshape(1, -1, 1).repeat(3, -1)
    rep = analyze.analyze(y.reshape(1, -1, 1).repeat(3, -1), d, 'jpg')
    curve, _ = tone.build_curve(rep, C)
    o = curve(y)
    check('曲线单调不减', bool(np.all(np.diff(o) >= -1e-12)))
    # log2 域斜率 = 每一档输入放大几倍。0~4 之外说明曲线要么会翻转、要么在暗部造台阶
    slope = np.diff(np.log2(np.maximum(o, 1e-12))) / np.diff(np.log2(y))
    check('log2 斜率在 [0, 4]', float(slope.min()) >= -1e-6 and float(slope.max()) <= 4.0,
          '%.3f ~ %.3f' % (slope.min(), slope.max()))
    check('值域有界', o.min() > 0 and o.max() < 1.0)
    check('纯黑仍为纯黑（不被提亮）', float(curve(np.array([0.0]))[0]) < 1e-5)


def t_style_lock():
    """锁中灰：职责是「**颜色变换**不许偷偷改曝光」。

    ⚠ 09-13 起 L2 多了一条**刻意**改影调的胶片曲线（`TONE_*`），所以这里要把曲线隔离掉 ——
    否则测到的是"曲线 + 锁"的合成结果，不是锁本身。曲线抬中灰的行为由 `t_style_tone_curve` 守。
    下面保留一条**接口回归**：曲线抬了中灰时，锁不能把它抵消掉。
    """
    print('[L2 锁中灰]')
    cfg = _Cfg(TONE_CURVE=False)
    d = _gray_img(gamma=0.9)
    ref = style.mid_of(d)
    out, info = style.apply(d, cfg, lock_ref=ref)
    check('颜色变换不动中灰', abs(style.mid_of(out) - ref) < 0.012,
          '%.4f -> %.4f' % (ref, style.mid_of(out)))
    out2, _ = style.apply(d, cfg, lock_ref=None)
    check('lock_ref=None 时不锁', True, 'ev=%.4f' % info['lock_ev'])

    # ★ 接口回归：影调曲线抬了中灰 + 带锁 ⇒ 中灰必须真的抬起来（锁不抵消）
    cur = _Cfg(TONE_CURVE=True, TONE_TOE=6.0, TONE_LIFT=6.0)
    out3, info3 = style.apply(d, cur, lock_ref=ref)
    check('曲线抬中灰时锁不抵消（lock_ev≈0 且中灰真的抬高）',
          abs(info3['lock_ev']) < 0.03 and style.mid_of(out3) > ref + 0.005,
          '%.4f -> %.4f (ev=%+.4f)' % (ref, style.mid_of(out3), info3['lock_ev']))


def t_style_contrast_direction():
    """contrast 的**方向**必须对：>1 = 加对比，<1 = 降对比，中灰不动。

    这条是踩过坑加的：曾经把 S 形的符号写反（+a·sin 实际在降对比），
    结果路 B 标定算出来的"这条线反差比大师大"全被落地成了"降对比"。
    """
    print('[L2 对比：方向与支点]')
    d = _gray_img(gamma=1.0)
    s0 = _span90(d)

    up, _ = style.apply(d, _Cfg(CONTRAST=1.35), lock_ref=None)
    s1 = _span90(up)
    check('contrast>1 = 加对比（反差变大）', s1 > s0 + 1.0, '%.1f -> %.1f' % (s0, s1))
    # 支点是 L*=50 那一点（u=0.5 处 sin=0），不是"这张图的中位"—— 用纯色块验最干净
    flat = color.from_lab(np.array([[[50.0, 0.0, 0.0]]]))
    L50 = float(color.to_lab(style.apply(flat, _Cfg(CONTRAST=1.35), lock_ref=None)[0])[0, 0, 0])
    check('支点是 L*=50（纯色块不动）', abs(L50 - 50.0) < 0.5, '50.00 -> %.2f' % L50)

    dn, _ = style.apply(d, _Cfg(CONTRAST=0.70), lock_ref=None)
    s2 = _span90(dn)
    check('contrast<1 = 降对比（反差变小）', s2 < s0 - 1.0, '%.1f -> %.1f' % (s0, s2))

    check('contrast=1.0 时完全不动',
          float(np.max(np.abs(style.apply(d, _Cfg(CONTRAST=1.0), lock_ref=None)[0] - d))) < 1e-9)


def t_style_tone_curve():
    """胶片影调曲线（L2 的 A 档）：趾部压深 + 中高调抬起，**黑白两端不动**。

    最不能错的一条：曲线抬了中灰，LOCK_MID 的参照必须跟着曲线走 ——
    否则锁会把刚抬起来的中灰原样拉回去（LIFT 白做）。下面有专门的回归。
    """
    print('[L2 胶片影调曲线]')
    # ⚠ 必须用**严格中性**的斜坡：_gray_img 逐通道独立加噪，自己就带 a*/b* 抖动
    #   （暗端尤其明显），拿它测"只动 L*"会误报。
    ramp = _gray_img(gamma=1.0)
    d = np.repeat(color.gray_of(ramp)[..., None], 3, axis=-1)

    check('关掉 = 完全不动',
          float(np.max(np.abs(style.apply(d, _Cfg(TONE_CURVE=False), lock_ref=None)[0] - d))) < 1e-9)

    cfg = _Cfg(TONE_CURVE=True, TONE_TOE=6.0, TONE_LIFT=6.0)
    g, y = style._tone_lut(6.0, 6.0)
    check('曲线严格单调', bool(np.all(np.diff(y) > 0)))
    check('黑端不动（L*=0 -> 0）', abs(float(y[0])) < 1e-9, '%.4f' % y[0])
    check('白端不动（L*=100 -> 100）', abs(float(y[-1]) - 100.0) < 1e-9, '%.4f' % y[-1])
    check('趾部：L*=10 被压深到 ~4', abs(float(np.interp(10.0, g, y)) - 4.0) < 0.4,
          '%.2f' % float(np.interp(10.0, g, y)))
    check('中高调：L*=70 被抬到 ~75.7', abs(float(np.interp(70.0, g, y)) - 75.7) < 0.4,
          '%.2f' % float(np.interp(70.0, g, y)))
    _sl_hi = (float(y[-1]) - float(np.interp(90.0, g, y))) / 10.0
    check('高光端有肩部（90->100 段斜率 < 1）', _sl_hi < 0.90, 'slope=%.3f' % _sl_hi)

    out, info = style.apply(d, cfg, lock_ref=None)
    lab0, lab1 = color.to_lab(d), color.to_lab(out)
    check('只动 L*（中性灰的 a*/b* 不动）',
          float(np.max(np.abs(lab1[..., 1]))) < 1e-3 and float(np.max(np.abs(lab1[..., 2]))) < 1e-3,
          'a*max=%.2e b*max=%.2e（Lab 往返噪声量级，远低于 1 个感知单位）'
          % (float(np.max(np.abs(lab1[..., 1]))), float(np.max(np.abs(lab1[..., 2])))))
    check('暗部真的更暗了', float(np.percentile(lab1[..., 0], 10)) <
          float(np.percentile(lab0[..., 0], 10)) - 3.0,
          'P10 %.1f -> %.1f' % (float(np.percentile(lab0[..., 0], 10)),
                                float(np.percentile(lab1[..., 0], 10))))
    check('中灰真的抬起来了', float(np.median(lab1[..., 0])) > float(np.median(lab0[..., 0])) + 2.0,
          'P50 %.1f -> %.1f' % (float(np.median(lab0[..., 0])), float(np.median(lab1[..., 0]))))

    # ★ 关键回归：带锁跑，中灰必须仍然按曲线走（不能被锁拉回）
    ref = style.mid_of(d)
    out_l, info_l = style.apply(d, cfg, lock_ref=ref)
    L_in = float(np.median(color.to_lab(d)[..., 0]))
    L_out = float(np.median(color.to_lab(out_l)[..., 0]))
    L_expect = float(np.interp(L_in, g, y))
    check('带锁跑时中灰仍按曲线走（锁不抵消 LIFT）', abs(L_out - L_expect) < 0.8,
          '期望 %.2f，实得 %.2f' % (L_expect, L_out))
    check('锁的增益被曲线参照抵消（lock_ev ≈ 0）', abs(info_l['lock_ev']) < 0.03,
          'lock_ev=%.4f' % info_l['lock_ev'])

    # 卷可以整组覆盖（给一个 tone_lift=0 的卷就退回"只有趾部"）
    st = stocks.get('neutral')
    st['color'] = dict(tone_curve=True, tone_toe=6.0, tone_lift=0.0)
    out_c, _ = style.apply(d, _Cfg(TONE_CURVE=True, TONE_TOE=0.0, TONE_LIFT=0.0),
                           lock_ref=None, stock=st)
    check('卷能覆盖影调曲线（tone_lift=0 -> 中灰不抬）',
          abs(float(np.median(color.to_lab(out_c)[..., 0])) - L_in) < 1.0,
          'P50 %.1f -> %.1f' % (L_in, float(np.median(color.to_lab(out_c)[..., 0]))))
    check('出厂默认影调曲线开着', bool(getattr(C, 'TONE_CURVE', False)))

    # ---- ★ 肩部（TONE_SHOULDER）：高光段整段下收 ----
    # 出厂默认现在是 2.0（09-13 深夜 SV 拍板「B 档」，由 4 放开 —— 见 config 注释与手册 §21.3）；
    # 下面一律显式传 0 当"老行为"基准，机理检查用 SH=8 的独立档，与默认值解耦。
    print('[★ 肩部 TONE_SHOULDER：收高光（09-13 深夜 SV 拍板「B = 2」）]')
    g0, y0 = style._tone_lut(1.0, 9.0, 0.0)
    _gz, yz = style._tone_lut(1.0, 9.0, 0.0)
    check('肩部 0 = 逐位等于老行为', float(np.max(np.abs(y0 - yz))) < 1e-12)
    check('出厂默认 TONE_SHOULDER = 2.0（B 档：白点上限 L*98，贴大师 L99 97.0）',
          abs(float(getattr(C, 'TONE_SHOULDER', 0.0)) - 2.0) < 1e-9,
          '%.1f' % float(getattr(C, 'TONE_SHOULDER', 0.0)))
    check('默认档的白点上限落在 L*98（不再被钉在 96）',
          abs(float(style._tone_lut(1.0, 9.0, 2.0)[1][-1]) - 98.0) < 1e-6,
          '%.3f' % style._tone_lut(1.0, 9.0, 2.0)[1][-1])

    SH = 8.0
    gs, ys = style._tone_lut(1.0, 9.0, SH)
    check('白端被下收（L*=100 -> 100-sh）', abs(float(ys[-1]) - (100.0 - SH)) < 1e-6,
          '%.3f' % ys[-1])
    sl0 = (y0[-1] - float(np.interp(90.0, g0, y0))) / 10.0
    sls = (ys[-1] - float(np.interp(90.0, gs, ys))) / 10.0
    check('高光段斜率收小（肩部真的更"滚"）', sls < sl0 - 0.05,
          '%.3f -> %.3f' % (sl0, sls))
    check('黑端 / 趾部不受肩部影响',
          abs(float(np.interp(10.0, gs, ys)) - float(np.interp(10.0, g0, y0))) < 0.45,
          '%.2f' % abs(float(np.interp(10.0, gs, ys)) - float(np.interp(10.0, g0, y0))))
    check('中灰基本不动（只收高光）',
          abs(float(np.interp(50.0, gs, ys)) - float(np.interp(50.0, g0, y0))) < 0.6,
          '%.2f' % abs(float(np.interp(50.0, gs, ys)) - float(np.interp(50.0, g0, y0))))
    check('加肩部后仍严格单调（不出平板）', bool(np.all(np.diff(ys) > 0)))

    # 反例守卫：**只压白点**会塌成平板 ⇒ 必须整段收（这是设计决定，别被"简化"掉）
    from scipy.interpolate import PchipInterpolator as _PI
    _xp = np.array([0.0, 10.0, 30.0, 50.0, 70.0, 90.0, 100.0])
    _yp = np.array([0.0, 9.0, 29.55, 54.95, 78.55, 94.95, 92.0])   # 只把最后一格往下挪
    _gg = np.linspace(0.0, 100.0, 1024)
    _yy = np.clip(np.maximum.accumulate(_PI(_xp, _yp)(_gg)), 0.0, 100.0)
    _flat = (float(_yy[-1]) - float(np.interp(90.0, _gg, _yy))) / 10.0
    check('反例：只压白点 = 平板（斜率≈0）⇒ 肩部必须整段收', _flat < 0.15, 'slope=%.3f' % _flat)

    # 卷可覆盖（各卷能有自己的肩部性格）
    st2 = stocks.get('neutral')
    st2['color'] = dict(tone_curve=True, tone_toe=1.0, tone_lift=9.0, tone_shoulder=SH)
    out_s, _ = style.apply(d, _Cfg(TONE_CURVE=True, TONE_TOE=1.0, TONE_LIFT=9.0,
                                   TONE_SHOULDER=0.0), lock_ref=None, stock=st2)
    L_in_s = float(np.median(color.to_lab(d)[..., 0]))
    L_out_s = float(np.median(color.to_lab(out_s)[..., 0]))
    check('卷能覆盖肩部（tone_shoulder 生效）',
          abs(L_out_s - float(np.interp(L_in_s, gs, ys))) < 1.0,
          '期望 %.2f，实得 %.2f' % (float(np.interp(L_in_s, gs, ys)), L_out_s))

    out_off = style.apply(d, _Cfg(TONE_CURVE=True, TONE_TOE=1.0, TONE_LIFT=9.0,
                                  TONE_SHOULDER=0.0), lock_ref=None)[0]
    out_sh = style.apply(d, _Cfg(TONE_CURVE=True, TONE_TOE=1.0, TONE_LIFT=9.0,
                                 TONE_SHOULDER=SH), lock_ref=None)[0]
    check('成片层面：高光更暗（P95 降）',
          float(np.percentile(color.to_lab(out_sh)[..., 0], 95)) <
          float(np.percentile(color.to_lab(out_off)[..., 0], 95)) - 1.0,
          'P95 %.1f -> %.1f' % (float(np.percentile(color.to_lab(out_off)[..., 0], 95)),
                                float(np.percentile(color.to_lab(out_sh)[..., 0], 95))))


def _hp_std(x):
    """高通道方差 = 噪点量（灰度 0~255 口径），跟 metrics 的 noise 一个意思。"""
    g = color.gray_of(np.clip(x, 0, 1)) * 255.0
    k = np.ones((9, 9), np.float32) / 81.0
    import cv2
    b = cv2.blur(g.astype(np.float32), (9, 9))
    return float((g - b).std())


def t_denoise():
    """降噪：默认关=恒等；开着时暗块噪点真下降、亮块不动、硬边保留；近似零均值。

    ⚠ 关于"黑位漂移"：在**带噪点**的暗部，P0.2 是噪声驱动的极值，降噪把噪声抹掉后
    极值**必然**抬高 —— 那是降噪的定义，不是"平白提黑"。所以这条不变量的正确问法是：
      ① 均值 / 中位（内容所在）不漂；
      ② 在**没有噪点**的暗块上，黑位不漂。
    """
    print('[降噪]')
    rng = np.random.default_rng(7)
    # 三块**平坦**色（暗 / 中 / 亮）各带噪点 —— 传感器噪点就长这样，不是整片斜坡
    d = np.empty((240, 360, 3))
    d[:80], d[80:160], d[160:] = 0.20, 0.45, 0.82
    d = np.clip(d + rng.normal(0, 0.025, d.shape), 0.0, 1.0)

    off, i0 = denoise.apply(d, _Cfg(DENOISE_ENABLE=False))
    check('关掉 = 恒等（⚠ 出厂 09-14 起是**开**，所以这里显式关）',
          float(np.max(np.abs(off - d))) < 1e-12 and not i0['applied'])

    cfg = _Cfg(DENOISE_ENABLE=True)
    on, i1 = denoise.apply(d, cfg)
    check('开启后真的动手', bool(i1['applied']) and i1['mask_mean'] > 0.0,
          'mask 均值 %.3f' % i1['mask_mean'])
    check('输出有界/无 NaN', bool(np.all(np.isfinite(on))) and on.min() >= 0 and on.max() <= 1)

    g0, g1 = color.gray_of(d), color.gray_of(on)
    check('全图均值漂移 < 0.002（零均值）', abs(float(g1.mean() - g0.mean())) < 0.002,
          '%.5f -> %.5f' % (g0.mean(), g1.mean()))
    for q in (C.PCT_MID, C.PCT_WHITE):
        a, b = float(np.percentile(g0, q)), float(np.percentile(g1, q))
        check('分位 %g 漂移 < 0.005' % q, abs(b - a) < 0.005, '%.4f -> %.4f' % (a, b))

    n0, n1 = _hp_std(d[:80]), _hp_std(on[:80])
    check('暗块噪点真的下降', n1 < n0 * 0.70, '%.2f -> %.2f' % (n0, n1))
    h0, h1 = _hp_std(d[160:]), _hp_std(on[160:])
    check('亮块基本不动（高光不给降）', h1 > h0 * 0.90, '%.2f -> %.2f' % (h0, h1))

    # 硬边保留：黑白各半的台阶不能被打平
    e = np.full((120, 120, 3), 0.22)
    e[60:] = 0.78
    e = np.clip(e + rng.normal(0, 0.02, e.shape), 0.0, 1.0)
    eo, _ = denoise.apply(e, cfg)
    f0, f1 = color.gray_of(e), color.gray_of(eo)
    step0 = float(f1[64:76].mean() - f1[44:56].mean())
    step1 = float(f0[64:76].mean() - f0[44:56].mean())
    check('硬边台阶保留 ≥85%', step0 >= 0.85 * step1, '%.4f -> %.4f' % (step1, step0))

    # 无噪点的暗部：黑位**不**该被抬（这一步才是"别平白提黑"的真正检验）
    z = np.empty((240, 360, 3))
    z[:120], z[120:] = 0.055, 0.72
    zo, _ = denoise.apply(z, cfg)
    b0 = float(np.percentile(color.gray_of(z), C.PCT_BLACK))
    b1 = float(np.percentile(color.gray_of(zo), C.PCT_BLACK))
    check('无噪点暗部的黑位不漂 < 0.005', abs(b1 - b0) < 0.005, '%.4f -> %.4f' % (b0, b1))


def t_guard():
    print('[L4 护栏]')
    d = np.clip(_gray_img(gamma=0.15) * 1.9, 0, 1)      # 大量死白
    before = float(np.mean(color.gray_of(d) >= 254 / 255))
    out, info = guard.enforce(d, C)
    after = float(np.mean(color.gray_of(out) >= 254 / 255))
    check('死白被压到上限内', after <= C.CAP_WHITE_FRAC + 1e-6,
          '%.4f -> %.4f (上限 %.3f)' % (before, after, C.CAP_WHITE_FRAC))
    check('护栏只往下压', float(np.percentile(color.gray_of(out), C.PCT_WHITE)) <=
          float(np.percentile(color.gray_of(d), C.PCT_WHITE)) + 1e-6)


def t_lut():
    print('[L2 .cube]')
    tmp = os.path.join(tempfile.gettempdir(), '_svfilm_selftest.pars')
    cube = style.bake_grid(9).reshape(9, 9, 9, 3)
    style.cube_write(tmp, cube, 'identity')
    back = style.cube_read(tmp)
    check('.cube 往返一致', float(np.max(np.abs(back - cube))) < 1e-5)
    d = np.random.default_rng(3).random((30, 40, 3))
    check('恒等 LUT 三线性 = 原图', float(np.max(np.abs(style.cube_apply(d, back) - d))) < 1e-3)
    try:
        style.cube_write(tmp, style.bake_grid(9).reshape(9, 9, 9, 3), 'x')
        ok = os.path.getsize(tmp) > 1000
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    check('烘焙写出文件', ok)


def t_io_roundtrip():
    print('[io]')
    tmp = os.path.join(tempfile.gettempdir(), '_svfilm_selftest.png')
    d = _gray_img(60, 90, gamma=0.6)
    io.save(d, tmp, quality=95)
    s = io.load(tmp, max_side=4096, src='jpg')
    e = float(np.max(np.abs(s.disp - d)))
    check('存-读往返误差 < 2/255', e < 2.0 / 255.0, 'max err %.4f' % e)
    os.remove(tmp)


def t_style_chroma_ends():
    """抽色饱和：两端掉彩、中间调不动（09-13 调研修正，出处 Emulsifier）。"""
    print('[L2 抽色饱和（两端掉彩）]')
    Ls = np.array([8.0, 25.0, 50.0, 75.0, 95.0])
    lab = np.stack([Ls, np.full_like(Ls, 12.0), np.full_like(Ls, 12.0)], axis=-1)
    # 先夹进显示域（L*=95 那块的 R 会超过 1），否则"关掉 = 恒等"测的是裁剪不是模型
    d = np.clip(color.from_lab(lab), 0.0, 1.0).reshape(1, -1, 3)
    out, _ = style.apply(d, _Cfg(CHROMA_ENDS=0.30), lock_ref=None)
    c0 = color.chroma(color.to_lab(d))[0]
    c1 = color.chroma(color.to_lab(out))[0]
    check('抽色饱和：中间调（L*=50）不动', abs(c1[2] / c0[2] - 1.0) < 0.03,
          '%.3f -> %.3f' % (c0[2], c1[2]))
    check('抽色饱和：暗端掉彩', c1[0] / c0[0] < 0.85, '%.3f -> %.3f' % (c0[0], c1[0]))
    check('抽色饱和：亮端掉彩', c1[4] / c0[4] < 0.85, '%.3f -> %.3f' % (c0[4], c1[4]))
    out0, _ = style.apply(d, _Cfg(CHROMA_ENDS=0.0), lock_ref=None)
    check('抽色饱和关掉 = 恒等', float(np.max(np.abs(out0 - d))) < 1e-9)
    # 出厂默认必须开着（否则这个修正等于没做）
    check('出厂默认抽色饱和开着', float(getattr(C, 'CHROMA_ENDS', 0.0)) > 0.0, str(getattr(C, 'CHROMA_ENDS', None)))


def t_stocks():
    print('[卷 stocks]')
    for n in stocks.names():
        s = stocks.TABLE[n]
        check('卷 %s 有名字/说明/颜色参数' % n,
              bool(s['label'] and s['desc'] and s['color']))
    d = _gray_img(gamma=0.8)
    # 这一条测的是"卷的恒等性"，所以必须把基准摘掉（否则测到的是出厂默认的全对齐）
    out, _ = style.apply(d, C, lock_ref=None, stock=stocks.get('neutral'),
                         base='BASE_NONE')
    check('neutral 卷 = 恒等（不风格化）', float(np.max(np.abs(out - d))) < 1e-9,
          'max %.2e' % float(np.max(np.abs(out - d))))
    # 出厂默认是哪一档要显式守在这里（09-13 SV 拍板 = 全对齐）；改了必须在这里承认
    check('出厂默认基准是合法档位', C.BASE in stocks.base_names(), C.BASE)
    check('_Cfg 隔离基准（单测不跟出厂默认走）', _Cfg().BASE == 'BASE_NONE')
    # 每个卷的参数都要能跑通并给出有界结果
    grid = style.bake_grid(5).reshape(1, -1, 3)
    for n in stocks.names():
        o, _ = style.apply(grid, C, lock_ref=None, stock=stocks.get(n))
        check('卷 %s 可跑通且有界' % n,
              bool(np.all(np.isfinite(o))) and o.min() >= 0.0 and o.max() <= 1.0)
    # ★★ 09-14 起卷分两种：**真卷**（差异在 `spek=`，交给 spektrafilm）和**恒等**（neutral）。
    #   ⇒ "不同卷确实不同"要看**标识**，不能只看我们的 color 参数（真卷的 color 是恒等）。
    _specs = {n: (stocks.get(n) or {}).get('spek') for n in stocks.names()}
    _ids = {repr(sorted(v.items())) for v in _specs.values() if v}
    check('卷确实是不同的（真卷看 spek 标识 / 恒等卷看 color）', len(_ids) >= 5,
          '真卷 %d 个 / 不同标识 %d 种' % (sum(1 for v in _specs.values() if v), len(_ids)))


def t_spatial_off():
    print('[空间域：全关 = 恒等]')
    # ⚠ 09-14 起出厂**默认开**（SV：「需要那些空间层的」）⇒ 这条要**显式关掉**三个开关再测。
    ks = ('GRAIN_ENABLE', 'BLOOM_ENABLE', 'HALATION_ENABLE')
    old = {k: getattr(C, k) for k in ks}
    try:
        for k in ks:
            setattr(C, k, False)
        d = _gray_img(gamma=0.55)
        out, info = spatial.apply(d, C, stock=None)
        check('三个都关时不动像素', float(np.max(np.abs(out - d))) < 1e-12)
        check('info.any=False', not info['any'])
    finally:
        for k, v in old.items():
            setattr(C, k, v)


def t_spatial_grain():
    print('[空间域：颗粒]')
    d = _gray_img(gamma=0.55)
    p = spatial.resolve(C, stocks.get('portra400'))['grain']
    g1, i1 = spatial.grain(d, p)
    g2, _ = spatial.grain(d, p)
    check('颗粒可复现（固定种子）', float(np.max(np.abs(g1 - g2))) < 1e-12)
    dm = float(np.percentile(color.gray_of(d), C.PCT_MID))
    gm = float(np.percentile(color.gray_of(g1), C.PCT_MID))
    check('颗粒不推走中灰（±0.006）', abs(gm - dm) < 0.006, '%.4f -> %.4f' % (dm, gm))
    blk = np.zeros((48, 48, 3))
    gb, _ = spatial.grain(blk, p)
    check('颗粒不把纯黑提亮（乘性叠加）', float(gb.max()) < 1e-12)
    # ★ 高光端精确归零（09-13 调研修 bug）：白墙/天空不能有颗粒
    wht = np.full((48, 48, 3), 0.99)
    gw, _ = spatial.grain(wht, p)
    check('颗粒在高光端归零（白墙/天空不动）', float(np.max(np.abs(gw - wht))) < 1e-12,
          'max %.2e' % float(np.max(np.abs(gw - wht))))
    check('颗粒输出有界', bool(np.all(np.isfinite(g1))) and g1.min() >= 0 and g1.max() <= 1)


def t_spatial_bloom_halation():
    print('[空间域：黑柔 / Halation 方向性]')
    d = np.zeros((200, 200, 3))
    d[80:120, 80:120] = 1.0                      # 黑底上一个白方块
    p = spatial.resolve(C, stocks.get('cinestill800t'))

    b, _ = spatial.bloom(d, p['bloom'])
    near = float(b[58:76, 80:120].mean())        # 方块正上方外侧
    far = float(b[0:10, 0:10].mean())            # 远角
    check('黑柔只在亮区往外扩散（近处亮、远处不动）', near > 1e-4 and far < 1e-4,
          'near %.5f far %.7f' % (near, far))

    h, _ = spatial.halation(d, p['halation'])
    ring = h[58:76, 80:120]
    r, gg, bb = (float(ring[..., i].mean()) for i in range(3))
    check('Halation 晕圈偏红橙（R>G>B）', r > gg > bb, '%.4f/%.4f/%.4f' % (r, gg, bb))
    check('Halation 输出有界', bool(np.all(np.isfinite(h))) and h.min() >= 0 and h.max() <= 1)

    # ★ 分通道半径（09-13 调研修正）：红光散得最远、绿居中、蓝近零
    _, hi = spatial.halation(d, p['halation'])
    ratios = hi.get('radius_ratios')
    check('Halation 分通道半径（R>G>B）',
          bool(ratios) and float(ratios[0]) > float(ratios[1]) > float(ratios[2]), str(ratios))
    # 远处外圈：红还有能量，绿已经基本没有（蓝半径最小）
    far = h[26:52, 80:120]
    fr, fg, fb = (float(far[..., i].mean()) for i in range(3))
    check('Halation 外圈以红为主（R 明显 > G）', fr > fg * 1.5,
          '外圈 R/G/B %.5f/%.5f/%.5f' % (fr, fg, fb))

    # ★ 09-13「黑柔阶梯」三条：三个机理独立 + "化开"方向与"加性辉光"相反
    #   ① 门控修错：veil>0 而 amount=0 时**必须真的动**
    #      （老代码 `amount<=0` 把整个函数关掉 ⇒ "只开面纱"逐位等于"关"，白扫一档）
    pv = dict(p['bloom']); pv.update(dict(amount=0.0, veil=0.10, spread=0.0))
    bv, _ = spatial.bloom(d, pv)
    dv = float(np.abs(bv - d).max())
    check('只开面纱（veil>0, amount=0）确实生效', dv > 1e-5, 'max %.2e' % dv)

    #   ② 方向不变量：加性辉光**抬高**核心，化开**压低**核心（参数名语义必须与行为一致）
    pa = dict(p['bloom']); pa.update(dict(amount=0.15, veil=0.0, spread=0.0))
    ba, _ = spatial.bloom(d, pa)
    ps = dict(p['bloom']); ps.update(dict(amount=0.15, veil=0.0, spread=0.60))
    bs, _ = spatial.bloom(d, ps)
    core_a = float(ba[95:105, 95:105].mean())
    core_s = float(bs[95:105, 95:105].mean())
    check('加性辉光抬核心 / 化开压核心（方向相反）', core_a > core_s + 0.05,
          '加性 %.4f vs 化开 %.4f' % (core_a, core_s))

    #   ③ 化开把能量**散到外圈**：外圈不能比纯加性的更暗（守恒 ⇒ 核心少了的挪到外面）
    ring_a = float(ba[58:76, 80:120].mean())
    ring_s = float(bs[58:76, 80:120].mean())
    check('化开把能量散到外圈（外圈 ≥ 加性辉光）', ring_s >= ring_a - 1e-9,
          '加性 %.5f vs 化开 %.5f' % (ring_a, ring_s))

    #   ④ 守恒条件：**amount == spread 时**平坦亮区几乎不动（+amt*glow 与 -spread*hot 相消）。
    #      注意：单独给 spread（amount=0）就是"只把高光压暗"，不守恒 —— 这一条守的是"配对使用"。
    flat = np.zeros((200, 200, 3)); flat[:, :] = 0.95
    pf = dict(p['bloom']); pf.update(dict(amount=0.60, veil=0.0, spread=0.60, warmth=0.0))
    bf, _ = spatial.bloom(flat, pf)
    df = float(np.abs(bf - flat).max())
    check('amount==spread 时平坦亮区不漂（能量守恒）', df < 0.01, 'max %.4f' % df)

    # ★★ 09-13 修「脸变色」的两条护栏（SV 报 DSCF2638）：
    #    老实现 `lin + amount*blur(lin*mask)` 把**模糊后的彩色光**直接加上去 ⇒
    #    辉光会把**周围亮物的颜色搬到别处**（红抱枕的红光糊到脸上）。
    #   ⑤ 数值不变量：`warmth=0` 时三通道的**增量必须完全相等**（只动亮度，不改色相）
    mid = np.zeros((240, 240, 3)); mid[:, :] = 0.30          # 中灰底（不会顶到白）
    mid[80:160, 80:160] = 1.0                                # 中间一块白
    pm = dict(p['bloom']); pm.update(dict(amount=0.30, veil=0.0, spread=0.0, warmth=0.0))
    bm, _ = spatial.bloom(mid, pm)
    d_lin = color.s2l(bm) - color.s2l(mid)
    selm = bm.max(axis=-1) < 0.99                            # 排除被裁到 1.0 的像素
    dev = float(np.max(np.abs(d_lin[..., 0][selm] - d_lin[..., 2][selm])))
    check('辉光只动亮度：warmth=0 时三通道增量相等（不改色相）', dev < 1e-6,
          'max|ΔR−ΔB| %.2e' % dev)

    #   ⑥ 语义不变量：**红块旁边不能把中性灰染红**（"不许搬邻居的颜色"）
    blk = np.zeros((240, 240, 3)); blk[:, :] = 0.25
    blk[:, :120] = np.array([1.0, 0.02, 0.02])               # 左半：鲜红亮块
    pb = dict(p['bloom']); pb.update(dict(amount=0.50, veil=0.0, spread=0.0, warmth=0.0))
    bb, _ = spatial.bloom(blk, pb)
    lab_b = color.to_lab(bb); lab_0 = color.to_lab(blk)
    right = np.zeros((240, 240), bool); right[:, 130:] = True   # 右半：离红块远一点的中性区
    da = float(np.median(lab_b[..., 1][right] - lab_0[..., 1][right]))
    db = float(np.median(lab_b[..., 2][right] - lab_0[..., 2][right]))
    check('辉光不把邻居的颜色搬过来（红块旁的中性区不发红）', abs(da) < 1.0 and abs(db) < 1.0,
          'Δa* %+.2f  Δb* %+.2f' % (da, db))


def _skin_patch(a, b, L=62.0, size=64):
    lab = np.zeros((size, size, 3), np.float64)
    lab[..., 0] = L
    lab[..., 1] = a
    lab[..., 2] = b
    return np.clip(color.from_lab(lab), 0.0, 1.0)


def t_local_skin_floor():
    print('[L3 肤色正向达标 + 逐像素门]')
    p = _skin_patch(10.0, 8.0)
    o, i = local.skin_floor(p, _Cfg(SKIN_FLOOR=False))
    check('达标关掉 = 逐像素恒等', float(np.max(np.abs(o - p))) < 1e-12)

    cfg = _Cfg(SKIN_FLOOR=True, SKIN_PROTECT_STRENGTH=0.0)
    cold = _skin_patch(10.0, 8.0, 62.0)
    o, i = local.skin_floor(cold, cfg)
    lo = color.to_lab(o)
    check('冷皮肤被抬到档上', i['applied'] is True, str(i.get('reason')))
    check('a* 落在目标上', abs(float(np.median(lo[..., 1])) - cfg.SKIN_FLOOR_A) < 0.3,
          'a*=%.2f' % float(np.median(lo[..., 1])))
    check('b* 落在目标上', abs(float(np.median(lo[..., 2])) - cfg.SKIN_FLOOR_B) < 0.3,
          'b*=%.2f' % float(np.median(lo[..., 2])))
    check('明度不动', abs(float(np.median(lo[..., 0])) - 62.0) < 0.6,
          'L*=%.2f' % float(np.median(lo[..., 0])))

    warm = _skin_patch(16.5, 19.0, 62.0)
    o2, i2 = local.skin_floor(warm, cfg)
    check('已达标的皮肤一分不动', float(np.max(np.abs(o2 - warm))) < 1e-12, str(i2.get('reason')))

    # 唇妆（a* 已经很高）：两个轴里 a* 超目标 ⇒ 完全不碰
    lips = _skin_patch(10.0, 8.0, 62.0)
    lips[:, :20] = _skin_patch(22.0, 12.0, 62.0)[:, :20]
    o3, _ = local.skin_floor(lips, cfg)
    lab3 = color.to_lab(o3)
    check('唇妆不被推红', abs(float(np.median(lab3[:, :20, 1])) - 22.0) < 0.1,
          'lip a*=%.2f' % float(np.median(lab3[:, :20, 1])))
    check('同一张图里皮肤照补', float(np.median(lab3[:, 20:, 1])) > cfg.SKIN_FLOOR_A * 0.97,
          'skin a*=%.2f' % float(np.median(lab3[:, 20:, 1])))

    # 粉衣服（a* 高于目标、b* 低于目标）：不该被"顺手推暖"
    cloth = _skin_patch(10.0, 8.0, 62.0)
    cloth[:32] = _skin_patch(21.0, 10.0, 62.0)[:32]
    o4, _ = local.skin_floor(cloth, cfg)
    lab4 = color.to_lab(o4)
    check('粉衣服不被顺手推暖', float(np.max(np.abs(o4[:32] - cloth[:32]))) < 0.02,
          'cloth b*=%.2f' % float(np.median(lab4[:32, :, 2])))

    bright = _skin_patch(10.0, 8.0, 88.0)
    o5, i5 = local.skin_floor(bright, cfg)
    check('过亮脸门收力（gate≈0）', i5['gate'] < 0.02, 'gate=%.3f' % i5['gate'])
    check('过亮脸基本不动', float(np.max(np.abs(o5 - bright))) < 0.02)

    # 门是**逐像素**的：同一张脸里，亮的像素少补、暗的像素照补
    ramp = _skin_patch(10.0, 8.0, 62.0, 64)
    ramp[:32] = _skin_patch(10.0, 8.0, 80.0)[:32]
    o6, i6 = local.skin_floor(ramp, cfg)
    lab6 = color.to_lab(o6)
    dk = float(np.median(lab6[32:, :, 1]))
    br = float(np.median(lab6[:32, :, 1]))
    check('逐像素门：暗的半边照补', dk > cfg.SKIN_FLOOR_A * 0.97, 'dark a*=%.2f' % dk)
    check('逐像素门：亮的半边少补', br < cfg.SKIN_FLOOR_A * 0.90, 'bright a*=%.2f' % br)
    check('逐像素门：门在 (0,1) 之间', 0.2 < i6['gate'] < 0.95, 'gate=%.3f' % i6['gate'])

    d3, li = local.apply(cold, cold, cfg)
    check('apply 汇报 skin_floor', 'skin_floor' in li)
    d3b, lib = local.apply(cold, cold, _Cfg())
    check('apply 关掉时不带 skin_floor', 'skin_floor' not in lib)


def t_pipeline_smoke():
    print('[全链 smoke]')
    d = _gray_img(120, 180, gamma=0.4)
    rep = analyze.analyze(_lin_from_disp(d), d, 'jpg')
    lin, ti = tone.correct(_lin_from_disp(d), rep, C)
    d1 = np.clip(color.l2s(lin), 0, 1)
    d1, di = denoise.apply(d1, C)
    d2, si = style.apply(d1, C, lock_ref=style.mid_of(d1), stock=stocks.get('portra400'))
    d2b, pi = spatial.apply(d2, C, stock=stocks.get('portra400'))
    d3, li = local.apply(d1, d2b, C)
    d4, gi = guard.enforce(d3, C)
    check('各层输出有界', all(x.min() >= 0 and x.max() <= 1 for x in (d1, d2, d2b, d3, d4)))
    check('各层无 NaN', all(np.all(np.isfinite(x)) for x in (d1, d2, d2b, d3, d4)))
    check('肤色掩膜不炸', 0.0 <= li['skin_cov'] <= 1.0, 'cov=%.4f' % li['skin_cov'])
    check('空间域 info 齐全', all(k in pi for k in ('grain', 'bloom', 'halation', 'any')))


def _mn(entries):
    """造一个最小的富士 MakerNote（只支持 SHORT / SRATIONAL）。

    entries: [(tag, type, value)]；type 3 的 value 是 int，type 10 的 value 是 (分子, 分母)。
    """
    n = len(entries)
    data_off = 12 + 2 + n * 12 + 4
    body = struct.pack('<H', n)
    blob = b''
    for tag, typ, val in entries:
        if typ == 3:
            body += struct.pack('<HHI', tag, typ, 1) + struct.pack('<H', val) + b'\0\0'
        elif typ == 10:
            body += struct.pack('<HHII', tag, typ, 1, data_off + len(blob))
            blob += struct.pack('<ii', val[0], val[1])
        else:
            raise ValueError('测试用的 MakerNote 只支持 SHORT/SRATIONAL')
    return b'FUJIFILM' + struct.pack('<I', 12) + body + struct.pack('<I', 0) + blob



def t_entry_bias():
    print('[入口基线曝光：MakerNote tag 语义]')
    S, SR = 3, 10
    # Manual/Raw 模式（实测 759 张全是这个）-> 看 0x1403
    check('Manual 模式读 0x1403',
          rawmeta.fuji_development_dr(_mn([(0x1402, S, 1), (0x1403, S, 400)])) == 400)
    check('Manual 模式读 DR200',
          rawmeta.fuji_development_dr(_mn([(0x1402, S, 1), (0x1403, S, 200)])) == 200)
    # Auto 模式 -> 看 0x140b
    check('Auto 模式读 0x140b',
          rawmeta.fuji_development_dr(_mn([(0x1402, S, 0), (0x140b, S, 200)])) == 200)
    # 模式读不到 -> 两个都试
    check('无模式 tag 时回退 0x1403',
          rawmeta.fuji_development_dr(_mn([(0x1403, S, 100)])) == 100)
    check('无模式 tag 时回退 0x140b',
          rawmeta.fuji_development_dr(_mn([(0x140b, S, 400)])) == 400)
    # ★ 回归护栏：0x1402 是模式开关（值 0/1），**绝不能**被当成档位返回
    check('0x1402 的值不当档位（Manual 缺 0x1403 时返回 None）',
          rawmeta.fuji_development_dr(_mn([(0x1402, S, 1)])) is None)
    check('0x1402 的值不当档位（Auto 缺 0x140b 时返回 None）',
          rawmeta.fuji_development_dr(_mn([(0x1402, S, 0)])) is None)
    check('非富士 MakerNote 不炸', rawmeta.fuji_development_dr(b'NIKON\x00\x00\x00') is None)
    check('空 MakerNote 不炸', rawmeta.fuji_development_dr(None) is None)
    # 精确 EV（0x9650）：绝大多数机身不写，写了就用
    check('0x9650 读出精确 EV', abs(rawmeta.fuji_raw_ev(_mn([(0x9650, SR, (272, 100))])) - 2.72) < 1e-6)
    check('0x9650 离谱值被丢弃', rawmeta.fuji_raw_ev(_mn([(0x9650, SR, (9999, 1))])) is None)
    check('没有 0x9650 时返回 None', rawmeta.fuji_raw_ev(_mn([(0x1403, S, 400)])) is None)

    print('[入口基线曝光：机型表 + DR 分层]')
    fuji = cameras.lookup('FUJIFILM', 'X-T30 III')
    check('富士机型命中', fuji['hit'] and fuji['key'] == 'x-t30 iii')
    check('富士基底 = 0.72EV', abs(fuji['baseline_ev'] - 0.72) < 1e-9)
    check('DR 表只放"额外"量 100/200/400 = 0/1/2',
          cameras.dr_bias_ev(100) == 0.0 and cameras.dr_bias_ev(200) == 1.0
          and cameras.dr_bias_ev(400) == 2.0)
    check('不认识的 DR 档返回 None（表示"不知道"）',
          cameras.dr_bias_ev(None) is None and cameras.dr_bias_ev(999) is None)
    # 与 darktable 官方总表对齐：DR100 −0.72 / DR200 −1.72 / DR400 −2.72
    tot = {dr: fuji['baseline_ev'] + cameras.dr_bias_ev(dr) for dr in (100, 200, 400)}
    check('合计对上 darktable 总表 0.72/1.72/2.72',
          abs(tot[100] - 0.72) < 1e-9 and abs(tot[200] - 1.72) < 1e-9 and abs(tot[400] - 2.72) < 1e-9,
          '%.2f/%.2f/%.2f' % (tot[100], tot[200], tot[400]))
    check('未知机型不猜（基底 0）', cameras.lookup('Canon', 'EOS R5')['baseline_ev'] == 0.0)

    print('[入口曲线：实测相机曲线查表（Q1）]')
    _saved_curve = dict(cameras.ENTRY_CURVE)
    try:
        # 表空 / 机型不认识 -> None（退回老的常数补偿，不能炸）
        cameras.ENTRY_CURVE.clear()
        check('表空时返回 None（退回常数补偿）', cameras.entry_curve('x-t30 iii', 400) is None)
        cameras.ENTRY_CURVE.update({
            'x-t30 iii': {
                # anchors = [[输入线性亮度, 增益倍数], ...]，弓形：暗部抬得多、高光收
                '400': dict(mid_ev=3.10, anchors=[[0.001, 5.0], [0.02, 7.0], [0.18, 8.57],
                                                  [0.30, 8.0], [1.0, 1.0]]),
                'None': dict(mid_ev=0.72, anchors=[[0.001, 1.4], [0.18, 1.647], [1.0, 1.0]]),
            }})
        check('机型不认识返回 None', cameras.entry_curve('nope', 400) is None)
        c = cameras.entry_curve('X-T30 III', 400)          # 大小写不敏感
        check('命中 -> 给出 mid_ev + 增益锚点', c is not None and abs(c[0] - 3.10) < 1e-9)
        check('锚点按输入线性升序', c[1] == sorted(c[1]))
        check('零点自洽：Y=0.18 处的增益 = 2^mid_ev（±0.05 档）',
              abs(np.log2(np.interp(0.18, c[1], c[2])) - c[0]) < 0.05,
              '%.3f vs %.3f' % (np.log2(np.interp(0.18, c[1], c[2])), c[0]))
        check('增益是"弓形"（中间调最高，暗部/高光都比它低）',
              c[2][2] > c[2][0] and c[2][2] > c[2][-1])
        c2 = cameras.entry_curve('x-t30 iii', None)        # dr 未知 -> 取 'None' 那条
        check('dr=None 回退到 None 那条', c2 is not None and abs(c2[0] - 0.72) < 1e-9)
        c3 = cameras.entry_curve('x-t30 iii', 800)         # dr 有但不认识 -> 回退
        check('dr 不认识时回退', c3 is not None and abs(c3[0] - 0.72) < 1e-9)
    finally:
        cameras.ENTRY_CURVE.clear()
        cameras.ENTRY_CURVE.update(_saved_curve)

    # 曲线怎么套：三通道同一个倍率（只动亮度）＋ 弓形（暗部倍率 > 高光倍率）
    _cv = (3.10, [0.001, 0.18, 0.60, 1.0], [5.0, 8.57, 2.0, 1.0])
    _g = np.array([[[0.18, 0.18, 0.18]]])
    check('曲线把 18% 中性灰抬 2^mid_ev 倍',
          abs(io.apply_entry_curve(_g, _cv)[0, 0, 0] / 0.18 - 8.57) < 0.02)
    _c = np.array([[[0.30, 0.15, 0.075]]])
    _r = io.apply_entry_curve(_c, _cv)[0, 0] / _c[0, 0]
    check('曲线不改色相（三通道同一个倍率）', float(_r.max() - _r.min()) < 1e-12)
    check('弓形：中间调倍率最高（暗部、高光都比它低）',
          io.apply_entry_curve(_g, _cv)[0, 0, 0] / 0.18
          > io.apply_entry_curve(np.array([[[0.001] * 3]]), _cv)[0, 0, 0] / 0.001
          and io.apply_entry_curve(_g, _cv)[0, 0, 0] / 0.18
          > io.apply_entry_curve(np.array([[[0.60] * 3]]), _cv)[0, 0, 0] / 0.60)

    print('[位置：入口 settle 定基准 + 脸锚点补偿]')
    _tsrc = open(tone.__file__, encoding='utf-8').read()
    check('L1 影调曲线**只压不提**（旧的兜底提亮整套已删）',
          'allow_lift' not in _tsrc and 'EV_CAP_UP' not in _tsrc)
    check('位置：唯一会给画面提亮的是锚点（pipeline 调 io.refocus）',
          'io.refocus(' in open(pipeline.__file__, encoding='utf-8').read())


def t_review_fixes():
    """09-13 晚「评审问题全部一起改」的守卫 —— 每修一条就钉一条不变量，防止以后被"简化"掉。"""
    print('[★ 评审修复守卫：P0-2 / P1-3 / P1-4 / P1-6 / P2-8 / P2-9 / P2-10]')

    # ---------- P0-2：中灰判据统一到 L*（一把尺子） ----------
    _below = float(C.TGT_MID_L) - float(C.MID_DEADZONE_L)
    dd = _gray_img(gamma=2.0)
    rr = analyze.analyze(_lin_from_disp(dd), dd, 'raw')
    check('P0-2 decision 由 L* 中位决定（与报告里的 l50 同源，不再用显示域灰度）',
          (rr['decision'] == 'below') == bool(rr['l50'] < _below)
          and (rr['decision'] == 'compress') == bool(rr['l50'] > C.GUARD_MID_L),
          'L50 %.1f / %s' % (rr['l50'], rr['decision']))

    # ---------- P1-3：提到 config 的旋钮**真的被读**（不是提了个空壳） ----------
    _g0, _y0 = style._tone_lut(1.0, 9.0)
    _save_w = C.TONE_LIFT_W
    try:
        C.TONE_LIFT_W = (0.0, 0.0, 0.0)
        style._TONE_CACHE.clear()
        _g1, _y1 = style._tone_lut(1.0, 9.0)
    finally:
        C.TONE_LIFT_W = _save_w
        style._TONE_CACHE.clear()
    _dw = float(np.max(np.abs(_y1 - _y0)))
    check('P1-3 影调曲线权重从 config 读（改它曲线跟着变，且缓存键含权重）', _dw > 0.5,
          '最大差 %.2f' % _dw)

    # ---------- P2-8：bloom 能量守恒的前提必须成对 ----------
    check('P2-8 bloom 守恒前提：BLOOM_AMOUNT == BLOOM_SPREAD',
          abs(float(C.BLOOM_AMOUNT) - float(C.BLOOM_SPREAD)) < 1e-9,
          '%.4f vs %.4f' % (C.BLOOM_AMOUNT, C.BLOOM_SPREAD))
    _pairs = []
    for _nm, _st in stocks.TABLE.items():
        _sp = ((_st.get('spatial') or {}).get('bloom') or {})
        _a, _s = _sp.get('amount'), _sp.get('spread')
        if _a is not None and _s is not None and abs(float(_a) - float(_s)) > 1e-9:
            _pairs.append(_nm)
    check('P2-8 没有卷单独改其中一个（改了画面会静默不守恒）', not _pairs, str(_pairs))

    # ---------- P1-4：空间序 = bloom → halation → grain ----------
    cf = _Cfg(BLOOM_ENABLE=True, HALATION_ENABLE=True, GRAIN_ENABLE=True,
              BLOOM_AMOUNT=0.15, BLOOM_SPREAD=0.15, BLOOM_VEIL=0.02,
              GRAIN_AMOUNT=0.03, HALATION_AMOUNT=0.11)
    dd = _gray_img(gamma=0.9)
    _out, _ = spatial.apply(dd, cf)
    _p = spatial.resolve(cf)
    _cur = np.clip(dd, 0.0, 1.0)
    _cur, _ = spatial.bloom(_cur, _p['bloom'], cf)
    _cur, _ = spatial.halation(_cur, _p['halation'], cf)
    _cur, _ = spatial.grain(_cur, _p['grain'], cf)
    check('P1-4 空间序 = bloom→halation→grain（与手册逐步比，逐位相同）',
          float(np.max(np.abs(_out - _cur))) < 1e-12)

    # ---------- P1-6：ENTRY_CURVE 的 DR 档步进必须自洽 ----------
    for _model in cameras.ENTRY_CURVE:
        z = cameras.dr_zero_evs(_model)
        if '100' in z and '400' in z:
            check('P1-6 %s：DR100→DR400 零点差 ≈ +2EV' % _model,
                  abs((z['400'] - z['100']) - 2.0) < 0.25, '%.3f' % (z['400'] - z['100']))
        if '100' in z and '200' in z:
            _step = z['200'] - z['100']
            # ★ 09-13 深夜复测（n=305，`_debug/lab_dr_relift.py`，配对源 = RAF 内嵌机内 JPEG）：
            #   **相机成片的真实步进就是 ≈ +1.84**（DR200 抬得几乎和 DR400 一样多），
            #   不是物理上的 +1 ⇒ **别再把它当数据缺陷**。见 cameras.py 的复核结论。
            _known = (_model, '200') in cameras.FUJI_DR_STEP_KNOWN_BAD
            check('P1-6 %s：DR100→DR200 步进 ≈ +1.84EV（实测相机成片行为，不是缺陷）' % _model,
                  abs(_step - 1.84) < 0.25 or _known, 'step=%+.2f' % _step)
            check('P1-6 %s：DR200 的 +1.84 是相机行为 ⇒ KNOWN_BAD 必须为空' % _model,
                  not _known, 'step=%+.2f 仍登记为缺陷=%s' % (_step, _known))

    # ---------- P2-9：入口护栏有 k 地板 ----------
    _blown = np.ones((48, 56, 3))
    _o, _k = io.clip_guard(_blown, C)
    check('P2-9 clip_guard 有地板（旧下界 0.02 ≈ −5.6EV 会把正常曝光一起拖黑）',
          _k >= float(C.ENTRY_CLIP_GUARD_LO) - 1e-9,
          'k=%.3f 地板=%.2f' % (_k, C.ENTRY_CLIP_GUARD_LO))
    _safe = np.full((48, 56, 3), 0.4)
    _o2, _k2 = io.clip_guard(_safe, C)
    check('P2-9 不裁切的图仍然逐位不动（k=1）',
          abs(_k2 - 1.0) < 1e-12 and float(np.max(np.abs(_o2 - _safe))) < 1e-12)

    # ---------- 白点：**只设上限**（09-14 起不再有「必须等于 TGT_WHITE」那条路） ----------
    #   ⚠ 必须挑一张 **compress** 的图（L*50 > GUARD_MID_L 87）—— 那是现在**唯一**还会动曲线的路；
    #     偏暗/hold 的图 `correct` 会整条跳过（`y_out` 是空数组）。
    dk = np.full((64, 64, 3), 0.95)                 # 恒定很亮 ⇒ L*50 ≈ 97 > GUARD_MID_L 87 ⇒ compress
    rep_dk = analyze.analyze(_lin_from_disp(dk), dk, 'raw')
    _, info_dk = tone.correct(_lin_from_disp(dk), rep_dk, C)
    _cap_w = float(color.s2l(C.WHITE_CEIL))
    check('白点只设上限：compress 路真的动手，落点 ≤ WHITE_CEIL、且永不抬',
          rep_dk['decision'] == 'compress' and info_dk['applied']
          and float(info_dk['y_out'][-1]) <= _cap_w + 1e-9
          and float(info_dk['white_raise_ev']) <= 1e-9,
          'decision=%s y_out=%s cap=%.4f' % (rep_dk['decision'], info_dk['y_out'][-1:], _cap_w))


def t_entry_settle():
    """★ 位置逐张听相机（09-13 深夜）—— 「相机落点规律」+ 回落行为 + 口径守卫。"""
    import math

    law = cameras.settle_law_models()
    check('落点规律：至少标了一个机型', bool(law), str(law))
    check('落点规律：X-T30 III 已标定', 'x-t30 iii' in law)
    for _dr in (100, 200, 400):
        check('落点规律：X-T30 III DR%s 有偏移项' % _dr,
              _dr in cameras.SETTLE_LAW['x-t30 iii']['dr_off'])

    # ① 单调 + 序：同 DR 档内随场景亮度单调不减；同一 e 上 DR 越高落点越大
    _a = [cameras.entry_settle_level('X-T30 III', 100, e) for e in (-5.0, -3.0, -1.0, 0.0, 1.0)]
    check('落点规律：同 DR 档内随场景亮度单调不减（"逐张"项真的在动）',
          all(_a[i] <= _a[i + 1] + 1e-12 for i in range(len(_a) - 1)),
          str([round(v, 4) for v in _a]))
    _b = [cameras.entry_settle_level('X-T30 III', d, -3.0) for d in (100, 200, 400)]
    check('落点规律：同一场景亮度上 DR 越高落点越大', _b[0] < _b[1] < _b[2],
          str([round(v, 4) for v in _b]))

    # ② 钉住数值（拟合结果，不是随手填的；乱改 alpha/beta/dr_off 会红）
    _v4 = cameras.entry_settle_level('X-T30 III', 400, -3.39)
    check('落点规律：DR400 @e=−3.39 → 落点 ≈1.295（5 折留出集中位误差 1.32 L*）',
          abs(_v4 - 1.295) < 0.02, 'a=%.4f' % _v4)
    _v1 = cameras.entry_settle_level('X-T30 III', 100, -0.19)
    check('落点规律：DR100 @e=−0.19 → 落点 ≈0.662（比全局 0.55 亮 0.27 档）',
          abs(_v1 - 0.662) < 0.02, 'a=%.4f' % _v1)

    # ③ 回落：没标定 / DR 不认 / 拿不到 e / dr 缺失 ⇒ None（调用方回落 ENTRY_LEVEL）
    check('落点规律：没标定的机型回落（None）',
          cameras.entry_settle_level('A7C II', 400, -3.0) is None)
    check('落点规律：DR 不认回落（None）',
          cameras.entry_settle_level('X-T30 III', 800, -3.0) is None)
    check('落点规律：拿不到场景曝光回落（None）',
          cameras.entry_settle_level('X-T30 III', 400, None) is None)
    check('落点规律：dr 缺失回落（None）',
          cameras.entry_settle_level('X-T30 III', None, -3.0) is None)

    # ④ 入口接线：level 传了就用它；level=None 与 level=ENTRY_LEVEL 逐位相同（回落不改行为）
    _img = np.full((24, 28, 3), 0.2)
    _o_glob = io.entry_tone(_img, 0.0, C)
    _o_same = io.entry_tone(_img, 0.0, C, level=float(C.ENTRY_LEVEL))
    check('入口成形：level=None 与 level=全局值 逐位相同（回落 = 旧行为）',
          float(np.max(np.abs(_o_glob - _o_same))) < 1e-15)
    _o_hi = io.entry_tone(_img, 0.0, C, level=float(C.ENTRY_LEVEL) * 2.0)
    check('入口成形：level 给大 ⇒ 出图更亮（接线真的生效，不是接了没通）',
          float(np.mean(_o_hi)) > float(np.mean(_o_glob)),
          '%.5f > %.5f' % (float(np.mean(_o_hi)), float(np.mean(_o_glob))))

    # ⑤ 口径守卫：`scene_exposure_index` 必须等于 L_of_lin 那条链（改口径 ⇒ 规律整体偏掉）
    for _L in (12.0, 25.33, 50.0, 78.0):
        _g = np.zeros((20, 20, 3))
        _g[..., :] = color.lin_of_L(_L)
        _e = io.scene_exposure_index(_g)
        _want = math.log2(float(color.lin_of_L(_L)) / 0.18)
        check('曝光指数口径：L*%.2f → e=%+.4f（与 lab_pos_law_fit 逐字一致）' % (_L, _want),
              abs(_e - _want) < 1e-9, '%.6f vs %.6f' % (_e, _want))

    # ⑥ 位置来源（09-14 评审后）：**入口 settle 定基准 + 脸锚点做「只提不压」的补偿**；
    #    旧的「兜底提亮」整套（`_allow_lift` / `LIFT_DARK_*` / `ALLOW_LIFT*`）已删。
    _tsrc2 = open(tone.__file__, encoding='utf-8').read()
    check('位置：L1 只压不提（tone 里没有 allow_lift / EV_CAP_UP / LIFT_DARK）',
          all(k not in _tsrc2 for k in ('allow_lift', 'EV_CAP_UP', 'LIFT_DARK')))
    check('位置：锚点接在 L0 之前（pipeline 调 io.anchor_ev + io.refocus）',
          'io.anchor_ev(' in open(pipeline.__file__, encoding='utf-8').read()
          and 'io.refocus(' in open(pipeline.__file__, encoding='utf-8').read())

    # ⑦ 接线守卫：装配点必须看开关（关掉 ENTRY_SETTLE_ENABLE 即回落 ENTRY_LEVEL）
    _src = open(io.__file__, encoding='utf-8').read()
    check('入口接线：装配点读的是 ENTRY_SETTLE_ENABLE（开关没被绕开）',
          "getattr(C, 'ENTRY_SETTLE_ENABLE', False)" in _src
          and 'cameras.entry_settle_level(' in _src)


def _entry_analytic(cfg, img, ev, a):
    """入口形状的解析式（**不含趾部**）—— 用来给趾部测试当"参照臂"。

    ⚠ 必须与 `io.entry_tone` 里的算式逐字一致；只用来算"没有趾部时该是多少"。
    """
    g = float(getattr(cfg, 'ENTRY_GAMMA', 1.0))
    knee = float(getattr(cfg, 'ENTRY_KNEE', 1.0))
    ceil = float(getattr(cfg, 'ENTRY_CEIL', 1.0))
    Y = np.maximum(color.luma(np.clip(img, 0.0, None)), 1e-9)
    y = np.power(np.maximum(Y * (2.0 ** float(ev)), 1e-9), g) * float(a)
    d = max(ceil - knee, 1e-6)
    # ★ 09-14 晚：入口的肩有了**形状族**（`ENTRY_SHOULDER_KIND`），这里必须跟着分支，
    #   否则"趾部可回退"会拿老指数肩的解析值去比新对数肩 ⇒ 守错世界、假报失败。
    if str(getattr(cfg, 'ENTRY_SHOULDER_KIND', 'exp')).lower() == 'log':
        umax = max(float(getattr(cfg, 'ENTRY_SHOULDER_UMAX', 12.0)), 1e-6)
        u = np.clip((y - knee) / d, 0.0, umax)
        y = np.where(y > knee, knee + d * np.log1p(u) / np.log1p(umax), y)
    else:
        y = np.where(y > knee, knee + d * (1.0 - np.exp(-(y - knee) / d)), y)
    return y, Y


def t_entry_toe():
    """入口趾部（v0.3.8，09-13 深夜 SV 拍板「对齐作者线A」）。

    不变量：
      ① **可回退**：`ENTRY_TOE=1.0` 必须逐位等于"没有趾部"的解析式；
      ② **只动低位**：出口亮度 y > `中位×ENTRY_TOE_HI_REL` 的那一段逐位不变；
      ③ **★ 内容归一**：锚点是"图自己的出口亮度中位" ⇒ **亮图和暗图都只在各自最低的一小段咬**，
         两张图各自的**中位像素必须逐位不变**（这一条是绝对断点版本栽过的地方：
         固定断点在暗片上会让整张图掉进趾部，实测 0071 中灰 28.8 → 16.5）；
      ④ **方向单调**：floor 越小 ⇒ 暗部越暗；且整体仍**单调不减**（不许倒挂）；
      ⑤ 纯黑仍是纯黑（Y=0 → 0）。
    """
    print('[入口趾部：只压自己最低那一小段，中灰以上逐位不动]')
    EV, A = 2.184, 1.449                      # 0791 的实测零点/落点（定档用的真实参数）
    cfg = _Cfg(ENTRY_TOE=float(C.ENTRY_TOE))
    lo_rel, hi_rel = float(C.ENTRY_TOE_LO_REL), float(C.ENTRY_TOE_HI_REL)
    ramp = np.linspace(0.0, 0.6, 256)[None, :, None].repeat(3, axis=2)
    dark_ramp = ramp * 0.15                   # 同一形状的"暗图"（内容归一要能扛住它）

    y0, Y = _entry_analytic(C, ramp, EV, A)
    want_none = ramp * (y0 / Y)[..., np.newaxis]
    ym = float(np.median(y0))
    hi, lo = hi_rel * ym, lo_rel * ym
    up = y0 > hi
    act = (y0 > 0.0) & (y0 < hi)
    mid_i = int(np.argmin(np.abs(y0.reshape(-1) - ym)))

    off = io.entry_tone(ramp, EV, _Cfg(ENTRY_TOE=1.0), level=A)
    on = io.entry_tone(ramp, EV, cfg, level=A)
    deep = io.entry_tone(ramp, EV, _Cfg(ENTRY_TOE=0.20), level=A)
    L_off = color.L_of_lin(color.luma(off))
    L_none = color.L_of_lin(color.luma(want_none))
    L_on = color.L_of_lin(color.luma(on))
    L_deep = color.L_of_lin(color.luma(deep))

    check('趾部可回退：ENTRY_TOE=1.0 逐位等于"无趾部"解析值',
          float(np.max(np.abs(off - want_none))) < 1e-15,
          'max err %.2e' % float(np.max(np.abs(off - want_none))))
    check('趾部只动低位：出口 y > 中位×HI_REL 的像素逐位不变',
          up.any() and float(np.max(np.abs(on[up] - off[up]))) < 1e-15,
          '%d/%d 个像素在断点之上（断点 y=%.4f，中位 y=%.4f）' % (int(up.sum()), up.size, hi, ym))
    check('趾部在低位真的压深（不是接了没通）',
          act.any() and float(np.max(L_none[act] - L_on[act])) > 3.0,
          '最大压深 %.2f L*（%d 个像素在趾部区间）'
          % (float(np.max(L_none[act] - L_on[act])), int(act.sum())))
    check('★ 内容归一：图自己的中位像素逐位不变（亮图）',
          float(np.max(np.abs(on.reshape(-1, 3)[mid_i] - off.reshape(-1, 3)[mid_i]))) < 1e-15,
          '中位 y=%.4f，断点 y=%.4f ⇒ 中位在断点之上' % (ym, hi))
    yd, Yd = _entry_analytic(C, dark_ramp, EV, A)
    ymd = float(np.median(yd))
    mid_id = int(np.argmin(np.abs(yd.reshape(-1) - ymd)))
    off_d = io.entry_tone(dark_ramp, EV, _Cfg(ENTRY_TOE=1.0), level=A)
    on_d = io.entry_tone(dark_ramp, EV, cfg, level=A)
    check('★ 内容归一：暗图自己的中位像素也逐位不变（绝对断点版本在这里会整图变暗）',
          float(np.max(np.abs(on_d.reshape(-1, 3)[mid_id]
                              - off_d.reshape(-1, 3)[mid_id]))) < 1e-15,
          '暗图中位 y=%.4f，断点 y=%.4f' % (ymd, hi_rel * ymd))
    want_d = dark_ramp * (yd / Yd)[..., np.newaxis]
    bite_d = float(np.max(color.L_of_lin(color.luma(want_d))[yd < hi_rel * ymd]
                          - color.L_of_lin(color.luma(on_d))[yd < hi_rel * ymd]))
    check('★ 内容归一：暗图仍有趾部（没被"相对锚点"变成空操作）', bite_d > 3.0,
          '暗图最大压深 %.2f L*' % bite_d)
    check('趾部方向单调：floor 越小 ⇒ 暗部越暗',
          float(np.max(L_on - L_deep)) > 0.5 and float(np.max(L_deep - L_off)) < 1e-9,
          '0.32 vs 0.20 最大差 %.2f L*' % float(np.max(L_on - L_deep)))
    check('趾部不破坏单调（曲线仍非降）',
          bool(np.all(np.diff(L_on.reshape(-1)) >= -1e-12))
          and bool(np.all(np.diff(L_deep.reshape(-1)) >= -1e-12)))
    check('趾部不动纯黑（Y=0 仍为 0，没把最暗处抬起来）',
          float(color.luma(on)[0, 0]) < 1e-12 and float(color.luma(deep)[0, 0]) < 1e-12)
    # 出厂默认守卫：别把它悄悄关掉
    check('出厂默认：趾部开着，且 0 < LO_REL < HI_REL < 1（中位永远在断点之上）',
          0.0 < float(C.ENTRY_TOE) < 1.0 and 0.0 < lo_rel < hi_rel < 1.0,
          'TOE=%.2f  LO_REL=%.2f  HI_REL=%.2f' % (C.ENTRY_TOE, lo_rel, hi_rel))
    # 接线守卫：装配点必须真的把 cfg 传进 entry_tone（别在别处又写死一份形状）
    _src = open(io.__file__, encoding='utf-8').read()
    check('趾部接线：entry_tone 里读的是 ENTRY_TOE / ENTRY_TOE_HI_REL（没有写死的 magic number）',
          "getattr(cfg, 'ENTRY_TOE', 1.0)" in _src
          and "getattr(cfg, 'ENTRY_TOE_HI_REL', 0.84)" in _src)


def t_anchor():
    r"""★★ 「脸的锚点决定位置」（09-14 SV 选「乙/甲」）—— 一条曲线、不分区域、不用掩膜。

    位置由脸算（闭式解），形状走入口那一条曲线；背景的亮度是**结果**不是另设的靶。
    原先是写在 t_l4_caps 里的；两道区域闸删掉之后单独成一条。
    """
    import inspect
    print('[脸的锚点 · 位置]')
    check('锚点：出厂开着，靶 = 68（作者线A脸中位）',
          bool(C.ANCHOR_ENABLE) is True and float(C.ANCHOR_FACE_L) == 68.0)
    check('锚点·只提不压：出厂为真（脸已经够亮就不动它）', bool(C.ANCHOR_ONLY_UP) is True)
    check('锚点：收尾出厂开着（让**最终脸**落在靶上，不被 L2 又抬走）',
          bool(C.ANCHOR_FINISH) is True)
    src_io = inspect.getsource(io.refocus)
    check('锚点：位置是**重打入口那条曲线**（不是分区域压）—— refocus 里用肩的正/逆函数',
          '_shoulder_inv' in src_io and '_shoulder(' in src_io)
    # ⚠ 09-14 起 `run` = `io.load` + `run_from`（常驻服务要跳过解码）
    #   ⇒ 这些**源码级**断言必须看 `run_from`（链逻辑搬过去了），不是 `run`。
    prun2 = inspect.getsource(pipeline.run_from)
    check('锚点：pipeline.run 里在 L0 分析**之前**就重打了（analyze 用的是 lin_in）',
          'io.anchor_ev(s.disp, cfg)' in prun2 and 'lin_in' in prun2)
    check('锚点：收尾接在 L2（style.apply）之后',
          'io.finish_anchor(' in prun2)
    check('接线：两个开关都从 config 读（没写死）',
          'ANCHOR_ONLY_UP' in inspect.getsource(io.anchor_ev))

    ao = float(C.ANCHOR_EV_MAX)
    try:
        C.ANCHOR_EV_MAX = 4.0
        # 闭式解：合成"脸在 ~46"的图（真实逆光片的脸就在这个量级）
        # ⚠ 别用太暗的合成图：L* 在 Y<0.0089 时走**线性段**，立方根近似不成立。
        img3 = np.full((8, 256, 3), 0.4274, np.float32)          # 显示值 0.4274 ⇒ L* ≈ 46
        fk = np.zeros((8, 256), np.float32); fk[:, :64] = 1.0
        Lf = float(np.median(color.L_of_lin(color.Y_of(color.s2l(img3)))[0, :64]))
        check('锚点：合成图的脸落在 46 量级', 44.0 < Lf < 49.0, 'L脸=%.1f' % Lf)
        d, ai = io.anchor_ev(img3, C, masks=dict(face_skin=fk))
        check('锚点：脸比 68 暗 ⇒ d_ev > 0（要提亮）', d > 0, 'd_ev=%+.3f' % d)
        out = io.refocus(color.s2l(img3), d, C)
        La = float(np.median(color.L_of_lin(color.Y_of(np.clip(out, 0.0, None)))[0, :64]))
        check('锚点：重打之后脸真的落到 68（±1.5）', abs(La - 68.0) < 1.5, '%.2f' % La)
        check('锚点：三通道同倍率（只改亮度、不改颜色）',
              bool(np.allclose(out[..., 0] / np.maximum(color.s2l(img3)[..., 0], 1e-9),
                               out[..., 2] / np.maximum(color.s2l(img3)[..., 2], 1e-9), atol=1e-6)))
        bright = np.full((8, 256, 3), 0.72, np.float32)           # L* ≈ 88 ⇒ 已经在靶之上
        Lb2 = float(np.median(color.L_of_lin(color.Y_of(color.s2l(bright)))))
        d2, i2 = io.anchor_ev(bright, C, masks=dict(face_skin=fk))
        check('锚点·只提不压：脸已经够亮（%.0f > 68）⇒ d_ev = 0' % Lb2,
              d2 == 0.0 and i2.get('applied') is False, 'reason=%s' % i2.get('reason'))
        d0, i0 = io.anchor_ev(img3, C, masks=dict(face_skin=np.zeros((8, 256), np.float32)))
        check('锚点：拿不到脸 ⇒ 0（逐位不变）', d0 == 0.0 and i0.get('applied') is False)
        check('锚点：refocus(lin, 0) 逐位等于 lin',
              bool(np.array_equal(io.refocus(color.s2l(img3), 0.0, C), color.s2l(img3))))
        # 收尾：把"当前脸"挪回靶（模拟 L2 之后脸被抬到 ~76 的情形）
        out4, _ = io.finish_anchor(img3, C, masks=dict(face_skin=fk))
        La4 = float(np.median(color.L_of_lin(color.Y_of(color.s2l(out4)))[0, :64]))
        check('锚点收尾：脸偏离靶 ⇒ 挪回靶（±0.6）', abs(La4 - 68.0) < 0.6, '%.2f' % La4)
        out5, _ = io.finish_anchor(img3, C, masks=dict(face_skin=np.zeros((8, 256), np.float32)))
        check('锚点收尾：拿不到脸 ⇒ 逐位不变', bool(np.array_equal(out5, img3)))
    finally:
        C.ANCHOR_EV_MAX = ao


def _legacy(fn):
    r"""把 `fn` 包一层：跑的时候**临时关掉 09-14 新增的三块颜色**（丙/甲/乙）。

    为什么需要：那些"关掉 = 恒等""neutral = 恒等""只动 L*（a*/b* 不动）"的断言，
    是按**老颜色路**（Lab 里事后加偏移）写的；而新三块是**故意要改颜色**的
    （丙串扰 / 甲分通道 / 乙密度引擎）。测老路本身时把新的关掉，两边各自成立。
    """
    def w():
        _o = (C.CROSSTALK_ENABLE, C.LAYER_SPEED_ENABLE, C.DENSITY_ENABLE)
        try:
            C.CROSSTALK_ENABLE = C.LAYER_SPEED_ENABLE = C.DENSITY_ENABLE = False
            return fn()
        finally:
            (C.CROSSTALK_ENABLE, C.LAYER_SPEED_ENABLE, C.DENSITY_ENABLE) = _o
    w.__name__ = getattr(fn, '__name__', 'wrapped')
    return w


def t_film_color():
    """★ 09-14 新增三块颜色（`film.py`）：【丙】串扰 /【甲】分通道 /【乙】密度引擎。"""
    import inspect
    print('[真胶片成色：丙串扰 / 甲分通道 / 乙密度引擎]')
    check('丙 串扰：出厂开着，强度 = LIMO 给 Portra 400 标的 0.38',
          bool(C.CROSSTALK_ENABLE) is True and abs(float(C.CROSSTALK_AMOUNT) - 0.38) < 1e-9)
    check('甲 分通道：★★ 出厂**关掉**（它是整体 gamma，会同时污染 b*/彩度 ⇒ 方向性错误）',
          bool(C.LAYER_SPEED_ENABLE) is False)
    check('乙 密度引擎：出厂开着；**按参考实现只混 ≤50%**（Emulsifier `opacity = strength×0.5`）',
          bool(C.DENSITY_ENABLE) is True and 0.0 < float(C.DENSITY_STRENGTH) <= 0.5)

    d = film.load_curve('portra400')
    check('乙：曲线是真的 Kodak Portra 400（101 点，logE −4 → +1）',
          d['logE'].shape[0] == 100 and abs(d['logE'].min() + 4.0) < 0.01
          and abs(d['logE'].max() - 1.0) < 0.01, '%d 点' % d['logE'].size)
    check('★ 乙：分通道底片密度（那就是负片的橙色片基，不是"加"上去的）'
          'R 0.243 / G 0.656 / B 0.885',
          bool(np.allclose(d['d_min'], [0.243, 0.656, 0.885], atol=1e-3)),
          str(np.round(d['d_min'], 3)))

    # 丙：会改颜色、且关掉即恒等
    g = np.linspace(0.0, 1.0, 64).reshape(8, 8, 1).repeat(3, -1).astype(np.float64)
    c0 = film.crosstalk(g, 0.0)
    check('丙：amount=0 逐位恒等', bool(np.array_equal(c0, g)))
    c1 = film.crosstalk(g, 0.38)
    _lc = color.luma(g).mean()
    check('丙：真的动了颜色，且**不是**整体提亮/压暗（通道之间在换，不是一起升降）',
          float(np.max(np.abs(c1 - g))) > 1e-3
          and abs(float(color.luma(c1).mean() - _lc)) < 0.05,
          'maxΔ %.4f  亮度 %.4f→%.4f' % (float(np.max(np.abs(c1 - g))), _lc,
                                        float(color.luma(c1).mean())))
    check('丙：输出有界非负', bool(np.isfinite(c1).all()) and c1.min() >= -1e-9)

    # 甲
    s0 = film.layer_speeds(g, (0.96, 1.0, 1.03), 0.0)
    check('甲：strength=0 逐位恒等', bool(np.array_equal(s0, g)))
    s1 = film.layer_speeds(g, (0.96, 1.0, 1.03), 1.0)
    # 实测方向：`rgb**(1/speeds)`，speed<1 ⇒ 指数>1 ⇒ 中间调更暗 ⇒ 这一档 R 最暗、B 最亮。
    # （⚠ 符号与 LIMO 自述的"红层更柔"方向相反 —— 他们那个 `rgb` 的域和我们的显示域不同，
    #   我们只保证"三通道响应不同"，方向以实测为准。）
    check('甲：三通道真的走不同（实测 R < G < B，红层被压得最狠）',
          s1[..., 0].mean() < s1[..., 1].mean() < s1[..., 2].mean(),
          'R%.3f G%.3f B%.3f' % tuple(s1[..., i].mean() for i in range(3)))

    # 乙：★ 中灰守恒 + 单调
    m0 = film.density(np.array([[[0.45] * 3]]))[0, 0, 1]
    check('★ 乙：中灰进 = 中灰出（印相机的分通道曝光解出来的，±0.02）',
          abs(float(m0) - 0.45) < 0.02, '0.45 -> %.4f' % float(m0))
    xs = np.linspace(0.01, 0.99, 160)
    oo = film.density(xs.reshape(-1, 1, 1).repeat(3, -1))
    check('乙：整条传递曲线单调不减（不翻）',
          bool((np.diff(oo[:, 0, 1]) >= -1e-9).all()))
    mid = film.density(np.array([[[0.45] * 3]]))[0, 0]
    check('乙：三个通道的中灰都守恒（所以不会出一片红）',
          bool(np.allclose(mid, 0.45, atol=0.02)), str(np.round(mid, 4)))
    check('乙：透射率用 10^(−密度)（源码里就是这个式子，不是随手一条曲线）',
          '10.0 ** (-dens' in inspect.getsource(film.density))
    # ★★ 跟卷走：`neutral` 卷必须把三块关掉（A/B 对照底要干净）
    #   ⚠ 断言的**不是**"neutral = 原图"（neutral 继承基准成色 BASE_FULL，那本来就要动颜色），
    #     而是"**neutral 把三块关掉了**"：它和"总强度 = 0"逐位相同。
    import numpy as _np
    g2 = _np.linspace(0.0, 1.0, 48).reshape(6, 8, 1).repeat(3, -1).astype(_np.float64)
    _w0 = float(C.FILM_COLOR_W)
    try:
        _n = style._builtin(g2, C, stock=stocks.get('neutral'))
        C.FILM_COLOR_W = 0.0
        _n0 = style._builtin(g2, C, stock=stocks.get('neutral'))
        _p = style._builtin(g2, C, stock=stocks.get('portra400'))
        C.FILM_COLOR_W = 1.0
        _p1 = style._builtin(g2, C, stock=stocks.get('portra400'))
    finally:
        C.FILM_COLOR_W = _w0
    check('★ 跟卷走：neutral 卷把三块关掉了（= 与"总强度 0"逐位相同）',
          float(_np.max(_np.abs(_n - _n0))) < 1e-12,
          'maxΔ %.2e' % float(_np.max(_np.abs(_n - _n0))))
    check('★ 跟卷走：portra400 卷**会**吃到三块（开/关不一样）',
          float(_np.max(_np.abs(_p - _p1))) > 1e-3,
          'maxΔ %.4f' % float(_np.max(_np.abs(_p - _p1))))
    check('跟卷走：neutral 的 color 里显式有 film_color_w=0.0',
          float((stocks.get('neutral').get('color') or {}).get('film_color_w', 1.0)) == 0.0)
    check('接线：style._builtin 里三块都接上了',
          all(k in inspect.getsource(style._builtin)
              for k in ('film.layer_speeds', 'film.crosstalk', 'film.density')))
    # ★★ 09-14 逐层追踪抓到的 bug：乙 会把**入口交出来的近白全部砍平**
    #   （实测 0304 12.63%→0、0774 15.76%→0），白区层次也被压 ⇒ 表现是"白的东西不白、发肉"。
    #   ⇒ 修法 = **乙 在高光端淡出**（高光交给入口 + 影调曲线）。这条守卫不许被"简化"掉。
    # ★★ 09-14「引擎两条口子」的守护线（常驻服务靠这个才快得起来）
    #   ① `run` 必须是 `io.load` + `run_from`（**一份实现、两条入口**）—— 不许各写一套；
    #   ② `run_from` 不许自己调 `io.load`（那样缓存解码就白做了）。
    #   ⚠ 先**去掉 docstring** 再查 —— 不然文档里提到的 "io.load()" 会误判（已踩过一次）
    def _body(fn):
        _t = inspect.getsource(fn)
        _i = _t.find('\"\"\"')
        if _i >= 0:
            _j = _t.find('\"\"\"', _i + 3)
            if _j > 0:
                _t = _t[:_i] + _t[_j + 3:]
        return _t
    _run_src = _body(pipeline.run)
    _from_src = _body(pipeline.run_from)
    check('★★ 引擎口子：run = io.load + run_from（一份实现两条入口，不许各写一套）',
          'io.load(' in _run_src and 'run_from(' in _run_src)
    check('★★ 引擎口子：run_from 不许自己再调 io.load（缓存解码靠这条）',
          'io.load(' not in _from_src)
    #   ③ 常驻服务必须用 run_from（不是 run），否则每次都在重新解码
    try:
        from . import service as _svc
        check('★ 引擎口子：常驻服务用的是 run_from（不是 run）',
              'run_from(' in inspect.getsource(_svc))
    except Exception as _e:                                    # noqa: BLE001
        check('★ 引擎口子：常驻服务能 import', False)

    # ★★ 09-14 「脸的层次」重做（旧的 face_tone 同日删）—— 三条不许被"简化"掉：
    #   ① 权重必须是**真分割的 face_skin**（不是整个脸框）—— 那正是旧版把眉毛压黑的根因；
    #   ② 必须有**暗部下限** `FACE_BOT_CAP`（旧版只有亮部上限 ⇒ 眉毛一拉贴死）；
    #   ③ 必须有**死区**（跨度够就不动）。
    _fd = inspect.getsource(local.face_depth)
    check('★★ 脸的层次：力道只落 face_skin（不是整个脸框）',
          "masks']['face_skin'" in _fd or "'face_skin'" in _fd)
    check('★★ 脸的层次：必须有暗部下限 FACE_BOT_CAP（旧版没这条 ⇒ 眉毛贴死黑）',
          'FACE_BOT_CAP' in _fd and float(getattr(C, 'FACE_BOT_CAP', 0.0)) > 0.0)
    check('★ 脸的层次：必须有死区（跨度够就不动）',
          'FACE_DEPTH_DEAD' in _fd)
    check('★ 脸的层次：排在 L3 末尾（空间层之后才补）',
          'face_depth' in inspect.getsource(local.apply))
    _src = inspect.getsource(style._builtin)
    check('★ 乙 在高光端淡出（DENSITY_FADE_LO/HI + blend_map）—— 不许砍平近白',
          'DENSITY_FADE_LO' in _src and 'blend_map' in _src
          and float(getattr(C, 'DENSITY_FADE_HI', 1.0)) < 1.0)


def skip(name):
    """本机跑不了的检查（**不记 FAIL**）—— 但不许悄悄变绿：打印出来让人看见。"""
    print('  skip ' + name)


class _TmpCfg:
    """临时改全局 config（用完还原）—— 给"改一个参数会不会让缓存失效"这类检查用。"""

    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        self.old = {k: getattr(C, k) for k in self.kw}
        for k, v in self.kw.items():
            setattr(C, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(C, k, v)
        return False


# ---- 段缓存用到的两份函数清单 --------------------------------------------------
# 「被缓存起来的段」（命中就整段不跑）：它的产物必须**只依赖 SIG_CACHE 里的参数**。
_CACHED_FUNCS = [
    ('io.anchor_ev', io.anchor_ev), ('io.refocus', io.refocus),
    ('io.clip_guard', io.clip_guard), ('io.finish_anchor', io.finish_anchor),
    ('analyze.analyze', analyze.analyze), ('tone.correct', tone.correct),
    ('denoise.apply', denoise.apply), ('denoise._guided', denoise._guided),
    ('denoise._grad_mag', denoise._grad_mag), ('spektra.render', spektra.render),
    ('face.parse', face.parse), ('face.masks', face.masks),
    ('face.landmarks', face.landmarks), ('face._detector', face._detector),
    ('face.person_weight', face.person_weight),
    ('pipeline.run_from', pipeline.run_from), ('pipeline._stock_of', pipeline._stock_of),
    ('stocks.resolve_base', stocks.resolve_base), ('stocks.base_label', stocks.base_label),
]
# 「每次都要重跑、缓存碰不到」的段：
#   L3 / L4（看画面本身定力道，**不许缓存**）+ 中性基准那条路的 L1/L2/空间层
#   （段缓存只对真卷生效 ⇒ 这三层在真卷下根本不跑，相关滑杆由它们负责）。
_STAGE_FUNCS = [local.apply, local._protect, local.skin_floor, local.white_micro,
                local.face_depth, local.skin_mask, guard.enforce, guard._solve_scale,
                guard._cap_chroma,
                tone.correct, style.apply, style._builtin, style._tone_on, style.tone_ref,
                spatial.apply, spatial.resolve, stocks.color_params]

# ★★ 「解码阶段」的段（09-15 新增）—— 这两层**不归段缓存管、也不归 L0~L4 管**，
#   它们在 `io.load()` 里就跑完了（`load_raw` = RAW 那条，`load_std` = JPG 那条），
#   算完才存进 Sample。`io.entry_tone` 是被 `load_raw` 调用的那个入口曲线函数，算同一阶段。
#   ⚠ 为什么要单列：`service.py` 对 Sample 有**解码缓存** ⇒ 这一阶段读到的参数
#     **必须**进"解码签名"，否则改了它不会重新解码 ⇒ 滑杆是死的
#     （「暗部亮度」「整张亮暗(总)」两根就是这个坑，09-15 修掉）。
_LOAD_FUNCS = [('io.entry_tone', io.entry_tone),
               ('io.load_raw', io.load_raw), ('io.load_std', io.load_std)]
# ⚠ `MAX_SIDE` 是**默认参数**（`def load_raw(path, max_side=C.MAX_SIDE)`）—— `inspect.getsource`
#   看得见它，但真正的尺寸由调用方传进来、而且已经算在解码缓存的键里 ⇒ 不算"漏签名"。
_LOAD_SIG_EXEMPT = ('MAX_SIDE',)


def _cfg_reads(fn):
    """把一个函数源码里**读到的 config 名字**抠出来（`cfg.X` / `C.X` / `getattr(cfg,'X')`）。"""
    import inspect
    import re
    src = inspect.getsource(fn)
    names = set(re.findall(r'\b(?:cfg|C)\.([A-Z][A-Z0-9_]*)\b', src))
    names |= set(re.findall(r'getattr\(\s*(?:cfg|C)\s*,\s*[\'"]([A-Z0-9_]+)[\'"]', src))
    return names


def _mk_sample(disp, path='<自检>'):
    return io.Sample(_lin_from_disp(disp), disp, 'jpg', path)


def t_stage_cache():
    r"""段缓存（09-15 SV 选「A」）：把「胶片出图」那一段及其之前的产物留下来。

    本组检查真正要守住的是**一件事**：缓存**只许**让"不相关的滑杆"变快，
    **绝不许**让任何一根滑杆变成"拧了没反应"。
    """
    from . import service as _svc                     # 只读它的 PARAMS 白名单，不启服务

    print('[段缓存：允许复用的那一段，读的参数必须全在 SIG_CACHE 里]')
    # ---- ① 源码扫描：缓存段读到的 config，一个都不许漏在 SIG_CACHE 之外 ----
    cached_reads = set()
    bad = {}
    for label, fn in _CACHED_FUNCS:
        for n in _cfg_reads(fn):
            cached_reads.add(n)
            if not any(n.startswith(p) for p in pipeline.SIG_CACHE):
                bad.setdefault(n, []).append(label)
    check('缓存段读到的 config 全被 SIG_CACHE 覆盖（漏一个 = 拧了没反应）',
          not bad, ('漏了 %s' % sorted(bad)[:8]) if bad else '共 %d 个参数' % len(cached_reads))

    # ---- ② 反方向：每一根滑杆都必须**至少有一层真的读它** ----
    stage_reads = set()
    for fn in _STAGE_FUNCS:
        stage_reads |= _cfg_reads(fn)
    load_reads = set()                       # ★ 解码阶段那一层也算"真的有人在读"
    for _lbl, fn in _LOAD_FUNCS:
        load_reads |= _cfg_reads(fn)
    dead = [p['k'] for p in _svc.PARAMS
            if p['k'] not in cached_reads and p['k'] not in stage_reads
            and p['k'] not in load_reads]
    check('每根滑杆都至少有一层真的读它（否则就是根死滑杆）', not dead, '死的: %s' % dead)
    # 反过来：只被"缓存段"读、又不被 L3/L4 读的那些滑杆，必须出现在 SIG_CACHE 里
    only_cached = [p['k'] for p in _svc.PARAMS
                   if p['k'] in cached_reads and p['k'] not in stage_reads]
    miss = [k for k in only_cached if not any(k.startswith(p) for p in pipeline.SIG_CACHE)]
    check('只被缓存段读的滑杆必须在 SIG_CACHE 里（否则改了没反应）', not miss, '缺: %s' % miss)

    # ---- ③ 签名（键）的行为 ----
    print('[段缓存：键怎么变]')
    k0 = pipeline._sig(_Cfg(), pipeline.SIG_CACHE)
    check('同样的 config ⇒ 同样的键', k0 == pipeline._sig(_Cfg(), pipeline.SIG_CACHE))
    check('改「整张亮暗」（原「本张落点」）⇒ 键变（胶片必须重算）',
          k0 != pipeline._sig(_Cfg(SPEK_PE_SHIFT=1.3), pipeline.SIG_CACHE))
    check('改「整张浓淡」⇒ 键变（它作用在显影那一步）',
          k0 != pipeline._sig(_Cfg(SPEK_COUPLERS=0.3), pipeline.SIG_CACHE))
    check('改降噪 ⇒ 键变（降噪在缓存段里）',
          k0 != pipeline._sig(_Cfg(DENOISE_LUMA=0.2), pipeline.SIG_CACHE))
    check('改「脸的红绿」⇒ 键**不变**（这是缓存要救的那根滑杆）',
          k0 == pipeline._sig(_Cfg(SKIN_FLOOR_A=12.0), pipeline.SIG_CACHE))
    check('改「脸的层次」⇒ 键**不变**',
          k0 == pipeline._sig(_Cfg(FACE_SPAN_KMAX=3.0), pipeline.SIG_CACHE))
    check('改「脸的暗部下限」⇒ 键**不变**（FACE_DEPTH_* 也不许进缓存段）',
          k0 == pipeline._sig(_Cfg(FACE_DEPTH_DEAD=0.8), pipeline.SIG_CACHE))
    check('改「白区层次」⇒ 键**不变**',
          k0 == pipeline._sig(_Cfg(WHITE_MICRO=0.4), pipeline.SIG_CACHE))

    # ---- ④ 身份与 LRU ----
    print('[段缓存：身份 / 淘汰]')
    d1 = _gray_img(60, 90, gamma=0.4)
    d2 = _gray_img(80, 90, gamma=0.4)
    check('样本身份稳定', pipeline._sample_uid(_mk_sample(d1, 'D:/x/a.jpg'))
          == pipeline._sample_uid(_mk_sample(d1, 'D:/x/a.jpg')))
    check('样本身份能区分尺寸',
          pipeline._sample_uid(_mk_sample(d1, 'D:/x/a.jpg'))
          != pipeline._sample_uid(_mk_sample(d2, 'D:/x/a.jpg')))
    check('样本身份能区分路径',
          pipeline._sample_uid(_mk_sample(d1, 'D:/x/a.jpg'))
          != pipeline._sample_uid(_mk_sample(d1, 'D:/x/b.jpg')))
    sc = pipeline.StageCache(2)
    a = np.zeros((300, 300, 3))
    b = np.ones((300, 300, 3))
    sc.put('k1', disp1=a, disp2=b)
    h = sc.get('k1')
    check('StageCache 命中且拿回原对象', h is not None and h['disp2'] is b and sc.hits == 1)
    check('StageCache 未命中计数', sc.get('nope') is None and sc.misses == 1)
    sc.put('k2', disp1=a, disp2=b)
    sc.put('k3', disp1=a, disp2=b)
    check('StageCache 超上限淘汰最旧的那套', sc.get('k1') is None and sc.get('k3') is not None)
    n_mb = (a.nbytes + b.nbytes) * 2 / 1024.0 ** 2
    check('StageCache 记账（套数 / 内存）',
          sc.stats()['sets'] == 2 and abs(sc.stats()['mb'] - n_mb) < 0.2,
          '%s 期望 mb≈%.1f' % (sc.stats(), n_mb))
    sc.clear()
    check('StageCache 能清空', sc.stats()['sets'] == 0)

    # ---- ⑤ 缓存只在真卷那一路生效 ----
    print('[段缓存：只在真卷生效 / 关掉时逐位不变]')
    d = _gray_img(120, 180, gamma=0.4)
    s = _mk_sample(d, 'D:/x/neutral.jpg')
    sc = pipeline.StageCache(4)
    r1 = pipeline.run_from(s, stock='neutral', cache=sc)
    r2 = pipeline.run_from(s, stock='neutral', cache=sc)
    st = sc.stats()
    check('中性基准：缓存完全不介入（没存也没查）',
          st['sets'] == 0 and st['hits'] == 0 and st['misses'] == 0, str(st))
    check('中性基准：两次输出逐位相同', np.array_equal(r1.disp, r2.disp))
    check('报告里带缓存命中标记（默认 False）',
          r1.report.get('stage_cache', {}).get('hit') is False)
    r3 = pipeline.run_from(s, stock='neutral')          # cache=None
    check('中性基准：传缓存的与不传的逐位相同', np.array_equal(r1.disp, r3.disp))

    # ---- ⑥ 真卷那一路：miss → hit，产物必须精确复用（需要 spektrafilm）----
    print('[段缓存：真卷那一发（miss → hit）]')
    try:
        spektra._sf()
        has_sf = True
    except Exception as e:                                   # noqa: BLE001
        has_sf = False
        skip('真卷段缓存的行为检查（本机没装 spektrafilm：%s）' % str(e)[:70])
    if has_sf:
        # ⚠ 合成图里必须**贴一块皮肤色**（`_skin_patch`）：纯灰渐变没有皮肤像素，
        #   `skin_floor` 会直接判 'no_skin' 早退 ⇒ "改了参数画面却不变"会假红。
        ds = _gray_img(160, 240, gamma=0.4)
        ds[48:112, 88:152] = _skin_patch(12.0, 14.0, L=62.0, size=64)
        ss = _mk_sample(ds, 'D:/x/sf.jpg')
        sc = pipeline.StageCache(4)
        a0 = pipeline.run_from(ss, stock='portra400')                    # 关缓存
        a1 = pipeline.run_from(ss, stock='portra400', cache=sc)          # 未命中
        # ⚠ 端到端这一比要留容差：真卷的**扫描那一步**每次重抽噪声（实测最大 ~3/255），
        #   所以"两次独立全跑"本来就不会逐位相同 —— 不是缓存的问题。
        g1 = float(np.abs(a0.disp - a1.disp).max())
        check('真卷：未命中那一发与关缓存一致（差异只在扫描噪声内）', g1 <= 0.03,
              'max=%.4f' % g1)
        a2 = pipeline.run_from(ss, stock='portra400', cache=sc)          # 命中
        a3 = pipeline.run_from(ss, stock='portra400', cache=sc)          # 再命中
        check('真卷：第二次确实命中', a2.report['stage_cache']['hit'] is True
              and sc.stats()['hits'] >= 1, str(sc.stats()))
        # ★ 这两发的上游（disp1/disp2）是**同一份对象**，L3/L4 又是确定的
        #   ⇒ 必须逐位相同。这一条才是"缓存精确"的真正证据。
        check('真卷：两次命中 ⇒ 产物逐位相同（缓存被精确复用）',
              np.array_equal(a2.disp, a3.disp))
        gap = float(np.abs(a0.disp - a2.disp).max())
        check('真卷：命中 vs 关缓存 ⇒ 差异只在扫描噪声之内', gap <= 0.03, 'max=%.4f' % gap)
        with _TmpCfg(SKIN_FLOOR_A=12.0):
            b_free = pipeline.run_from(ss, stock='portra400')
            b_hit = pipeline.run_from(ss, stock='portra400', cache=sc)
        check('真卷：改「脸的红绿」仍然命中（真卷不重算）',
              b_hit.report['stage_cache']['hit'] is True)
        check('真卷：改「脸的红绿」产物确实跟着变（不是空转）',
              not np.array_equal(b_hit.disp, a2.disp))
        check('真卷：改「脸的红绿」命中那一发 = 关缓存那一发的尾巴',
              float(np.abs(b_free.disp - b_hit.disp).max()) <= 0.03)
        n_before = sc.stats()['misses']
        with _TmpCfg(SPEK_PE_SHIFT=1.15):
            pipeline.run_from(ss, stock='portra400', cache=sc)
        check('真卷：改「整张亮暗」⇒ 不命中（胶片必须重算）',
              sc.stats()['misses'] == n_before + 1, str(sc.stats()))


def t_sliders():
    r"""滑杆清单（09-15 SV 选「C」：13 根 → 23 根）。

    这一组守四件事，每一件都是**真踩过的坑**：
      ① **每根滑杆都真的能传进引擎** —— 白名单只认前缀，`CONTRAST`/`CHROMA_S` 这种
         "配不上任何前缀"的名字会被**静默丢弃**（拧了没反应、还不报错）。
      ② **初值 `dv` 是引擎现读的**，不是抄在表里的、更不许退回"区间中点"
         （退回中点就是老 bug：13 根里 12 根显示的数和实际生效的对不上）。
      ③ **方向**：只有一根是天生反的（真卷印相曝光），它靠 `inv` 翻过来；
         其余一律"往右 = 更强/更亮"。顺带把 `ENTRY_TOE` 的**名字**也按看得见的方向取了。
      ④ **成对联动**：黑柔的"加"和"化开"必须相等才量守恒，滑杆只动主项、
         从项由引擎自动同步。
    """
    from . import service as _svc

    print('[滑杆：清单本身]')
    ps = _svc.PARAMS
    ks = [p['k'] for p in ps]
    check('滑杆不重名', len(ks) == len(set(ks)), '%d 根' % len(ks))
    bad_exist = [k for k in ks if not hasattr(C, k)]
    check('每根滑杆的参数在 config 里真的存在（防打错字）', not bad_exist, '查无此键: %s' % bad_exist)
    bad_rng = [p['k'] for p in ps
               if not (float(p['lo']) < float(p['hi'])) or float(p['step']) <= 0]
    check('每根滑杆的区间/步长合法（lo < hi、step > 0）', not bad_rng, '坏: %s' % bad_rng)
    bad_grp = [p['k'] for p in ps if not p.get('grp') or not p.get('name') or not p.get('d')]
    check('每根滑杆都有 名字 / 分组 / 说明（前端全靠这三个画）', not bad_grp, '缺: %s' % bad_grp)

    print('[滑杆：初值 dv = 引擎此刻实际在用的值]')
    defs = _svc._param_defs()
    no_dv = [q['k'] for q in defs if not isinstance(q.get('dv'), float)]
    check('每根滑杆都算得出 dv', not no_dv, '算不出: %s' % no_dv)
    out_rng = [q['k'] for q in defs
               if isinstance(q.get('dv'), float)
               and not (q['lo'] - 1e-9 <= q['dv'] <= q['hi'] + 1e-9)]
    check('★ dv 必须落在区间内（否则滑杆一打开就顶在边上、一碰就跳）', not out_rng,
          '越界: %s' % out_rng)
    check('★ dv 里至少有一根**不等于区间中点**（等于中点就说明又退回"取中点"那个老 bug）',
          any(abs(q['dv'] - (q['lo'] + q['hi']) / 2.0) > 1e-9
              for q in defs if isinstance(q.get('dv'), float)))
    with _TmpCfg(SPEK_COUPLERS=0.31):
        d2 = {q['k']: q['dv'] for q in _svc._param_defs()}
    check('★ 改 config ⇒ dv 跟着变（说明是**现读**的，不是抄死在表里的）',
          abs(d2['SPEK_COUPLERS'] - 0.31) < 1e-9, str(d2['SPEK_COUPLERS']))

    print('[滑杆：方向（往右 = 更强/更亮）]')
    check('方向翻转只标在真的反的那一根上（人工核对过：真卷印相曝光是负片逻辑）',
          _svc._PARAM_INV == frozenset(['SPEK_PE_SHIFT']), str(sorted(_svc._PARAM_INV)))
    e_lo = _svc._parse_params('SPEK_PE_SHIFT:0.62')['SPEK_PE_SHIFT']
    e_hi = _svc._parse_params('SPEK_PE_SHIFT:1.43')['SPEK_PE_SHIFT']
    check('★「整张亮暗」往右 ⇒ 引擎值反而变小（那才是画面更亮）', e_hi < e_lo,
          '左 %.4f → 右 %.4f' % (e_lo, e_hi))
    check('「整张亮暗」的正中间 1.00 恰好 = 引擎不动',
          abs(_svc._parse_params('SPEK_PE_SHIFT:1.00')['SPEK_PE_SHIFT'] - 1.0) < 1e-12)
    dv_inv = {q['k']: q['dv'] for q in defs}['SPEK_PE_SHIFT']
    check('翻转过的滑杆，dv 给的是**显示值**（= 1 / 引擎值），不是引擎值',
          abs(dv_inv - 1.0 / float(getattr(C, 'SPEK_PE_SHIFT'))) < 1e-9, 'dv=%s' % dv_inv)

    print('[滑杆：成对联动（黑柔的守恒就靠它）]')
    check('只有「黑柔」挂了成对联动', _svc._PARAM_PAIR == {'BLOOM_AMOUNT': 'BLOOM_SPREAD'},
          str(_svc._PARAM_PAIR))
    got = _svc._parse_params('BLOOM_AMOUNT:0.10')
    check('★ 拧「黑柔」⇒「化开」自动跟着走（加进去的光 = 扣掉的，才守恒）',
          abs(got.get('BLOOM_SPREAD', -1.0) - 0.10) < 1e-12, str(got))
    check('成对的从项也在白名单里（否则同步过去会被丢掉）',
          all(any(m.startswith(x) for x in _svc.PARAM_PREFIX) for m in _svc._PARAM_PAIR.values()))
    check('只给从项仍然放行（留着给 A/B 研究用，别一刀切封死）',
          _svc._parse_params('BLOOM_SPREAD:0.05') == {'BLOOM_SPREAD': 0.05})

    print('[滑杆：白名单（"静默丢弃"只许发生在我们允许的地方）]')
    never = [q['k'] for q in defs if q['k'] not in _svc._parse_params('%s:1' % q['k'])]
    check('★ 每一根滑杆都真的能传进引擎（传不进去 = 拧了没反应）', not never,
          '被丢掉: %s' % never)
    blocked = [k for k in ('CONTRAST_S_SCALE', 'CONTRAST_S_CLAMP', 'CHROMA_REF',   # 内部系数
                           'ENTRY_SHOULDER_KIND', 'ENTRY_CURVE', 'LUT_PATH', 'BASE', 'STOCK')
               if _svc._parse_params('%s:1' % k)]
    check('内部系数 / 结构性参数被 PARAM_BLOCK 挡住（放开前缀会连坐它们）', not blocked,
          '漏网: %s' % blocked)
    dead_pre = [p for p in _svc.PARAM_PREFIX
                if not any(k.startswith(p) for k in dir(C) if k.isupper())]
    check('白名单里没有**死前缀**（配不上任何 config 键 = 只是看着有）', not dead_pre,
          '死前缀: %s' % dead_pre)
    check('清掉的历史死前缀没被加回来（整体色偏其实叫 COL_A，`COLOR_` 配不上）',
          'COLOR_' not in _svc.PARAM_PREFIX and 'SHARP_' not in _svc.PARAM_PREFIX)

    print('[滑杆：解码阶段那两根（入口参数）真的会重新解码]')
    load_reads = set()
    for _lbl, fn in _LOAD_FUNCS:
        load_reads |= _cfg_reads(fn)
    miss_load = sorted(n for n in load_reads
                       if n not in _svc._DECODE_SIG_KEYS and n not in _LOAD_SIG_EXEMPT)
    check('★ 解码阶段读到的参数全在解码签名里（漏了 = 改了不生效、滑杆是死的）',
          not miss_load, '漏: %s' % miss_load)
    k0 = _svc._load_key('x.jpg', 700)
    with _TmpCfg(ENTRY_TOE=0.5):
        k1 = _svc._load_key('x.jpg', 700)
    check('★ 改「暗部亮度」⇒ 解码缓存的键变（会重新解码，否则滑杆拉半天没反应）', k0 != k1)
    with _TmpCfg(ENTRY_SETTLE_SHIFT_EV=0.5):
        k2 = _svc._load_key('x.jpg', 700)
    check('★ 改「整张亮暗(总)」⇒ 解码缓存的键变', k0 != k2)
    with _TmpCfg(RAW_DECODE=dict(C.RAW_DECODE, half_size=True)):
        k2b = _svc._load_key('x.jpg', 700)
    check('改解码参数 RAW_DECODE ⇒ 解码缓存的键也变', k0 != k2b)
    with _TmpCfg(SKIN_FLOOR_A=18.0):
        k3 = _svc._load_key('x.jpg', 700)
    check('改「脸的红绿」⇒ 解码缓存的键**不变**（它跟解码无关，别白重解码一次）', k0 == k3)
    sentinel = object()
    check('签名一致 ⇒ 直接用缓存里的 Sample（不白解码）',
          _svc._ensure_decoded(0, dict(sample=sentinel, decoded=_svc._decode_sig()))
          is sentinel)
    # 光有"键会变"还不够 —— 得证明入口那个函数**真的改画面**
    ramp = np.linspace(1e-5, 0.35, 200 * 200).reshape(200, 200)
    ramp = np.stack([ramp, ramp * 0.97, ramp * 0.93], -1)
    a = io.entry_tone(ramp, 1.0, _Cfg(ENTRY_TOE=1.0))
    b = io.entry_tone(ramp, 1.0, _Cfg(ENTRY_TOE=0.32))
    da = float(np.mean(np.sort(a.ravel())[: a.size // 20]))
    db = float(np.mean(np.sort(b.ravel())[: b.size // 20]))
    check('★ 入口趾部真的改暗部（1.00 = 不压 ⇒ 暗部更亮）', db < da * 0.8,
          '不压 %.5f vs 出厂 %.5f' % (da, db))
    check('入口趾部**不动中灰**（只咬最底下那一段）',
          abs(float(np.median(a)) - float(np.median(b))) < 1e-6)
    # ★ 名字与**看得见的方向**一致：引擎的措辞是 ENTRY_TOE = "最深处的增益下限"（1.0 = 关掉），
    #   直接叫「入口黑位」会让人以为往右更黑（反的）⇒ 现在叫「暗部亮度」，往右必须真的更亮。
    _dk = lambda arr: float(np.mean(np.sort(arr.ravel())[: arr.size // 20]))   # noqa: E731
    check('★「暗部亮度」往右 ⇒ 暗部真的更亮（名字照看得见的方向取，不照引擎措辞）',
          _dk(io.entry_tone(ramp, 1.0, _Cfg(ENTRY_TOE=0.0)))
          < _dk(io.entry_tone(ramp, 1.0, _Cfg(ENTRY_TOE=1.0))))


# ============================================================================
# 09-15 SV「完善一下测试单元」补的四组
# 每组都对着一次**真踩过的事故**，不是"为了覆盖率凑数"：
#   t_entry_raw_only    ← 拧「整张亮暗(总)」没反应（喂的是 JPG）
#   t_stock_matrix      ← 真卷真正的出图在 spektrafilm 里，原来全库只碰到一卷
#   t_routing_contract  ← 改路由时真卷被误走 Lab 路，画面味道全变但没人发现
#   t_pipeline_e2e      ← 单层各自都对、装配起来的整链崩
# ============================================================================
def _exp_gray(h=180, w=240, gamma=1.6, seed=0):
    r"""给"卷 / 路由 / 整链"三组用的样本：**中低亮度**的灰阶。

    ⚠ 为什么要暗一点的样本（踩过）：真卷**自己定曝光**，样本一泛白，
      五个卷的中位会一起顶到 L*89.8、量出来一模一样
      ⇒ "换卷到底换没换画面"根本测不出来（第一版就是这么白量的）。
    """
    rng = np.random.default_rng(seed)
    y = np.linspace(0.0, 1.0, h)[:, None] * np.ones((1, w))
    img = np.repeat(np.clip(y ** gamma, 1e-4, 1.0)[..., None], 3, axis=-1)
    img += rng.normal(0, 0.002, img.shape)
    return np.clip(img, 0.0, 1.0)


def t_entry_raw_only():
    r"""入口那两根（「整张亮暗(总)」「暗部亮度」）**只认 RAW** —— 09-15 真踩的坑。

    症状：在工作台拧这两根、点渲染，**没反应**。两半原因，这一半是：
    工作台当时喂给引擎的是 **JPG**，而入口那一段（零点/成形/趾部/护栏）
    **只写在 `io.load_raw` 里**，`io.load_std`（JPG 那条）根本不跑
    ⇒ 这两根在 JPG 上**一点作用都没有**（实测 RAW 上 −5.2~+31 L*，JPG 上 0.00）。

    守三层，一层比一层硬（也一层比一层慢）：
      ① 源码级：`load_std` 里不许出现入口段的任何符号；`load_raw` 里**必须**有。
      ② 行为级（零素材）：同一张临时 JPG，在 `ENTRY_*` 两端取值下必须**逐位相同**。
      ③ 素材级（可选）：设了环境变量 `SVFILM_RAW_SAMPLE=<一张 RAW>` 才跑 ——
         同一张 RAW 上「整张亮暗(总)」必须**真的动画面**（否则"只认 RAW"是句空话）。
         ⚠ **不写死任何个人路径**（这仓库要开源），没设就 `skip` 并打印出来。
    """
    import inspect

    print('[入口：只认 RAW（喂 JPG 时那两根天生是死的）]')
    src_std = inspect.getsource(io.load_std)
    src_raw = inspect.getsource(io.load_raw)
    leaked = [t for t in ('ENTRY_', 'entry_tone', 'clip_guard', 'anchor_ev') if t in src_std]
    check('★ JPG 那条入口里不许出现「入口段」的任何符号', not leaked, '漏进: %s' % leaked)
    missing = [t for t in ('entry_tone', 'ENTRY_BIAS_ENABLE', 'ENTRY_TOE',
                           'ENTRY_SETTLE_SHIFT_EV', 'clip_guard') if t not in src_raw]
    check('★ RAW 那条入口必须真接上入口段（零点/成形/趾部/落点/护栏）', not missing,
          '缺: %s' % missing)

    tmpd = tempfile.mkdtemp(prefix='svfilm-entry-')
    tmp = os.path.join(tmpd, 'grad.jpg')
    try:
        io.save(_gray_img(160, 240, gamma=0.5), tmp)
        with _TmpCfg(ENTRY_BIAS_ENABLE=True, ENTRY_TONE=True, ENTRY_SETTLE_ENABLE=True,
                     ENTRY_SETTLE_SHIFT_EV=1.5, ENTRY_TOE=0.20, ENTRY_GAMMA=1.9):
            a = io.load_std(tmp, max_side=400)
        with _TmpCfg(ENTRY_BIAS_ENABLE=True, ENTRY_TONE=True, ENTRY_SETTLE_ENABLE=True,
                     ENTRY_SETTLE_SHIFT_EV=-1.0, ENTRY_TOE=1.00, ENTRY_GAMMA=1.0):
            b = io.load_std(tmp, max_side=400)
        d_disp = float(np.max(np.abs(a.disp - b.disp)))
        d_lin = float(np.max(np.abs(a.lin - b.lin)))
        check('★ 喂 JPG 时把这套入口参数推到两端 ⇒ 画面**逐位不变**（天生管不着 JPG）',
              d_disp == 0.0 and d_lin == 0.0, 'disp %.3e / lin %.3e' % (d_disp, d_lin))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
        try:
            os.rmdir(tmpd)
        except OSError:
            pass

    raw = os.environ.get('SVFILM_RAW_SAMPLE')
    if not raw or not os.path.exists(raw):
        skip('真 RAW 上「这两根真的动画面」（没设 SVFILM_RAW_SAMPLE，跳过）')
        return
    try:
        with _TmpCfg(ENTRY_SETTLE_SHIFT_EV=0.0):
            ra = io.load_raw(raw, 400)
        with _TmpCfg(ENTRY_SETTLE_SHIFT_EV=0.5):
            rb = io.load_raw(raw, 400)
        d = float(np.median(np.abs(ra.disp - rb.disp)))
        check('★ 同一张 RAW 上「整张亮暗(总)」必须真的动画面（0 = 入口成了装饰品）', d > 1e-4,
              '中位差 %.5f' % d)
    except Exception as e:
        skip('真 RAW 上的入口敏感性（这张解不开：%s）' % str(e)[:60])


def _counted_run(stock, sample=None):
    """跑一发整链，顺便数**每个模块函数被调了几次**（monkey-patch，用完立刻还原）。"""
    import collections

    _mods = [('tone.correct', tone, 'correct'), ('style.apply', style, 'apply'),
             ('spatial.apply', spatial, 'apply'), ('spektra.render', spektra, 'render'),
             ('denoise.apply', denoise, 'apply'), ('local.apply', local, 'apply'),
             ('guard.enforce', guard, 'enforce'), ('analyze.analyze', analyze, 'analyze')]
    cnt = collections.Counter()
    orig = {}
    for name, m, fn in _mods:
        orig[name] = getattr(m, fn)

        def _mk(n, f):
            def w(*a, **k):
                cnt[n] += 1
                return f(*a, **k)
            return w
        setattr(m, fn, _mk(name, orig[name]))
    try:
        s = sample if sample is not None else _mk_sample(_exp_gray(), 'D:/x/route.jpg')
        return cnt, pipeline.run_from(s, cfg=C, stock=stock, keep_stages=True)
    finally:
        for name, m, fn in _mods:
            setattr(m, fn, orig[name])


def t_stock_matrix():
    r"""每个卷都**端到端出一张** —— 真卷走的是 spektrafilm 那条路。

    ★ 为什么必须有这一组：原来的 `t_stocks` 只测 `style.apply`，而那是 **Lab 那条路**；
      真卷真正的出图在 `spektra.render`（spektrafilm）里，全库只有段缓存那组碰到过一卷
      ⇒ 某卷的 `spek=` 映射写错（片名/相纸名指到别的卷、`pe` 丢了），
      要等**用户点开那一卷**才炸 —— 这就是"点开某卷没反应"最可能的样子。
    ★ 顺带守一条：**换卷必须真的换画面**（两两差异），不然"卷"这个功能就是装饰。
    """
    print('[卷矩阵：每个卷端到端出一张]')
    outs, bad = {}, []
    for n in stocks.names():
        s = _mk_sample(_exp_gray(), 'D:/x/%s.jpg' % n)
        try:
            r = pipeline.run_from(s, cfg=C, stock=n, keep_stages=True)
        except Exception as e:
            bad.append('%s(%s: %s)' % (n, type(e).__name__, str(e)[:70]))
            continue
        d = np.asarray(r.disp)
        if not (np.all(np.isfinite(d)) and d.min() >= -1e-9 and d.max() <= 1 + 1e-9):
            bad.append('%s(产物非有限/越界)' % n)
            continue
        outs[n] = d
        rep, sp = r.report, (stocks.get(n) or {}).get('spek')
        if sp:
            # 真卷：必须真的走了 spektrafilm，而且用的是**这一卷自己的片和相纸**
            if rep['style'].get('how') != 'spektrafilm':
                bad.append('%s(没走 spektrafilm：%s)' % (n, rep['style'].get('how')))
            elif rep['style'].get('film') != sp.get('film'):
                bad.append('%s(跑了别人的片 %s ≠ %s)'
                           % (n, rep['style'].get('film'), sp.get('film')))
            elif abs(float(rep['style'].get('pe_base') or 0) - float(sp.get('pe') or 0)) > 1e-9:
                bad.append('%s(每卷的印相曝光 pe 没传进去)' % n)
        elif rep['style'].get('how') == 'spektrafilm':
            bad.append('%s(中性卷竟走了 spektrafilm)' % n)
    check('★ 每个卷都端到端出图成功，真卷真的走了自己的片/相纸/pe', not bad, '坏: %s' % bad)
    if len(outs) < 2:
        return
    weak, same = [], []
    ks = sorted(outs)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            mx = float(np.max(np.abs(outs[ks[i]] - outs[ks[j]])))
            weak.append(mx)
            if mx <= 0.02:
                same.append('%s≈%s(%.4f)' % (ks[i], ks[j], mx))
    check('★ 换卷必须真的换画面（两两差异都 > 0.02）', not same,
          '%d 对，最弱 %.4f%s' % (len(weak), min(weak), ('，太像: ' + ','.join(same)) if same else ''))


def t_routing_contract():
    r"""真卷 / 中性卷**各走哪条路** —— 用「谁被调用了」钉，不看引擎自报的字段。

    ★ 为什么必须数调用：`rep` 里那几个 `applied / how` 是**引擎自己写的**，
      断言"自报字段"等于自证。数"谁真的被调了"才是机制。
      （两组都留着：计数管机制，字段管"给人看的报告别写错"。）

    实测（合成样本）：
      真卷   tone 0 / style 0 / spatial 0 / **spektra 1** / denoise 1 / local 1 / guard 1
      中性卷 **tone 1 / style 1 / spatial 1** / spektra 0 / denoise 1 / local 1 / guard 1
    改路由（比如让真卷也走 Lab）会立刻红。
    """
    print('[路由：真卷 vs 中性卷各走哪条路（数调用，不看自报）]')
    plan = {
        # 卷:              tone style spatial spektra   （denoise/local/guard 两条路都必须跑）
        'portra400': dict(tone=0, style=0, spatial=0, spektra=1),
        'neutral': dict(tone=1, style=1, spatial=1, spektra=0),
    }
    for stock, exp in plan.items():
        try:
            cnt, r = _counted_run(stock)
        except Exception as e:
            skip('%s 的路由（跑不起来：%s）' % (stock, str(e)[:60]))
            continue
        got = {k: cnt[v] for k, v in (('tone', 'tone.correct'), ('style', 'style.apply'),
                                      ('spatial', 'spatial.apply'),
                                      ('spektra', 'spektra.render'))}
        check('★ %s 各走哪条路（真卷只走胶片段 / 中性卷只走我们那三层）' % stock,
              got == exp, '实测 %s 期望 %s' % (got, exp))
        common = {k: cnt[k] for k in ('denoise.apply', 'local.apply', 'guard.enforce',
                                      'analyze.analyze')}
        check('★ %s 入口(L0)/降噪/L3 局部/L4 护栏 **两条路都必须跑**' % stock,
              all(v == 1 for v in common.values()), str(common))
        # 自报字段也要跟机制一致（报告是给人看的，别写成另一回事）
        if stock == 'portra400':
            check('真卷的报告说的是实话（how=spektrafilm / L1 与空间层让位）',
                  r.report['style'].get('how') == 'spektrafilm'
                  and r.report['tone'].get('applied') is False
                  and r.report['spatial'].get('applied') is False,
                  'tone=%s spatial=%s' % (r.report['tone'].get('reason'),
                                          r.report['spatial'].get('reason')))


def t_pipeline_e2e():
    r"""整链：`run_from(keep_stages=True)` —— 各层产物齐全、形状一致、有界、逐层可查。

    ★ 为什么和 `t_pipeline_smoke` 不重复：那个是**手写串联**（逐层自己调），
      能证明"每层单独不炸"，证明不了"**装配起来的整链**不炸" ——
      层与层之间的接口改了、单层各自都对，整链照样崩。
    ⚠ 本组的样本**不会触发 L4 那道护栏**（实测它一个像素没动）⇒「只许往下」这条
      在这里是**方向性护栏**（防有人把它改成提亮），不是护栏力道的验收。
    """
    print('[整链：run_from(keep_stages=True)]')
    need = ('base', 'after_tone', 'after_style', 'after_spatial', 'after_local',
            'style_raw', 'spatial_raw')
    for stock, tag in (('portra400', '真卷'), ('neutral', '中性卷')):
        try:
            r = pipeline.run_from(_mk_sample(_exp_gray(), 'D:/x/e2e.jpg'),
                                  cfg=C, stock=stock, keep_stages=True)
        except Exception as e:
            check('★ %s 整链跑得通' % tag, False, '%s: %s' % (type(e).__name__, str(e)[:90]))
            continue
        check('★ %s 整链跑得通' % tag, True)
        st = r.report.get('stages') or {}
        miss = [k for k in need if k not in st]
        check('%s 逐层产物齐全（可逐层追踪）' % tag, not miss, '缺: %s' % miss)
        if miss:
            continue
        shp = {st[k].shape for k in need}
        check('%s 各层形状一致（换层不该改尺寸）' % tag, len(shp) == 1, str(shp))
        check('%s 各层都有限、都在 [0,1]' % tag,
              all(np.all(np.isfinite(st[k])) and st[k].min() >= -1e-9
                  and st[k].max() <= 1 + 1e-9 for k in need))
        d3, d4 = np.asarray(st['after_local']), np.asarray(r.disp)
        check('%s L4 那道护栏**只许往下**（逐像素不许上行）' % tag,
              bool((d4 <= d3 + 1e-9).all()), '最大上行 %.3e' % float((d4 - d3).max()))
        check('%s 出图 = 过完 L4 的那张（不是某层中间产物）' % tag,
              d4.shape == np.asarray(r.disp).shape and float(d4.max()) <= 1 + 1e-9)


def t_stock_map_valid():
    r"""卷表里的**每个名字**都必须在 vendored spektrafilm 里真实存在。

    ★ 为什么必须有这一组（09-15 挖出来的真 bug）：
      `spektra.STOCK_MAP` 里 `gold200` / `ultramax400` 的相纸名写成了
      `kodak_endura_premium`，而人家叫 **`premier`** ⇒ 谁哪天把这两卷放出来，
      **一点就崩**（`init_params` 报 `FileNotFoundError`，找不到那个 json）。
      一直没暴露：这俩在"备着"那一栏、界面没放、自检也只测那 5 个真卷。
      **纯查表就能防住的事故**，不该等用户点出来。
    ★ 顺手守一条**重复声明**：同一份映射在 `stocks.TABLE[*]['spek']` 里又写了一遍
      （film / print / pe）⇒ 两边漂了就是"报告说的是一个卷、实际跑的是另一个"。
    ⚠ 查的是**仓库自带那份**（`_sf()` 选定的 root），不是本机 pip 装的那份 ——
      本机那份是 editable 指向已退休老目录的，见 `spektra._sf()` 的注释。
    """
    print('[卷表：每个名字都要在 spektrafilm 里真实存在]')
    try:
        spektra._sf()
        from spektrafilm.model.stocks import FilmStocks, PrintPapers
        import spektrafilm as _sfmod
    except Exception as e:                                        # noqa: BLE001
        check('★ 卷表校验：能 import vendored spektrafilm 的卷/纸枚举', False,
              '%s: %s' % (type(e).__name__, str(e)[:90]))
        return
    films = {e.value for e in FilmStocks}
    papers = {e.value for e in PrintPapers}
    check('★ 卷表校验：能 import vendored spektrafilm 的卷/纸枚举', True,
          '胶片 %d 种 / 相纸 %d 种' % (len(films), len(papers)))

    bad_f = sorted({f for f, _p in spektra.STOCK_MAP.values() if f not in films})
    bad_p = sorted({p for _f, p in spektra.STOCK_MAP.values() if p not in papers})
    check('★ 卷表里**每个负片名**在 spektrafilm 里都真实存在',
          not bad_f, '查无此片: %s' % bad_f)
    check('★ 卷表里**每个相纸名**在 spektrafilm 里都真实存在',
          not bad_p, '查无此纸（拼错？）: %s' % bad_p)

    # 光有枚举还不够 —— 名字对但 json 缺，照样是 init 时才炸。逐个查文件。
    pdir = os.path.join(os.path.dirname(os.path.abspath(_sfmod.__file__)),
                        'data', 'profiles')
    miss = []
    for f, p in sorted(set(spektra.STOCK_MAP.values())):
        for nm in (f, p):
            if not os.path.exists(os.path.join(pdir, nm + '.json')):
                miss.append(nm)
    check('★ 每一条 (负片, 相纸) 的 profile json **真的在盘上**',
          not miss, '缺文件: %s' % sorted(set(miss)))

    # 两份卷表不许漂
    drift = []
    for n in stocks.names():
        sp = (stocks.get(n) or {}).get('spek')
        if not sp:
            continue                      # neutral 本来就没有 spek
        ours = spektra.STOCK_MAP.get(n)
        if ours is None:
            drift.append('%s 不在 STOCK_MAP 里' % n)
        elif (ours[0], ours[1]) != (sp.get('film'), sp.get('print')):
            drift.append('%s: STOCK_MAP %s ≠ TABLE %s'
                         % (n, ours, (sp.get('film'), sp.get('print'))))
    check('★ 两份卷表不许漂（STOCK_MAP 与 stocks.TABLE 的片/纸必须一致）',
          not drift, str(drift))


def t_paper_choice():
    r"""相纸（09-15 SV 选「C」）：印相纸要能选，而且**换了必须真的换画面**。

    ★ 为什么要有这一组：一张真卷出图 = **(负片, 相纸)** 二元组。相纸是**最终成色的另一半**
      —— 同一卷负片印在不同的纸上 = 两套不同的颜色（人像最经典的就是
      「柯达卷 + Portra Endura」和「富士卷 + Crystal Archive」两套脸色）。
      原来只开放了负片那一半，相纸写死在卷表里出不来。
    ★ 这一组守三件事，每件都对应一类"看着对、其实对不上"：
      ① 换纸**真的换画面**（不是把参数收下就忘了）—— 而且必须能和"扫描那步重抽噪声"分开；
      ② 脏纸名**既不许崩、也不许静默换一张**（要在报告里说出来）；
      ③ 段缓存的键里**必须带纸**（不带 ⇒ 换了纸还是吐上一张的图 = 白换，且看不出来）。

    实测（160×240 合成样本、带皮肤块；`_gray_img` 的种子固定）：
      「同一张纸跑两次」的差（= 扫描那步重抽的噪声）  mean 0.000084 / max 0.0141
      「换一张纸」的差                                mean 0.061191 / max 0.3101
      ⇒ 均值差 **730 倍** —— 所以下面可以用"远大于噪声"来判。
    """
    # ---- ① 表与默认（纯查表，不需要 spektrafilm）----
    print('[相纸：表 / 配套纸 / 认不得就回落]')
    plist = spektra.papers('portra400')
    own = spektra.STOCK_MAP['portra400'][1]
    dflt = [p['name'] for p in plist if p.get('isDefault')]
    check('相纸表能列出来，而且**恰好一张**标着"本卷配套"',
          len(plist) >= 6 and len(dflt) == 1, '%d 张，配套 %s' % (len(plist), dflt))
    check('★ 配套纸 = 卷表里写的那张（前端拿到的默认不能是另一张）',
          bool(dflt) and dflt[0] == own, '配套 %s / STOCK_MAP %s' % (dflt, own))
    check('★ 真卷之外没有"相纸"这回事（中性卷 / 不认得的卷 ⇒ 空表）',
          spektra.papers('neutral') == [] and spektra.papers('__no_such__') == [],
          '中性卷 %d 张' % len(spektra.papers('neutral')),
          '中性卷也列一堆纸 ⇒ 把一个拧不动的开关摆给用户')

    check('★ 不传纸 ⇒ 用本卷配套纸，且**不**标"回落"',
          spektra.resolve_paper('portra400') == (own, False, ''))
    check('★ 传的就是配套纸 ⇒ 原样用，也不标"回落"',
          spektra.resolve_paper('portra400', own) == (own, False, ''))
    check('★★ 传一个**认不得**的名字 ⇒ 回落到配套纸 + 说出来（不崩、不静默）',
          spektra.resolve_paper('portra400', 'kodak_endura_premium')
          == (own, True, 'unknown_paper:kodak_endura_premium'),
          '（用的就是当初 STOCK_MAP 里写错的那个名字）',
          '把脏名字原样塞给 init_params ⇒ FileNotFoundError；静默换一张 ⇒ 用户以为在用 A 纸')
    check('★ 中性卷 / 不认得的卷 ⇒ 没有相纸可解析，也**不**该标成"回落"',
          spektra.resolve_paper('neutral', 'kodak_portra_endura')
          == (None, False, 'unknown_stock'))

    try:
        spektra._sf()
        from spektrafilm.model.stocks import PrintPapers
        sf_papers = {e.value for e in PrintPapers}
    except Exception as e:                                    # noqa: BLE001
        skip('相纸：换纸真的换画面 / 报告 / 缓存键（本机没装 spektrafilm：%s）'
             % str(e)[:70])
        return
    miss = [n for n in spektra.PAPER_ORDER if n not in sf_papers]
    check('★ 我们那张相纸表里**每个名字**都在 vendored spektrafilm 里真实存在',
          not miss, '查无此纸: %s' % miss,
          '上游改了枚举值 / 我们拼错了 ⇒ 选中那一张就崩（卷表拼错那次的翻版）')

    # ---- ② 换纸真的换画面（要和"扫描噪声"分开）----
    print('[相纸：换了必须真的换画面]')
    # ⚠ 合成图里必须**贴一块皮肤色**：真卷这一路有肤色局部层，纯灰渐变没有皮肤像素
    #   ⇒ 会出现"改了参数画面却不变"的假红（同 t_stage_cache 的注释）。
    ds = _gray_img(160, 240, gamma=0.4)
    ds[48:112, 88:152] = _skin_patch(12.0, 14.0, L=62.0, size=64)
    s = _mk_sample(ds, 'D:/x/paper.jpg')
    P1, P2 = 'kodak_portra_endura', 'fujifilm_crystal_archive_typeii'

    a = pipeline.run_from(s, stock='portra400', paper=P1)
    b = pipeline.run_from(s, stock='portra400', paper=P2)
    c = pipeline.run_from(s, stock='portra400', paper=P1)      # 同纸再来一发 = 噪声基线
    nz = float(np.abs(np.asarray(a.disp) - np.asarray(c.disp)).mean())
    df = float(np.abs(np.asarray(a.disp) - np.asarray(b.disp)).mean())
    check('同一张纸跑两次基本一致（差异只来自扫描那步重抽噪声）',
          nz < 0.005, 'mean=%.6f' % nz,
          '噪声比预期大 ⇒ 下面的"换纸判据"要重新标定（别硬调阈值糊过去）')
    check('★★ 换一张相纸必须**真的换画面**（差异远大于噪声）',
          df > 0.02 and df > 10 * max(nz, 1e-6),
          '换纸 mean=%.5f / 噪声 mean=%.6f ⇒ %.0f 倍'
          % (df, nz, df / nz if nz > 0 else float('inf')),
          '换纸只把参数收下、没进物理链 ⇒「相纸」是个纯装饰')

    # ---- ③ 报告要说实话 ----
    sa, sb = a.report['style'], b.report['style']
    check('★ 报告里写的就是**实际用的那张纸**',
          sa.get('print') == P1 and sb.get('print') == P2,
          '%s / %s' % (sa.get('print'), sb.get('print')))
    check('★ `print_default` = "用的到底是不是本卷配套纸"',
          sa.get('print_default') is True and sb.get('print_default') is False,
          '%s / %s' % (sa.get('print_default'), sb.get('print_default')))
    check('★ 正常选纸不该被标成"回落"',
          sa.get('print_fallback') is False and sb.get('print_fallback') is False)

    # ---- ④ 脏名字：不崩 + 报告里说出来 ----
    try:
        z, z_err = pipeline.run_from(s, stock='portra400',
                                     paper='kodak_endura_premium'), None
    except Exception as e:                                    # noqa: BLE001
        z, z_err = None, '%s: %s' % (type(e).__name__, str(e)[:80])
    check('★★ 脏纸名**不许崩**（当初 STOCK_MAP 里的相纸名写错就是这么炸的）',
          z_err is None, z_err or 'ok',
          '原样塞给 init_params ⇒ FileNotFoundError，用户点一下就崩')
    if z is not None:
        sz = z.report['style']
        check('★★ 脏纸名**也不许静默**：报告里要标出来 + 带上那个名字',
              sz.get('print') == own and sz.get('print_fallback') is True
              and 'kodak_endura_premium' in str(sz.get('print_fallback_reason')),
              'print=%s fallback=%s why=%s'
              % (sz.get('print'), sz.get('print_fallback'),
                 sz.get('print_fallback_reason')),
              '静默换一张 ⇒ 用户以为在用 A 纸、其实出的是 B 纸，还看不出哪里不对')

    # ---- ⑤ 段缓存的键必须带纸（不带 = 白换）----
    sc = pipeline.StageCache(4)
    c1 = pipeline.run_from(s, stock='portra400', paper=P1, cache=sc)
    c2 = pipeline.run_from(s, stock='portra400', paper=P2, cache=sc)
    d12 = float(np.abs(np.asarray(c1.disp) - np.asarray(c2.disp)).mean())
    check('★★ 换了纸 ⇒ 段缓存必须**不命中**（否则这一发直接把上一张的图吐回来）',
          c2.report['stage_cache']['hit'] is False,
          'hit=%s stats=%s' % (c2.report['stage_cache']['hit'], sc.stats()),
          '缓存键不带纸 ⇒ 换了纸画面不变，用户以为"这张纸没效果"')
    check('★ 换纸之后产物确实不同（没被上一张兜住）', d12 > 0.02, 'mean=%.5f' % d12)


def main():
    for fn in (t_color, t_analyze, t_tone_mid_target, t_tone_monotone, _legacy(t_style_lock),
               _legacy(t_style_contrast_direction), _legacy(t_style_tone_curve), _legacy(t_style_chroma_ends), t_denoise,
               t_guard, t_lut,
               t_io_roundtrip, t_stocks, t_spatial_off, t_spatial_grain,
               t_spatial_bloom_halation, t_local_skin_floor, t_entry_bias, t_pipeline_smoke,
               t_review_fixes, t_entry_settle, t_entry_toe, t_anchor,
               t_film_color, t_stage_cache, t_sliders,
               # 09-15 补的四组（各对着一次真踩过的事故）
               t_entry_raw_only, t_stock_matrix, t_routing_contract, t_pipeline_e2e,
               # 09-15 晚：卷表拼错名（选中即崩）⇒ 纯查表就能防住
               t_stock_map_valid,
               # 09-15 晚：相纸可选（SV 选「C」）⇒ 换纸必须真的换画面
               t_paper_choice):
        fn()
    print('-' * 52)
    if FAIL:
        print('失败 %d 项: %s' % (len(FAIL), ', '.join(FAIL)))
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
