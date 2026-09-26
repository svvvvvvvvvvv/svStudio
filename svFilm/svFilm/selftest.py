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
        ('二次调色：分色 + 混色（L2/L3）', t_grade),
        ('直方图（LR 画法：亮度 + RGB 叠加 + 5 个区）', t_hist),
        ('技术层：贴边 / 堆积 / 挤压系数', t_tech),
        ('靶按预设分组', t_targets),
        ('肤色层：真脸掩膜 / 空窗口 / 三件事', t_skin),
        ('可调键：config 里真的接上了（防"假旋钮"）', t_config_keys),
        ('人脸掩膜：算在解码后 / 只算一次 / 报告不说谎', t_mask_contract),
        ('场景判据：六轴 / 只在线性域判过曝 / 覆盖只加不减', t_scene),
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
    # ⚠ 别写死条数 —— 加一条预设就要改一次，那是「拿 A 比 A」的假检查。
    #   改成钉**外部真值**：原来那 9 条的名字必须都还在（一条都不能被改名/弄丢），
    #   外加鹿井那条（Ultramax400沉褐）也必须在。条数只当信息，不当断言。
    _BASE = ('C200过曝', 'C200透明', 'C200青蓝', 'Ektar100浓彩', 'Portra400淡雅',
             'Portra400空气感', 'Portra400薄荷', 'Pro400H清风', 'Pro400H马卡龙')
    _gone = [n for n in _BASE if n not in ns]
    check('原来那 9 条一条都不少（不是只写了几个名字）', not _gone,
          '共 %d 条；丢了的: %s' % (len(ns), _gone or '无'),
          '这条钉的是名字，不是条数 ⇒ 以后加预设不用改它')
    check('鹿井那条预设在（Ultramax400沉褐）', 'Ultramax400沉褐' in ns, '、'.join(ns),
          '这条是照他主页 514 张对齐的那条')

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



def t_hist():
    """直方图工具（`hist.py`）：LR 那四条曲线 + 5 个区 + 裁切三角。

    ⚠ 这一组是给"以后**看直方图不看数字**"兜底的 —— 图要是画错了，
      SV 会照着一张错的图做判断，比数字错还危险。
    """
    from . import hist

    # ① 四条曲线：长度 256、0~1、无 NaN
    c, sh_c, hi_c = hist.channels(_gray_img(seed=31))
    check('★ 四条直方图曲线（亮度 + R/G/B）：各 256 bin、落在 0~1、无 NaN',
          len(c) == 4 and all(len(x) == 256 for x in c)
          and all(np.all(np.isfinite(x)) and x.min() >= 0.0 and x.max() <= 1.0 + 1e-9 for x in c),
          '亮度最大 %.2f' % float(np.max(c[0])))
    check('亮度那条跟 RGB 三条不是同一条（真算了，不是复制）',
          float(np.max(np.abs(c[0] - c[1]))) > 0.01)
    # ★ 纵轴必须是**固定口径**（满格 = 单档占画面 YMAX_RATIO），不是按每张图自己的最大值
    g = np.asarray(_gray_img(seed=31), np.float64)
    ys = hist._luma(g).astype(np.int32).ravel()
    cnt = np.bincount(ys, minlength=256).astype(np.float64)
    want = (cnt.max() / (cnt.sum() * hist.YMAX_RATIO)) ** hist.Y_GAMMA
    got = float(np.max(hist.channels(g)[0][0]))      # [0]=四条曲线, [0][0]=亮度那条
    check('★★ 纵轴是**固定口径**（满格 = 单档占画面 %.0f%%），不是每张自己归一'
          % (hist.YMAX_RATIO * 100),
          abs(got - min(want, 1.0)) < 1e-6, '实到 %.4f  应到 %.4f' % (got, min(want, 1.0)),
          '改成"按每张图自己的最大值归一"的话每张刻度都不一样 ⇒ 跨图不可比')
    check('★ 同一张图喂两次峰值完全一样（口径稳定）',
          abs(float(np.max(hist.channels(g)[0][0])) - got) < 1e-12)

    # ② 裁切：全黑 ⇒ 阴影裁切亮；全白 ⇒ 高光裁切亮；中间灰 ⇒ 都不亮
    _, a_bk, b_bk = hist.channels(np.zeros((32, 32, 3)))
    _, a_wh, b_wh = hist.channels(np.ones((32, 32, 3)))
    _, a_gy, b_gy = hist.channels(np.full((32, 32, 3), 0.5))
    check('★ 全黑 ⇒ 阴影裁切三角要亮（LR 里那个左上的蓝三角）', a_bk > 0.9, '%.3f' % a_bk)
    check('★ 全白 ⇒ 高光裁切三角要亮（右上的红三角）', b_wh > 0.9, '%.3f' % b_wh)
    check('中间灰 ⇒ 两个三角都不亮', a_gy < 5e-4 and b_gy < 5e-4,
          '%.4f / %.4f' % (a_gy, b_gy))

    # ③ 出图：尺寸对、别炸
    im = hist.draw(_gray_img(seed=33), w=400, h=160, title='自检')
    check('画得出来、尺寸对', im.size == (400, 160), str(im.size))
    pn = hist.panel(_gray_img(seed=35), w=400, title='自检')
    check('缩略图 + 直方图 一体也画得出来', pn.size[0] == 400 and pn.size[1] > 160, str(pn.size))
    st = hist.stack([(None, '参照', c, (sh_c, hi_c))], w=400)
    check('★ 只喂曲线（画"一组片的平均直方图"）也画得出来', st.size == (400, 240), str(st.size))

    # ④ 命令行入口别断（`python -m svFilm.hist` 以后要常用）
    check('命令行入口在（`python -m svFilm.hist`）', callable(hist._main))


