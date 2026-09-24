# -*- coding: utf-8 -*-
r"""自检 —— 动完任何一层都要跑。不依赖任何样片，纯合成图 + 不变量。

  python -m svFilm.selftest

★ 09-23 边界重划之后，这里只验三件事：

  1. **胶片风格**（9 条预设）真的被 spektrafilm 读到、字段没被静默吞掉
  2. **曝光风格**（三条档）真的把画面搬到靶、曲线单调、极端图不崩
  3. **契约**：曝光在胶片**之前**、不认得的名字当场报错、缓存只在同参数时命中

迭代期那些层（风格层 / 空间层 / 局部肤色 / 降噪 / 护栏 / 真卷旋钮）的几百条检查，
随那些模块一起删了 —— 它们验的对象已经不存在。
"""
import os
import sys

import numpy as np

from . import color, config as C, io, pipeline, presets, spektra, tone

# ★ 09-23：凡是要点名「一条胶片风格」的地方就用这一条（别把中文名写死到各处）。
_PRESET = 'Portra400薄荷'

# ★ 自检跟着**当前配置**走：曝光风格作用在引擎之后（三套力度）还是之前（三个绝对靶）。
#   两套契约完全不同，所以下面凡是分叉的地方都按它选一条 —— 不许只测其中一条。
_AFTER = bool(getattr(C, 'TONE_AFTER_ENGINE', False))

FAIL = []


def check(name, cond, extra='', why=''):
    """`extra` = 现场（成败都打）；`why` = **红了意味着什么**（只在红时打）。"""
    print(('  ok   ' if cond else '  FAIL ') + name
          + (('   ' + extra) if extra else '')
          + (('   ⇒ ' + why) if (why and not cond) else ''))
    if not cond:
        FAIL.append(name + ((' — ' + why) if why else ''))


# ---------------------------------------------------------------------------
# 合成图
# ---------------------------------------------------------------------------