def t_skin():
    """L4 肤色层（09-24 重做）：真脸掩膜 · 空窗口不写 nan · 三件事都认靶。

    ⚠⚠ 教训：原来用**色相窗**定位肤色 —— 实测那个窗里**只有 10.5% 是真皮肤**
      （其余是墙/木头/黄叶）⇒ 提亮的是背景。现在用 `face.py` 的人脸皮肤掩膜。
    """
    from . import targets
    from . import grade

    # ① 模板里三个肤色靶都在（相对亮度 / 相对彩度 / **色相角**）
    for nm in ('Pro400H清风', 'Portra400薄荷'):
        t = targets.for_stock(nm)
        check('%s 的肤色靶齐三件（亮度/彩度/色相角）' % nm,
              t.get('skin_l') is not None and t.get('skin_c') is not None
              and t.get('skin_hue') is not None,
              '亮度 %+.1f · 彩度 %.2f · 色相角 %.1f°'
              % (t['skin_l'], t['skin_c'], t['skin_hue']))

    # ② 关键：**没有脸 / 空窗口**时不许写出 nan（空 median 会算出 nan 污染整张）
    for tag, img in (('纯灰图（检不出脸）', np.full((64, 64, 3), 0.5)),
                     ('纯色块', np.zeros((48, 48, 3)))):
        o, gi = grade.apply(np.asarray(img, np.float64), C)
        check('★ 没有脸的图：输出无 nan / Inf（空窗口不许污染整张）[%s]' % tag,
              bool(np.all(np.isfinite(o))),
              '补 亮度%s 色相%s' % (gi.get('skin_dL'), gi.get('skin_dH')),
              '空窗口时 np.median([]) = nan ⇒ 整张变 nan。必须先判 `_sel.any()`')

    # ③ 报告里带上"用的哪种掩膜"（自查不用猜）
    #   ★★★ 09-26 这里**新增 'seg'**：`skin_mask` 原来只有 'face'/'hue' 两个值，
    #     而 'face' 在**检测器根本没认出脸**的时候也会出现（只看分割的 face_skin 非空就动手）
    #     ⇒ 报告会骗人。现在四个取值互斥：face / seg / hue / none，见 `grade.apply` 的注释。
    o, gi = grade.apply(np.asarray(_gray_img(seed=61), np.float64), C)
    check('报告里带 skin_mask（face / seg / hue / none 四态，不许说谎）',
          gi.get('skin_mask') in ('face', 'seg', 'hue', 'none'),
          str(gi.get('skin_mask')),
          '取值不在四态里 ⇒ 后来人改了这个字段却忘了它是有语义的')
    check('★ 报告里还带「检测器到底认没认出脸」（`skin_face_seen`）',
          'skin_face_seen' in gi and gi.get('skin_face_seen') is False,
          'face_seen=%s model_ok=%s' % (gi.get('skin_face_seen'), gi.get('skin_model_ok')),
          '灰图检不出脸 ⇒ 必须是 False。原来这条信息根本不在报告里，'
          '所以"没检到脸却写着 face"藏了很久')
    check('★ 有脸才写 face（没认出脸时不许写 face）',
          (gi.get('skin_mask') != 'face') or bool(gi.get('skin_face_seen')),
          'skin_mask=%s face_seen=%s' % (gi.get('skin_mask'), gi.get('skin_face_seen')),
          'skin_mask=face 但检测器没认出脸 ⇒ 又回到"只看分割就动手"的老毛病')


def t_targets():
    """靶按预设分组（`targets.py`）：换预设必须换靶。"""
    from . import targets

    check('★ 滨田 / 増田 两条预设各自有专属靶（不是落回 _default）',
          targets.for_stock('Pro400H清风')['own'] and targets.for_stock('Portra400薄荷')['own'])
    check('★ 没有专属靶的预设落回 _default（不报错、有靶）',
          targets.for_stock('C200过曝')['own'] is False
          and targets.for_stock('C200过曝')['black_shape'] is not None)
    b, z = targets.for_stock('Pro400H清风'), targets.for_stock('Portra400薄荷')
    check('★★ 两条预设的靶**真的不一样**（一个文件一个靶，不是复制）',
          abs(b['black_shape'] - z['black_shape']) > 1.0
          and abs(b['sh_abs'][0] - z['sh_abs'][0]) > 1.0,
          '黑位 %.1f vs %.1f · 暗Δa %+.2f vs %+.2f'
          % (b['black_shape'], z['black_shape'], b['sh_abs'][0], z['sh_abs'][0]))
    check('★★ tone / grade 都接受 stock 参数（靶能传下去）',
          'stock' in __import__('inspect').signature(tone.settle_finished).parameters
          and 'stock' in __import__('inspect').signature(__import__('svFilm.grade', fromlist=['x']).apply).parameters)