def _gray_img(h=180, w=240, gamma=0.45, seed=0, tint=(1.02, 0.99, 0.95)):
    """一张有结构的灰度图（不是纯噪声）—— 分位判据要的是有宽有窄的分布。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    g = 0.5 + 0.34 * np.sin(xx / 23.0) * np.cos(yy / 31.0)
    g = np.clip(g, 0.02, 0.98) ** gamma
    g = np.clip(g + 0.03 * rng.standard_normal((h, w)), 1e-3, 1.0)
    out = np.stack([g * tint[0], g * tint[1], g * tint[2]], -1)
    return np.clip(out, 0.0, 1.0)


def _lin_from_disp(d):
    return np.clip(color.s2l(np.clip(np.asarray(d, np.float64), 0.0, 1.0)), 0.0, None)


def _mk_sample(disp, path='<自检>'):
    return io.Sample(_lin_from_disp(disp), disp, 'jpg', path)


def _main():
    groups = [
        ('胶片风格：9 条预设', t_presets),
        ('胶片风格：换一条真的换画面', t_preset_differs),
        ('曝光风格：三条档', t_styles),
        ('曝光风格：真的把画面搬到靶', t_tone_hits),
        ('曝光风格：曲线单调 + 极端图不崩', t_tone_sane),
        ('曝光风格：脸锚点只做有限幅修正', t_tone_bias),
        ('契约：曝光在胶片之前 + 名字不认得要报错', t_contract),
        ('段缓存：同参数命中、换风格不命中', t_cache),
        ('服务：路由只剩该有的那几条', t_routes),
    ]
    for title, fn in groups:
        print('[%s]' % title)
        try:
            fn()
        except Exception as e:                                    # noqa: BLE001
            check('★ 这一组跑到底（没炸）', False,
                  '%s: %s' % (type(e).__name__, str(e)[:160]),
                  '这一组里有一个异常没接住 —— 后面的检查全都没跑到')
    print('-' * 68)
    if FAIL:
        print('失败 %d 条：' % len(FAIL))
        for f in FAIL:
            print('  ✗ ' + f)
        return 1
    print('全部通过')
    return 0


# ---------------------------------------------------------------------------
# 1. 胶片风格（9 条预设）
# ---------------------------------------------------------------------------

def t_presets():
    ns = presets.names()
    check('9 条胶片风格都在（不是只写了几个名字）', len(ns) == 9, '、'.join(ns))

    miss = [n for n in ns if not presets.has(n)]
    check('每条的 JSON 都真在磁盘上', not miss, '缺: %s' % miss,
          '预设文件丢了 ⇒ 界面上会多出一个点不动的选项')

    # ★★ 关键字段必须**真的落到 params 上**，不是被引擎冲回出厂
    #   （`GrainParams`/`HalationParams` 都是普通 dataclass、没有 `__slots__`
    #    ⇒ 字段名写错**不报错**，只会静默多出一个没人读的属性）
    #    ⚠ 字段名跟 vendor 版本绑死：**0.3.2 = `agx_particle_*`**、0.3.4 = `particle_*`。
    #      这条**必须**跟着 `_apply` 一起改 —— 它就是防"换版本忘了换字段名"的。
    p = presets.digested(_PRESET)
    d = presets.load_raw(_PRESET)
    check('颗粒真落到 params（防字段改名后静默吞掉）',
          abs(float(p.film_render.grain.agx_particle_area_um2)
              - float(d['grain']['particle_area_um2'])) < 1e-9,
          '%.3f vs %.3f' % (p.film_render.grain.agx_particle_area_um2,
                            d['grain']['particle_area_um2']),
          '颗粒字段对不上 ⇒ 有人改了 vendor 的字段名（0.3.2 叫 agx_particle_*、0.3.4 叫 particle_*）'
          ' —— 换 vendor 版本时 `presets._apply` 和这里要一起改')
    check('光晕强度真落到 params（不被卷的抗晕层标签冲掉）',
          abs(float(p.film_render.halation.halation_strength[0]) * 100.0
              - float(d['halation']['halation_strength'][0])) < 1e-6,
          '%.4f vs %.1f' % (p.film_render.halation.halation_strength[0] * 100.0,
                            d['halation']['halation_strength'][0]),
          '`apply_stocks_specifics=True` 会按卷的抗晕层把它冲回出厂 ⇒ 必须保持 False')
    check('配平基准钉死成 public 那一对（不跟 vendor 版本漂）',
          abs(float(p.enlarger.y_filter_neutral) - float(C.PRESET_NEUTRAL_Y)) < 1e-9
          and abs(float(p.enlarger.m_filter_neutral) - float(C.PRESET_NEUTRAL_M)) < 1e-9,
          'y %.3f m %.3f' % (p.enlarger.y_filter_neutral, p.enlarger.m_filter_neutral))
    # ★★ 上面那条是**自证**（拿 params 比 config），把 config 两个数对调它照样绿。
    #    09-23 真踩过一次「Y/M 写反」⇒ 这里再钉**绝对数值**：
    #    读 DB 的顺序是 `c, m, y`（vendor `params_builder.apply_database_neutral_print_filters`）
    #    ⇒ public 0.3.2 的 `fujifilm_pro_400h` = [0.0, 50.713(M), 51.423(Y)]。
    check('★★ 而且 Y/M **不许对调**（绝对值，来源＝public 0.3.2 的滤片库：M 小、Y 大）',
          abs(float(C.PRESET_NEUTRAL_Y) - 51.423) < 5e-4
          and abs(float(C.PRESET_NEUTRAL_M) - 50.713) < 5e-4,
          'Y %.3f / M %.3f' % (C.PRESET_NEUTRAL_Y, C.PRESET_NEUTRAL_M),
          '对调了整张会偏色，而上面那条自证检查看不出来')

    # 每条都得能被 spektrafilm 认得（负片 / 相纸 profile 名拼错 = 渲染那一步直接崩）
    bad = []
    for n in ns:
        try:
            presets.pe_of(n)
            assert presets.film_of(n) and presets.paper_of(n)
        except Exception as e:                                    # noqa: BLE001
            bad.append('%s(%s)' % (n, e))
    check('每条的负片 / 相纸名字都能被 spektrafilm 认得', not bad, '、'.join(bad),
          'profile 名拼错 ⇒ 渲染那一步 FileNotFoundError')


def t_preset_differs():
    """换胶片风格 ⇒ 画面必须真的换（防"九条其实是同一份"）。"""
    lin = _lin_from_disp(_gray_img())
    outs = {n: presets.render(lin, n, C) for n in presets.names()}
    a = presets.names()[0]
    diffs = {n: float(np.max(np.abs(outs[n] - outs[a]))) for n in presets.names() if n != a}
    lo = min(diffs.values())
    check('换一条预设 ⇒ 画面真的不同（最小的那对也有肉眼可见的差）', lo > 0.005,
          '最小 %s %.4f' % (min(diffs, key=diffs.get), lo),
          '两条渲染结果几乎一样 ⇒ 预设 JSON 是同一份拷的')


# ---------------------------------------------------------------------------
# 2. 曝光风格
# ---------------------------------------------------------------------------

def t_styles():
    check('三条档都在（高长调 / 中性调 / 暗调）', tone.names() == ['高长调', '中性调', '暗调'],
          '、'.join(tone.names()))
    check('默认档由 config 给、且在档表里', C.STYLE in tone.names(), C.STYLE)
    check('名字不认得 ⇒ 回默认档（不是静默不动）',
          tone.rel_of('不存在的一档') == tone.rel_of(C.STYLE))

    if _AFTER:
        # ---- 作用在成片上：三条档 = 三套力度（中位 / 亮部 / 黑位各自往下搬多少）----
        rl = {n: tone.rel_of(n) for n in tone.names()}
        check('★★ 三档都是"往下搬"的量，且黑位真的往下（不是往上提）',
              all(v['bl_down'] > 0 and v['ev_down'] >= 0 and v['hi_down'] >= 0 for v in rl.values()),
              ' / '.join('%s 中位↓%.2f档 亮部↓%.0f 黑位↓%.0f'
                         % (n, rl[n]['ev_down'], rl[n]['hi_down'], rl[n]['bl_down'])
                         for n in tone.names()),
              '黑位那项是**往下压**（对齐鹿井/大师带）；写反了就是"提阴影"，会发灰')
        check('★ 「暗调」黑位压得比「高长调」多（档名与实际效果对得上）',
              rl['暗调']['bl_down'] > rl['中性调']['bl_down'] > rl['高长调']['bl_down'],
              ' / '.join('%.0f' % rl[n]['bl_down'] for n in tone.names()))
        check('★ 中性调对齐鹿井 32 张量出来的那个数（黑位 −11）',
              abs(rl['中性调']['bl_down'] - 11.0) < 3.0,
              '%.1f' % rl['中性调']['bl_down'],
              '数值来自 `master_resurvey.json` 里鹿井 32 张的内容归一形状，改之前先回去量一遍')
    else:
        st = {n: tone.get(n) for n in tone.names()}
        check('★★ 落点是**从大师真片量出来的**那三个数（P70/P50/P30 = 69.9/58.8/40.5）',
              abs(st['高长调']['mid_L'] - 69.9) < 0.1
              and abs(st['中性调']['mid_L'] - 58.8) < 0.1
              and abs(st['暗调']['mid_L'] - 40.5) < 0.1,
              ' / '.join('%.1f' % st[n]['mid_L'] for n in tone.names()),
              '数值被改过 ⇒ 要改请连 `tone.py` 顶部那段"怎么量的"一起改，别只动数')
        check('三档亮度严格拉开（高 > 中 > 暗）',
              st['高长调']['mid_L'] > st['中性调']['mid_L'] > st['暗调']['mid_L'])
        check('名字不认得 ⇒ 回中性调（不是静默不动）',
              tone.get('不存在的一档')['mid_L'] == st['中性调']['mid_L'])


def t_tone_hits():
    """同一张图，三档分别按各自的契约落地。"""
    if _AFTER:
        disp = _gray_img(seed=3)
        got, got5 = {}, {}
        for n in tone.names():
            out, i = tone.settle_finished(disp, n, C)
            rl = tone.rel_of(n)
            # 相对量：黑位按自己的量往下搬；中位只在 ev_down>0 时才动
            check('%s：黑位按自己的量往下搬（不是"算出来了但没做"）' % n,
                  i['L5_out'] < i['L5_in'] - rl['bl_down'] * 0.6 + 1e-6,
                  '黑位 %.1f → %.1f（要往下 %.0f）' % (i['L5_in'], i['L5_out'], rl['bl_down']))
            check('%s：亮部/中位按自己的量走（不动就是真的不动）' % n,
                  i['L95_out'] < i['L95_in'] - rl['hi_down'] * 0.6 + 1e-6
                  and (rl['ev_down'] > 0.02 or abs(i['L50_out'] - i['L50_in']) < 1.0),
                  '中 %.1f→%.1f  亮 %.1f→%.1f'
                  % (i['L50_in'], i['L50_out'], i['L95_in'], i['L95_out']),
                  '方向反了：这三项都是"往下搬"')
            got[n] = i['L50_out']
            got5[n] = i['L5_out']
        check('三档真的拉得开：暗调最暗、黑位最深',
              got['高长调'] >= got['中性调'] > got['暗调']
              and got5['暗调'] < got5['中性调'] <= got5['高长调'],
              '中位 %.1f / %.1f / %.1f   黑位 %.1f / %.1f / %.1f'
              % (got['高长调'], got['中性调'], got['暗调'],
                 got5['高长调'], got5['中性调'], got5['暗调']))
        return
    lin = _lin_from_disp(_gray_img(seed=3))
    for n in tone.names():
        _, i = tone.apply(lin, n, C)
        t = tone.get(n)
        check('%s：落点真的到靶（不是"算出来了但没做"）' % n,
              abs(i['L50_out'] - t['mid_L']) < 1.2,
              'L50 %.1f vs 靶 %.1f' % (i['L50_out'], t['mid_L']),
              '三点曲线没把中位搬到位 ⇒ 锚点的 `_t(y50)` 与曲线结点对不上')
    mids = [tone.apply(lin, n, C)[1]['L50_out'] for n in tone.names()]
    check('三档出来的画面亮度真的拉开了', (max(mids) - min(mids)) > 20,
          '%.1f / %.1f / %.1f' % tuple(mids))


def t_tone_sane():
    disp = _gray_img(seed=5)
    lin = _lin_from_disp(disp)
    if _AFTER:
        out, _ = tone.settle_finished(disp, '中性调', C)
        out_y = _lin_from_disp(out)
        Y0, Y1 = color.Y_of(lin), color.Y_of(out_y)
    else:
        out, _ = tone.apply(lin, '中性调', C)
        Y0, Y1 = color.Y_of(lin), color.Y_of(out)
    # ① 单调：按输入亮度排好序之后，输出亮度必须也是不减的（翻折 ⇒ 暗部出现台阶）
    #   ⚠ 不能拿两次 `argsort` 的结果比"顺序一致率" —— 输入里有一大片相等的像素
    #     （合成图有 clip 出来的平台），`argsort` 对**并列**的排法不稳定
    #     ⇒ 那样量出来的"不一致"是**并列造成的假信号**，不是曲线翻折。
    ys = Y1.ravel()[np.argsort(Y0.ravel())]
    worst = float(np.min(np.diff(ys))) if ys.size > 1 else 0.0
    check('★ 曲线单调（明暗顺序没被打乱，暗部不会出台阶）', worst >= -1e-9,
          '最小步进 %.3g' % worst,
          '三点之间出现非单调 ⇒ 靶那三点没有按 黑<中<白 夹过')

    check('输出没有 NaN / Inf', bool(np.all(np.isfinite(out))), '',
          '曲线里出现了除零或 log(0)')

    # ② 极端图不崩（全黑 / 全白 / 单值）
    for tag, img in (('全黑', np.zeros((8, 8, 3))),
                     ('全白', np.ones((8, 8, 3))),
                     ('全中间灰', np.full((8, 8, 3), 0.18))):
        try:
            if _AFTER:
                o, _ = tone.settle_finished(img, '中性调', C)
            else:
                o, _ = tone.apply(img, '中性调', C)
            good = bool(np.all(np.isfinite(o)))
        except Exception as e:                                    # noqa: BLE001
            good = False
            print('     （%s 抛了 %s）' % (tag, e))
        check('极端图不崩：%s' % tag, good, '',
              '分位全相等时 log(0) 或除以零 —— 必须有 _EPS 兜着')

    # ③ 保险丝：单个像素的增益不许超过 TONE_MAX_GAIN_EV
    #   ⚠ 只在"动作在引擎之前"那条路上成立：那条曲线直接乘在线性图上。
    #     成片那条（`settle_finished`）是显示域的三点曲线，量纲不同，不套这个上限。
    if not _AFTER:
        cap = float(C.TONE_MAX_GAIN_EV)
        g = np.maximum(Y1, 1e-9) / np.maximum(Y0, 1e-9)
        mx = float(np.max(np.abs(np.log2(np.maximum(g, 1e-9)))))
        check('单个像素的增益在保险丝之内（不是在拉伸噪声）', mx <= cap + 1e-6,
              '最大 %.2f EV / 上限 %.2f' % (mx, cap))
    else:
        check('★ 成片那条的增益有界（三点曲线本身不会把暗部拉爆）',
              float(np.max(Y1)) <= 1.0 + 1e-6 and float(np.max(Y1) / max(np.max(Y0), 1e-9)) < 8.0,
              '最大输出 %.3f' % float(np.max(Y1)))


def t_tone_bias():
    """脸锚点的偏移（`ev_bias`）必须真的动落点。

    ⚠ 只有"曝光风格在引擎**之前**"那条路有脸锚点；动作挪到引擎之后就不做了
    （脸锚点是为"自己标的真卷"校落点用的，见 `pipeline.run_from`）。
    """
    if _AFTER:
        check('★ 动作在引擎之后 ⇒ 不做脸锚点（报告里写明）',
              True, '已跳过（当前配置不走这条路）')
        return
    lin = _lin_from_disp(_gray_img(seed=7))
    a = tone.apply(lin, '中性调', C)[1]['L50_out']
    b = tone.apply(lin, '中性调', C, ev_bias=0.5)[1]['L50_out']
    c = tone.apply(lin, '中性调', C, ev_bias=-0.5)[1]['L50_out']
    check('脸锚点给的偏移真的动落点（+0.5 档更亮、-0.5 档更暗）', b > a > c,
          '%.1f / %.1f / %.1f' % (b, a, c))
    check('★ 偏移是**有限幅**的（`pipeline` 里夹 ANCHOR_LIMIT_EV）',
          0.0 < float(C.ANCHOR_LIMIT_EV) <= 1.5, '%.2f 档' % C.ANCHOR_LIMIT_EV,
          '不限幅的话脸锚点会整个盖掉曝光风格 ⇒ 三档就没区别了')


# ---------------------------------------------------------------------------
# 3. 契约
# ---------------------------------------------------------------------------

def t_contract():
    # ① 曝光风格与胶片引擎的先后
    calls = []
    if _AFTER:
        _f, _p0 = tone.settle_finished, presets.render

        def _t(*a, **k):
            calls.append('tone')
            return _f(*a, **k)

        def _p(*a, **k):
            calls.append('presets')
            return _p0(*a, **k)
        tone.settle_finished, presets.render = _t, _p
    else:
        _t0, _p0 = tone.apply, presets.render

        def _t(*a, **k):
            calls.append('tone')
            return _t0(*a, **k)

        def _p(*a, **k):
            calls.append('presets')
            return _p0(*a, **k)
        tone.apply, presets.render = _t, _p
    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=11)), stock=_PRESET, style='中性调')
    finally:
        if _AFTER:
            tone.settle_finished, presets.render = _f, _p0
        else:
            tone.apply, presets.render = _t0, _p0
    if _AFTER:
        check('★★ 曝光风格跑在胶片引擎**之后**（控制不了成片亮度，只能事后收）',
              calls[:2] == ['presets', 'tone'], '调用序: %s' % calls[:4],
              '顺序反了 = 又回到"在引擎之前调亮度"，实测那样三条档只拉开 8.8（靶上该 29.4）')
    else:
        check('★★ 曝光风格跑在胶片风格**之前**（因果顺序：先给光、再显影）',
              calls[:2] == ['tone', 'presets'], '调用序: %s' % calls[:4],
              '顺序反了 = 对印好的照片再翻拍调增益，物理上不成立，且显示域没有高光余量')

    # ② 不认得的名字**当场报错**
    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=13)), stock='根本没有这一条')
        ok = False
    except KeyError:
        ok = True
    check('胶片风格名字不认得 ⇒ 当场报错（不是静默出一张别的）', ok,
          '', '"名字不认得就静默走默认"是本项目最阴的一类坑，出现过三次')

    # ③ 报告要说实话（进去多少 / 出来多少，能自查，不用读图）
    r = pipeline.run_from(_mk_sample(_gray_img(seed=17)), stock=_PRESET, style='暗调')
    t = r.report['tone']
    if _AFTER:
        check('报告里带着"进去多少 / 出来多少"（能自查，不用读图）',
              t['L5_out'] < t['L5_in'] - 1.0
              and r.report['style_target']['bl_down'] == 14.0
              and r.report['style'] == '暗调',
              '黑位 %.1f → %.1f' % (t['L5_in'], t['L5_out']))
        check('★ 报告里带上了三个力度，且黑位是**往下搬**的（`bl_down` > 0）',
              t['bl_down'] > 0 and t['ev_down'] >= 0 and t['hi_down'] >= 0,
              '中位↓%.2f档 亮部↓%.0f 黑位↓%.0f' % (t['ev_down'], t['hi_down'], t['bl_down']),
              '方向搞反了：黑位要往下压（对齐鹿井），不是往上提')
        # ⚠ 这里**不量** L5 的升降：喂进去的是合成小图、又过了一遍胶片引擎，
        #   分布已经很窄（L5≈L50≈L95），三点曲线会退化。真正的方向判据在
        #   `t_tone_hits`（直接喂 `_gray_img`，分布是正常的）。
    else:
        check('报告里带着"靶是多少 / 实到多少"（能自查，不用读图）',
              abs(t['L50_out'] - t['mid_L']) < 1.5
              and r.report['style_target']['mid_L'] == 40.5
              and r.report['style'] == '暗调',
              '靶 %.1f 实到 %.1f' % (t['mid_L'], t['L50_out']))

    check('成片没有 NaN / Inf 且在 [0,1]',
          bool(np.all(np.isfinite(r.disp))) and float(r.disp.min()) >= 0.0
          and float(r.disp.max()) <= 1.0)


def t_cache():
    s = _mk_sample(_gray_img(seed=19))
    c = pipeline.StageCache()
    r1 = pipeline.run_from(s, stock=_PRESET, style='中性调', cache=c)
    r2 = pipeline.run_from(s, stock=_PRESET, style='中性调', cache=c)
    check('同参数 ⇒ 命中缓存（切回来不用重跑）',
          bool(r2.report['stage_cache']['hit']))
    r3 = pipeline.run_from(s, stock=_PRESET, style='高长调', cache=c)
    check('★ 换曝光风格 ⇒ **不**命中（否则就是"拧了没反应"）',
          not r3.report['stage_cache']['hit'])
    r4 = pipeline.run_from(s, stock='C200青蓝', style='中性调', cache=c)
    check('★ 换胶片风格 ⇒ **不**命中', not r4.report['stage_cache']['hit'])
    check('两档出来的画面真的不同',
          float(np.max(np.abs(r2.disp - r3.disp))) > 0.01,
          '最大差 %.4f' % np.max(np.abs(r2.disp - r3.disp)))


def t_routes():
    from . import service as svc
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'service.py'), encoding='utf-8').read()
    for p in ('/stocks', '/styles', '/render', '/export', '/load', '/base', '/health'):
        check('路由 %s 在' % p, ("u.path == '%s'" % p) in src)
    for p in ('/params', '/bases', '/papers'):
        check('★ 已经删掉的路由 %s 确实不在了' % p, ("u.path == '%s'" % p) not in src,
              '', '滑杆 / 相纸 / 成色基准都随新边界删了，接口不该还留着')
    check('默认端口还是 8765（台子那边钉着）', svc.DEFAULT_PORT == 8765, str(svc.DEFAULT_PORT))


if __name__ == '__main__':
    sys.exit(_main())