def t_tech():
    r"""技术层体检（`tone.health`）：贴边 / 堆积 / 挤压系数。

    ⚠⚠ 横轴是**显示域 Y（0~255）**，不是 Lab 的 L\* —— 我自己混过一次：
      在直方图上看到 Blacks 处的"峰"以为是裁切，其实那在 Y≈20~40。
    """
    from . import tone

    # ① 正常片：三样都 0、挤压系数 1（不干预）
    ok = np.random.RandomState(0).rand(64, 64, 3) * 0.7 + 0.15
    h = tone.health(ok)
    check('正常片 ⇒ 贴边/堆积都是 0、挤压系数 1.0（不干预）',
          h['clip_lo'] == 0 and h['clip_hi'] == 0 and h['pile_lo'] < 0.01
          and h['squeeze'] == (1.0, 1.0),
          '贴 %.4f/%.4f 堆 %.4f/%.4f' % (h['clip_lo'], h['clip_hi'], h['pile_lo'], h['pile_hi']))

    # ② 暗部全黑 ⇒ 暗侧挤压系数掉到 0（**停止继续压黑位**）
    dark = ok.copy(); dark[:20] = 0.0
    h = tone.health(dark)
    check('★ 暗部全黑 31% ⇒ 暗侧挤压系数掉到 0（停止再压黑位）',
          h['clip_lo'] > 0.3 and h['squeeze'][0] == 0.0,
          '贴黑 %.1f%%  系数 %.2f' % (h['clip_lo'] * 100, h['squeeze'][0]))

    # ③ 高光全白 ⇒ 亮侧挤压系数掉到 0
    bright = ok.copy(); bright[:13] = 1.0
    h = tone.health(bright)
    check('★ 高光全白 20% ⇒ 亮侧挤压系数掉到 0（停止再压亮部）',
          h['clip_hi'] > 0.15 and h['squeeze'][1] == 0.0,
          '贴白 %.1f%%  系数 %.2f' % (h['clip_hi'] * 100, h['squeeze'][1]))

    # ④ 体检真的**接到了**影调层（挤到 0 ⇒ 这一侧一个像素都不动）
    d1 = _gray_img(seed=41)
    r1 = tone.settle_finished(d1, '中性调', C)[1]
    d2 = d1.copy(); d2[: d2.shape[0] // 3] = 0.0          # 上面 1/3 涂黑
    r2 = tone.settle_finished(d2, '中性调', C)[1]
    check('★★ 挤到 0 ⇒ 黑位这一侧真的不动了（护栏真的接上了）',
          r2['clip_lo'] > 0.2 and abs(r2['bl_applied']) < 1e-9,
          '贴黑 %.1f%%  实际压黑位 %.3f（正常片是 %.3f）'
          % (r2['clip_lo'] * 100, r2['bl_applied'], r1['bl_applied']),
          '体检没接到影调层 ⇒ 暗部已经糊住的片子会被继续压')


def t_grade():
    """二次调色（`grade.py`）：关掉必须逐位恒等；开着必须按量到的方向动。"""
    from . import grade

    disp = _gray_img(seed=23)
    # ① 关掉 ⇒ 逐位不变（不能"说关还偷偷动一点"）
    C.GRADE_ENABLE = False
    try:
        off, info = grade.apply(disp, C)
    finally:
        C.GRADE_ENABLE = True
    check('★ 关掉二次调色 ⇒ 逐位不动（不许"说关还偷偷动"）',
          float(np.max(np.abs(off - disp))) < 1e-12,
          '最大差 %.2e' % float(np.max(np.abs(off - disp))))

    on, info = grade.apply(disp, C)
    check('开着 ⇒ 画面真的变了', float(np.max(np.abs(on - disp))) > 0.005,
          '最大差 %.4f' % float(np.max(np.abs(on - disp))))
    check('输出没有 NaN / Inf 且在 [0,1]',
          bool(np.all(np.isfinite(on))) and float(on.min()) >= 0.0 and float(on.max()) <= 1.0)

    # ② 极端图不崩
    for tag, img in (('全黑', np.zeros((8, 8, 3))), ('全白', np.ones((8, 8, 3))),
                     ('全灰', np.full((8, 8, 3), 0.5))):
        try:
            o, _ = grade.apply(img, C)
            ok = bool(np.all(np.isfinite(o)))
        except Exception:                                          # noqa: BLE001
            ok = False
        check('极端图不崩：%s' % tag, ok, '',
              '分位全相等时除零 —— 彩度归一那段必须有 _EPS 兜着')

    # ③ 暗部真的往鹿井的方向动了（a* 更绿、b* 更黄）
    def _split(d):
        lab = color.to_lab(np.ascontiguousarray(d))
        L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
        am, bm = np.median(a), np.median(b)
        m = L <= np.percentile(L, 25.0)
        return float(a[m].mean() - am), float(b[m].mean() - bm)
    a0, b0 = _split(disp)
    a1, b1 = _split(on)
    # ★ 分色现在是**逐图往靶收**（靶按预设取，见 `targets.py`）。
    #   判据不是"往哪个方向"，而是**离靶是不是更近了** —— 这条跟靶换谁都不冲突。
    from . import targets as _T
    _t = _T.for_stock(None)
    _sh_a, _sh_b = float(_t['sh_abs'][0]), float(_t['sh_abs'][1])
    _d0 = abs(a0 - _sh_a) + abs(b0 - _sh_b)
    _d1 = abs(a1 - _sh_a) + abs(b1 - _sh_b)
    check('★★ 暗部分色**离靶更近了**（逐图往靶收；靶 %+.2f/%+.2f）' % (_sh_a, _sh_b),
          _d1 < _d0 - 1e-6,
          '离靶 %.2f → %.2f   （当前 a* %+.2f→%+.2f  b* %+.2f→%+.2f）'
          % (_d0, _d1, a0, a1, b0, b1),
          '越来越远 ⇒ 补的符号反了，或者 target 取错了')
    # ★★★ 彩度守恒（09-24 这个 bug 的回归护栏）：`GRADE_SAT = 1.0` 时
    #   分色混色**不许改变整张的彩度中位** —— 它只该"重新分配"。
    #   ⚠ 原来归一系数取的是**彩色像素**的中位、却乘到**所有**像素上 ⇒ 灰像素被多乘一次
    #     ⇒ 整张彩度虚涨 46%、画面发飘（SV 一眼看出脸崩了）。
    #   ⚠ 不能用灰图测（灰图 C=0，比值没意义）⇒ 造一张**有颜色**的确定性测试图
    _rng = np.random.RandomState(51)
    _c = np.stack([_rng.rand(96, 96) * 0.55 + 0.22 for _ in range(3)], -1)
    _c[..., 1] = np.clip(_c[..., 1] * 1.05, 0, 1)      # 偏彩（不是灰）
    _lab0 = color.to_lab(_c)
    _c1, _ = grade.apply(_c, C)
    _lab1 = color.to_lab(np.ascontiguousarray(_c1))
    _m0 = float(np.median(np.sqrt(_lab0[..., 1] ** 2 + _lab0[..., 2] ** 2)))
    _m1 = float(np.median(np.sqrt(_lab1[..., 1] ** 2 + _lab1[..., 2] ** 2)))
    check('★★★ 分色混色**不许改整张彩度中位**（GRADE_SAT=1 ⇒ 只重新分配）',
          abs(_m1 / max(_m0, 1e-6) - 1.0) < 0.08,
          '整张彩度中位 %.2f → %.2f（%+.1f%%）' % (_m0, _m1, 100 * (_m1 / max(_m0, 1e-6) - 1)),
          '涨太多 ⇒ 归一的系数算错了基准（别拿彩色子集的中位去乘所有像素）')

    check('报告里带着"动了多少"（能自查，不用读图）',
          bool(info.get('applied')) and 'd_sh' in info and 'c_gain' in info)

    # ④ 灰像素不被动（加饱和不许把中性轴一起推偏 —— digitalFilm 那条教训）
    # ⚠ 必须三通道**相等**才是真灰（`dstack` 三个不同常数 = 一个浅蓝，不是灰）
    cmax = 0.0
    for gv in (0.2, 0.35, 0.5, 0.65, 0.8):
        g, _ = grade.apply(np.full((64, 64, 3), float(gv), np.float64), C)
        lab = color.to_lab(np.ascontiguousarray(g))
        cmax = max(cmax, float(np.max(np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2))))
    check('★ 中性灰不被推彩度（彩度闸兜着；留 3 的余量给分级那一点点）',
          cmax < 3.0, '五档灰最大彩度 %.2f' % cmax,
          '中性轴被推偏 ⇒ 灰像素没被彩度闸挡掉（加饱和把中性轴一起推偏是 digitalFilm 的老毛病）')


def t_config_keys():
    """★★ 09-26：**可调参数只在 `config.py`** —— 这四个键以前是"假旋钮"。

    症状：代码里写的是 `getattr(cfg, 'X', 字面默认)`，而 `config.py` 里**没有 X 这个键**
    ⇒ 永远取那个字面默认，**改源码里那个常量毫无反应**。
    （`TARGET_BLACK_FLOOR_L` / `TARGET_HI_FLOOR_L` 当时各写了两份 —— 一份模块常量、一份
     getattr 默认值，函数读的恰好是**另一份**。）
    """
    from . import tone

    for k in ('TARGET_BLACK_FLOOR_L', 'TARGET_HI_FLOOR_L', 'SPEK_PE_SHIFT', 'TONE_REL'):
        check('config.%s 这个键真的在（不是 getattr 的裸默认）' % k,
              hasattr(C, k), '当前 %r' % getattr(C, k, None),
              '缺它 ⇒ 代码里 `getattr(cfg, …)` 永远取那个字面默认，'
              '改源码里同名常量**不会有任何反应**')
    check('tone 里的兜底常量与 config 同值（两边不许各说各的）',
          abs(tone.TARGET_BLACK_FLOOR_L - C.TARGET_BLACK_FLOOR_L) < 1e-9
          and abs(tone.TARGET_HI_FLOOR_L - C.TARGET_HI_FLOOR_L) < 1e-9,
          'tone %.1f/%.1f  config %.1f/%.1f' % (tone.TARGET_BLACK_FLOOR_L,
                                                tone.TARGET_HI_FLOOR_L,
                                                C.TARGET_BLACK_FLOOR_L, C.TARGET_HI_FLOOR_L))

    # ★★ 光"键在"不够 —— 必须**真的读它**（键在但没人读 = 换了个人继续摆着看）
    img = _gray_img(seed=71)
    _f0 = C.TARGET_BLACK_FLOOR_L
    try:
        C.TARGET_BLACK_FLOOR_L = 4.0
        a = float(tone.settle_finished(img, '中性调', C)[1]['bl_applied'])
        C.TARGET_BLACK_FLOOR_L = 60.0          # 明显绑住（这张图的 L5 远低于 60）
        b = float(tone.settle_finished(img, '中性调', C)[1]['bl_applied'])
    finally:
        C.TARGET_BLACK_FLOOR_L = _f0
    check('★★ 改 config.TARGET_BLACK_FLOOR_L **真的**改变压黑位的量（键在、且被读到）',
          abs(a - b) > 0.5, 'floor 4 ⇒ 压 %.2f ；floor 60 ⇒ 压 %.2f' % (a, b),
          '改了没反应 ⇒ 这个键还是"摆着看的"')

    _r0 = C.TONE_REL
    try:
        C.TONE_REL = {'中性调': dict(ev_down=0.0, hi_down=0.0, bl_down=0.0, desc='自检')}
        z = float(tone.settle_finished(img, '中性调', C)[1]['bl_applied'])
    finally:
        C.TONE_REL = _r0
    check('★ 改 config.TONE_REL 能给整套力度换一份（bl_down=0 ⇒ 黑位一个像素不动）',
          abs(z) < 1e-9, '压黑位 %.3f' % z,
          'TONE_REL 没被读到 ⇒ 三套力度只能改源码')


def t_mask_contract():
    """★★★ 09-26 人脸掩膜的三条契约（都是拿真图实测出来的坑）。

    ① 掩膜必须算在**解码后**那张图上 —— 链尾（胶片出图后）发白 ⇒ 分割认不出脸。
       实测同一批 12 张：解码后检出 12/12、引擎出图后只有 11/12。
    ② 一次出图**只算一遍** —— `region` 和 L4 原来各算一遍，同一份像素白付两次（273 ms/次）。
    ③ 报告**不许说谎** —— 原来只写 `'face'`/`'hue'`，而检测器没认出脸时也会写 `'face'`。
    ④ **并发安全** —— 常驻服务是 `ThreadingHTTPServer`，而 YuNet 检测器不是线程安全的：
       实测共享一个实例、8 线程 × 40 次，**崩 34 次**（`cv2.error ... net_impl2.cpp:1323`）。
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import face as _face, grade

    calls = []
    _orig = _face.parse

    def _spy(d):
        a = np.asarray(d)
        calls.append((tuple(a.shape), float(np.median(a))))
        return _orig(d)

    _face.parse = _spy
    try:
        # ① 传进来的 `parsed` 必须被**复用**（不许再调一遍分割）
        _pz = _orig(_gray_img(seed=3, h=24, w=24))
        calls.clear()
        _o, gi = grade.apply(np.asarray(_gray_img(seed=3, h=24, w=24), np.float64), C,
                             stock=_PRESET, parsed=_pz)
        check('★ 传了 parsed ⇒ grade 里**不再**调 face.parse（去掉重复的那一次）',
              len(calls) == 0, '调了 %d 次' % len(calls),
              '还在调 ⇒ region 与 L4 各算一遍，白付一次分割（实测 273 ms/次）')
        check('报告里标明掩膜来自外部（skin_mask_src = given）',
              gi.get('skin_mask_src') == 'given', str(gi.get('skin_mask_src')))

        # ② 跑整条链：只算一次，而且喂的是**解码后**那张图
        s = _mk_sample(_gray_img(seed=77))
        calls.clear()
        r = pipeline.run_from(s, stock=_PRESET, style='中性调')
        got = list(calls)
        check('★★ 整条链只算**一次**人脸掩膜（修复前是两次）',
              len(got) == 1, '算了 %d 次' % len(got),
              '>1 ⇒ 还在重复算；0 ⇒ 这道工序整个没跑，掩膜会退回色相窗')
        check('★★★ 掩膜喂的是**解码后**那张图，不是胶片引擎的成片',
              bool(got) and abs(got[0][1] - float(np.median(s.disp))) < 1e-9,
              '喂进去那张的中位 %.4f ；解码后那张 %.4f' % (
                  (got[0][1] if got else float('nan')), float(np.median(s.disp))),
              '喂成片 ⇒ 链尾发白、分割认不出脸（老路专门避开这件事，'
              '新路 09-26 之前又撞上了）')
        _gi2 = ((r.report.get('tone') or {}).get('grade') or {})
        check('★ 整条链的报告里 skin_mask_src = given（掩膜走的是解码后那一份）',
              _gi2.get('skin_mask_src') == 'given', str(_gi2.get('skin_mask_src')))

        # ④ 并发安全：4 线程同时解析 —— 不崩、结果与单线程一致
        _imgs = [np.random.RandomState(s).rand(240, 320, 3) for s in range(4)]
        _base = [float(_orig(i)['masks']['person'].sum()) for i in _imgs]
        _err = []

        def _job(k):
            try:
                return float(_face.parse(_imgs[k % 4])['masks']['person'].sum())
            except Exception as e:                                  # noqa: BLE001
                _err.append('%s: %s' % (type(e).__name__, str(e)[:50]))
                return None
        with ThreadPoolExecutor(max_workers=4) as _ex:
            _got = list(_ex.map(_job, list(range(4)) * 3))
        _bad = sum(1 for i, g in enumerate(_got)
                   if g is None or abs(g - _base[i % 4]) >= 1e-6)
        check('★★★ 4 线程并发解析：不崩，且与单线程结果一致（YuNet 每线程一份）',
              not _err and _bad == 0,
              '异常 %d 次 ；结果不一致 %d 次' % (len(_err), _bad),
              '共享一个 YuNet 实例时实测 8 线程 × 40 次**崩 34 次**'
              '（`cv2.error ... net_impl2.cpp:1323`，OpenCV DNN 没有线程安全承诺）')
        check('★ 并发确实走到了检测器那一层（不是"图太小没触发"）',
              bool(getattr(_face._DET_TLS, 'det', None)),
              '每线程检测器尺寸键：%s' % sorted(getattr(_face._DET_TLS, 'det', {}) or {}))
    finally:
        _face.parse = _orig


def t_scene():
    """场景判据（`scene.py`）：六轴都到得了 · key 带版本 · 源头过曝只在线性域判 ·
    `targets._scene` 覆盖**只加不减**、不给场景就一个字段不动。"""
    from . import scene, targets

    # ① 六轴都在，key 带版本号，且**同一张图判两次一样**
    sc = scene.classify(_gray_img(seed=91), None)
    check('场景判据给出六根轴 + 版本化的 key（改了判据就要作废缓存）',
          all(k in sc for k in scene.AXES) and sc['key'].startswith('v%d|' % scene.VERSION),
          'key = %s' % sc['key'])
    check('判据稳定：同一张图判两次 key 完全一样',
          scene.classify(_gray_img(seed=91), None)['key'] == sc['key'])

    # ② 每根轴的每一档都要**到得了**（落不到的分档 = 死的专家）
    #   ⚠⚠ 给的是**显示域**的值，而分档线在 **Lab L\*** 上 —— 中间隔着 sRGB 解码 + 立方根。
    #     "看起来中等"的 0.35 显示域其实是 L*≈38（落在「亮」档），在这儿红过两次。
    #     实测对应：0.087→L*7.3 / 0.19→L*20.1 / 0.95→L*95.6。
    dark = np.full((32, 32, 3), 0.087)
    mid = np.full((32, 32, 3), 0.19)
    bright = np.full((32, 32, 3), 0.95)
    flat = np.full((32, 32, 3), 0.35)
    wide = np.zeros((32, 32, 3)); wide[16:] = 1.0
    e = {scene.classify(x, None)['exp'] for x in (dark, mid, bright)}
    s = {scene.classify(x, None)['span'] for x in (flat, wide)}
    check('「曝光」三档都到得了（暗/正常/亮）', len(e) == 3, str(sorted(e)))
    check('「光比」至少两档到得了（平/大）', len(s) >= 2, str(sorted(s)))

    # ③ ★★ 源头过曝**只在线性域**判：显示域一样、线性不一样 ⇒ 结论必须不同
    #   ⚠ 判据是「通道最大值 ≥ 白点」，**不是**"三通道同时贴顶"（那样写永远不会响，
    #     因为显示域的"白"只是 sRGB 把 1.0 以上压到 255 的假象，见 `config` 里那段）。
    a = scene.classify(flat, None, C, lin=np.full((32, 32, 3), 0.5))['overwhite']
    b = scene.classify(flat, None, C, lin=np.full((32, 32, 3), 1.2))['overwhite']
    check('★★ 源头过曝在线性域判得出来（显示域一样、线性不一样 ⇒ 结论不同）',
          a is False and b is True, '线性 0.5 → %s ；线性 1.2 → %s' % (a, b),
          '只看显示域的话这两张"一样" ⇒ 这一轴就废了（成片那边早被重渲染压过了）')
    check('不给线性图 ⇒ overwhite = None（不硬猜）',
          scene.classify(flat, None)['overwhite'] is None)

    # ④ 场景覆盖：默认**一个字段都不动**；命中才盖、且只盖命中的那些
    base = targets.for_stock(_PRESET, None)
    _sc = {'key': 'v1|x', 'exp': '暗', 'span': '平', 'back': False,
           'shot': '近景', 'face': 'face', 'overwhite': False}
    same = targets.for_stock(_PRESET, _sc)
    check('★★ 没有 `_scene` 段时，给不给场景一个字段都不变（默认逐位不变）',
          same['skin_L_abs'] == base['skin_L_abs'] and same['_scene_hits'] == []
          and same.get('span') == base.get('span'),
          'hits=%s' % same['_scene_hits'])

    d = targets.load()
    _save = d.get('_scene')
    try:
        d['_scene'] = {'overwhite=过曝': {'skin_L_abs': 1.23}, 'exp=*': {'skin_C_abs': 9.9}}
        _hit = dict(_sc); _hit['overwhite'] = True
        tt = targets.for_stock(_PRESET, _hit)
        check('★★ 命中场景覆盖时字段真的盖上，且只盖命中的那些',
              tt['skin_L_abs'] == 1.23 and tt['skin_C_abs'] == 9.9
              and tt['_scene_hits'] == ['exp=*', 'overwhite=过曝'],
              'hits=%s' % tt['_scene_hits'])
        tt2 = targets.for_stock(_PRESET, _sc)
        check('★ 没命中的档不盖（overwhite=False ⇒ 不掉进 overwhite=过曝）',
              tt2['skin_L_abs'] == base['skin_L_abs'] and tt2.get('skin_C_abs') == 9.9,
              '脸靶 %s（应还是 %s）' % (tt2['skin_L_abs'], base['skin_L_abs']))
    finally:
        if _save is None:
            d.pop('_scene', None)
        else:
            d['_scene'] = _save


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
