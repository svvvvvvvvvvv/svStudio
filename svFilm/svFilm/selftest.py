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
# ★ 当前阶段可以只做胶片引擎（`TONE_ENABLE=False` / `GRADE_ENABLE=False`）——
#   这时 `report['tone']` 里不再有 `bl_down` / `L5_out` 这些键，断言必须跟着开关走，
#   否则会把"关掉这一层"误报成"功能坏了"。
_TONE_ON = bool(getattr(C, 'TONE_ENABLE', True))
_GRADE_ON = bool(getattr(C, 'GRADE_ENABLE', True))

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


def _pure_engine_same(res, sample, style=None):
    """关掉后期层时，`pipeline` 的出图应当**就是** `presets.render` 的输出。

    ⚠ 引擎的颗粒是随机的 ⇒ 判据只能是"差值在引擎自身的噪声量级内"，
      **不能**要求逐位相等。参照量 = 同一输入连跑两次纯引擎的差值。
    ★★ 09-29：参照物必须带上**生产那份引擎覆盖**（`_scene_engine`，例：全局那条
      `density_curves_morph` 把跨度 76.2 拉到 81.87）。不带 ⇒ 拿"没有覆盖的引擎"去比
      "有覆盖的引擎"，差的 0.032 **全是覆盖带来的**，与"影调 / 颜色两层"无关
      —— 这条老账就是这么红的（`_scene_engine` 是 09-28 才加的，这条检查没跟着改）。
    ★★ 同理：**脸增益也是"引擎这一步"的一部分**（默认开）⇒ 它动了就要带它当参照物。
    """
    lin = np.clip(sample.lin, 0.0, None)
    a = np.asarray(res.disp)
    from . import targets as _TS
    _sc = (res.report.get('tone') or {}).get('scene')
    ov = _TS.scene_engine(_sc, stock=res.report['stock'], cfg=C) or None
    stock = res.report['stock']
    fg = res.report.get('face_gain') or {}
    # ★★ 09-29（C2）：判"这张到底动没动"要看 `any_applied` —— 身体那一段可以**单独**动
    #   （脸本来就在靶上 ⇒ 脸 0 轮，身体照样被拉）。只看 `applied` 会把它当成"没动"。
    _moved = bool(fg.get('applied') or fg.get('any_applied'))
    ft = _TS.face_lab_target(stock) if _moved else None
    if ft:
        from . import face as _face
        pz = _face.parse(np.clip(sample.disp, 0.0, 1.0))
        # ★★ 09-29（C2）：**身体闭环也是"引擎这一步"的一部分** ⇒ 参照物必须带上它的靶，
        #   否则拿"只做脸那段"的图去比"脸+身体两段"的图，差的就全是第二段（这条检查会假红）。
        _gt = _TS.skin_gap_target(stock, _sc)
        _kw = dict(pz=pz, target_L=ft[0], target_a=ft[1], target_b=ft[2], overrides=ov,
                   gap_target=_gt)
        b = np.asarray(presets.render_with_face(lin, stock, C, **_kw)[0])
        c = np.asarray(presets.render_with_face(lin, stock, C, **_kw)[0])
    else:
        b = np.asarray(presets.render(lin, stock, C, overrides=ov))
        c = np.asarray(presets.render(lin, stock, C, overrides=ov))
    noise = float(np.max(np.abs(b - c)))          # 引擎自身不可复现的那点
    diff = float(np.max(np.abs(a - b)))
    return diff <= max(noise * 2.5, 5e-3)


def _main():
    groups = [
        ('胶片风格：9 条预设', t_presets),
        ('胶片风格：换一条真的换画面', t_preset_differs),
        ('曝光风格：三条档', t_styles),
        ('曝光风格：真的把画面搬到靶', t_tone_hits),
        ('曝光风格：曲线单调 + 极端图不崩', t_tone_sane),
        ('和脸有关的机制：只许有一个（防死机制复活）', t_tone_bias),
        ('二次调色：分色 + 混色（L2/L3）', t_grade),
        ('直方图（LR 画法：亮度 + RGB 叠加 + 5 个区）', t_hist),
        ('技术层：贴边 / 堆积 / 挤压系数', t_tech),
        ('靶按预设分组', t_targets),
        ('肤色层：真脸掩膜 / 空窗口 / 三件事', t_skin),
        ('可调键：config 里真的接上了（防"假旋钮"）', t_config_keys),
        ('★★★★★ 去掉「认人/认脸」：一次都不调 / 没后门 / 不丢跨度', t_no_face_step),
        ('人脸掩膜：算在解码后 / 只算一次 / 报告不说谎', t_mask_contract),
        ('脸增益（**已停用**，代码保留）：只动脸 / 没脸不动 / ΔE00 是真尺子', t_facegain),
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

    # ★★ 09-29：**光晕幅度 = 该卷自己的物理值**（不是"10 条一个数"，更不许在过火档）。
    #   两处**独立**出处，互相印证：
    #     ① 公开 GUI（`spektrafilm_gui/widget_specs.py`）滑杆「Halation 幅度 %」tooltip：
    #        **红通道典型值：弱 AH 2-8，无 AH 8-25**；人像提示：
    #        **"R 通道 1.5 = 现代彩色负片的物理值；拍人别超 8，超了脸上高光泛红"**。
    #     ② 引擎 `_HALATION_PRESETS[(use, antihalation)]`：strong 0.015 / weak 0.08 / no 0.30。
    #   两边**逐档对上** ⇒ 期望值按**卷的 profile 标签**取（标签是引擎/胶卷给的外部真值）。
    #   ⚠ 我们原来的 40% 是「完全无抗晕层」上限(25) 的 1.6 倍、**人像上限(8) 的 5 倍**
    #     ⇒ 一开机就在过火档。
    #   ★ `halation_amount`（GUI 滑杆「Halation 强度」，1.0=物理默认）**不钉**：
    #     它是**用户风格旋钮**，`C200透明`(1.4) / `C200过曝`(1.2) / `Pro400H马卡龙`(1.2)
    #     正是用它做风格区分的（`C200透明` 的**唯一**区别就是它）。
    #     ⚠ 我 09-29 先试过把它一起归到 1.0 —— **结果 `C200透明` 与 `C200青蓝` 变成逐位相同**，
    #       `t_preset_differs` 当场红 ⇒ 撤回，只改幅度。**这条注释是给"以后想动它的人"的护栏。**
    #   ⇒ 用户拍板（09-29）：幅度**改成卷的物理值**。要改这个数，先把依据写进
    #     `效果debug\2026-09-29\调研与设计_光晕与白平衡.md`，再回来改这里。
    _PHYS = {'strong': (1.5, 0.5, 0.0), 'weak': (8.0, 2.0, 0.0), 'no': (30.0, 10.0, 1.5)}
    _bad, _seen = [], []
    for n in ns:
        _pp = presets.digested(n)
        _tag = str(getattr(_pp.film.info, 'antihalation', '') or '')
        _exp = _PHYS.get(_tag)
        _got = tuple(round(float(v) * 100.0, 4)
                     for v in _pp.film_render.halation.halation_strength)
        _amt = float(_pp.film_render.halation.halation_amount)
        _seen.append('%s %s=%.1f%%×%.1f' % (n, _tag, _got[0], _amt))
        if _exp is None or _got != _exp:
            _bad.append('%s[%s] 幅度%s（期望 %s）' % (n, _tag, _got, _exp))
    check('★★ 光晕幅度 = 该卷自己的物理值（按抗晕层标签取）', not _bad,
          '；'.join(_bad) if _bad else ' / '.join(_seen),
          '人像提示原话「拍人别超 8，超了脸上高光泛红」⇒ 过火档会让人像高光泛红；'
          '要改先回 `调研与设计_光晕与白平衡.md` 写清依据')
    # ★ 倍率**必须留在合理带内**（不是 1.0，但也不许回到过火档：GUI 人像提示 1.2~1.3 是上限）
    check('★ 光晕强度倍率留在带内（1.0~1.4，不回到过火档）',
          all(1.0 - 1e-9 <= float(presets.digested(n).film_render.halation.halation_amount) <= 1.4 + 1e-9
              for n in ns),
          ' / '.join('%.1f' % float(presets.digested(n).film_render.halation.halation_amount) for n in ns),
          'GUI 人像提示：「halation_amount 人像最容易翻车的一根：1.2~1.3 是上限；2 以上脸上高光泛红」')

    # ★★ 09-29：**「整张冷暖基准」的落点 = 印相端校色滤片**（负片→印相体系里"白平衡"本来就是它）。
    #   钉两件事：① 这个落点**真通电**（`_apply` 读进 params，不是又一组假旋钮）；
    #            ② 它**不是常量**（10 条不全同）—— 否则"逐预设的冷暖基准"就是句空话。
    #   ⚠ 同一支卷的几条**允许相同**（C200 三条都是 y=3/m=2）：那是同一底片的同一次印相配平。
    _wb, _wbb = [], []
    for n in ns:
        _q = presets.digested(n)
        _sim = presets.load_raw(n)['simulation']
        _wb.append((round(float(_q.enlarger.y_filter_shift), 3),
                    round(float(_q.enlarger.m_filter_shift), 3)))
        _wbb.append((round(float(_sim['print_y_filter_shift']), 3),
                     round(float(_sim['print_m_filter_shift']), 3)))
    check('★ 整张冷暖基准落在印相滤片上（Y/M **真进 params**，不是假旋钮）', _wb == _wbb,
          ' / '.join('%s y=%.1f m=%.1f' % (n[4:] if len(n) > 4 else n, w[0], w[1])
                     for n, w in zip(ns, _wb))[:160],
          'JSON 写了但 params 里不是那个数 ⇒ 又是一组"改了没反应"的假旋钮')
    check('★ 而且它**不是常量**（10 条的冷暖基准不全同）', len(set(_wb)) >= 2,
          '%d 种组合' % len(set(_wb)),
          '全同 ⇒ "逐预设的冷暖基准"是句空话；改冷暖请改 `simulation.print_y_filter_shift`'
          '（映射见 §`_note_load_raw`），别去动 `load_raw` 那三个死键')
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
    """★★★ 09-26：**「和脸有关的机制只许有一个」** —— 这条钉子防死机制复活。

    背景：原来有**两套**和脸有关的东西 —— 老路的脸锚点（`io.anchor_ev` / `io.finish_anchor`，
    量一次脸、把它提到 68）和新路的 L4 肤色层。前者只在 `TONE_AFTER_ENGINE=False` 时跑，
    而 config 里 `ANCHOR_ENABLE=True` / `ANCHOR_FACE_L=68.0` 还摆着 ⇒ 看起来"救暗脸有机制"，
    实际上**没有任何一环能提亮暗片的脸**（引擎给 47、L4 限幅 ±6 只能到 53，而大师是 67~70）
    —— 这就是 09-26「酱油脸」的根因。⇒ 已整体删除（见 `io.py` / `pipeline.py` 的说明）。
    """
    from . import io
    gone = [n for n in ('anchor_ev', 'finish_anchor', 'refocus', '_shoulder_inv')
            if hasattr(io, n)]
    check('★★★ 老的脸锚点**确实删掉了**（不许留死机制冒充"有机制"）', not gone,
          '还在: %s' % (gone or '无'),
          '又冒出来了 ⇒ 会有两套机制管同一件事（脸），行为互相打架且难查')
    check('★★ config 里不许再有 ANCHOR_* 这种"没人读的开关"',
          not [k for k in dir(C) if k.startswith('ANCHOR_')],
          '还剩: %s' % ([k for k in dir(C) if k.startswith('ANCHOR_')] or '无'))
    import inspect
    # ⚠ 必须**剥掉注释**再查：源码里那些"已删除"的说明文字本身就带着 anchor_ev / finish_anchor
    #   这两个词，不剥就会永远红（"查源码断言用剥掉注释的版本"—— 踩过一次了）。
    _src = '\n'.join(l.split('#')[0] for l in inspect.getsource(pipeline.run_from).splitlines())
    _hit = [n for n in ('anchor_ev', 'finish_anchor', 'ANCHOR_') if n in _src]
    check('★★ pipeline 的**代码里**不许再调 anchor（两套机制不许并存）',
          not _hit, '命中: %s' % (_hit or '无'),
          '又冒出来了 ⇒ 会有两套机制管同一件事（脸），行为互相打架且难查')


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

    # ★★ 09-29：下面这三条测的是**颜色层自己**（报告字段），所以显式把它打开
    #   （与 `t_grade` 显式设 `GRADE_SCOPE='all'` 同理）。当前阶段
    #   `config.GRADE_ENABLE=False` ⇒ 不打开的话 `grade.apply` 直接原样返回，
    #   报告里根本没有 `skin_mask` 这些键 ⇒ 这几条会"过期变红"。
    #   ⚠ 必须 try/finally 还原：这一组跑在 `t_mask_contract` / `t_contract` / `t_cache`
    #     **之前**，漏还原会让那三组里所有"关掉"分支错位。（09-29 的老账正是这么来的。）
    _ge0 = bool(getattr(C, 'GRADE_ENABLE', True))
    C.GRADE_ENABLE = True
    try:
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

        # ④ ★★★★ 09-29 新契约：**L4 的「量」只用脸**（与 `facegain` 同款拆法）
        #   + 「脸的绝对靶」唯一主人 = 脸增益。
        #   ★ 为什么必须 A/B：这条改的正是**旧 bug 的签名** ——
        #     `_w = max(脸×1.6, 身体×GRADE_SKIN_BODY_W)` 且 `BODY_W = 0.8 > 0.5`
        #     ⇒ 直方图里的 `_sel = _w > 0.5` 把**身体也圈进来了**
        #     ⇒ `_aL`（注释写"脸自己的绝对 L*"）实际是"脸 ∪ 身体"的中位
        #     ⇒ **身体比脸暗 ⇒ `_aL` 偏低 ⇒ `_dl` 偏大 ⇒ 脸被推得更高**（"脸太白"的方向）。
        #   造一张**脸亮、身体暗**的合成图，靶就设成脸自己的亮度 ⇒
        #     新行为 `_dl ≈ 0` ；旧行为 `_dl` 会被身体拉成正的大值。两条一起跑，差值就是证据。
        from . import color as _col
        _h, _w2 = 240, 320
        _lab = np.zeros((_h, _w2, 3), np.float64)
        _lab[..., 0] = 55.0                       # 背景
        _lab[40:120, 120:200, 0] = 60.0           # 脸（亮）
        # ★ 身体必须**比脸大得多**才复现得出那个 bug：羽化(σ≈面积开方/6)会把
        #   `皮肤×0.8` 的软区压薄，身体太小的话它掉到 0.5 以下、压根进不了旧 `_sel`，
        #   测试就变成"空转"（第一版就是这么假过的）。真照片里胳膊+脖子+手
        #   的面积常**大于**脸掩膜的核心区，所以"身体更大"才是**代表真实**的造法。
        _lab[150:230, 10:310, 0] = 40.0           # 身体（暗，且面积远大于脸）
        _img = np.clip(_col.from_lab(_lab), 0.0, 1.0)
        _fs = np.zeros((_h, _w2), np.float64)
        _fs[40:120, 120:200] = 1.0
        _sk = _fs.copy()
        _sk[150:230, 10:310] = 1.0
        _pzr2 = dict(face=dict(), masks=dict(face_skin=_fs, skin=_sk))
        _lab2 = _col.to_lab(np.ascontiguousarray(_img))
        _L2, _a2, _b2 = _lab2[..., 0], _lab2[..., 1], _lab2[..., 2]
        _tg2 = dict(skin_l=4.0, skin_c=1.0, skin_hue=54.8,
                    skin_L_abs=60.0, skin_C_abs=12.0)      # 靶 = 脸自己的亮度

        _bw0 = float(getattr(C, 'GRADE_SKIN_BODY_W', 0.8))
        _mf0 = bool(getattr(C, 'GRADE_SKIN_MEAS_FACE', True))
        _ow0 = getattr(C, 'SKIN_ABS_OWNER', 'facegain')
        _fg0 = bool(getattr(C, 'FACE_GAIN_ENABLE', False))
        try:
            C.GRADE_SKIN_MEAS_FACE = True
            C.SKIN_ABS_OWNER = 'grade'                 # 先把"主人"让给 L4，才测得到 `_dl`
            C.FACE_GAIN_ENABLE = False
            _i_new = grade.skin(_img, _L2, _a2, _b2, _tg2, C, _pzr2)[3]
            C.GRADE_SKIN_MEAS_FACE = False             # 退回旧行为（量 = 脸∪身体）
            _i_old = grade.skin(_img, _L2, _a2, _b2, _tg2, C, _pzr2)[3]
            C.GRADE_SKIN_MEAS_FACE = True
            C.GRADE_SKIN_BODY_W = 0.2                  # 动"作用"权重，看「量」跟不跟
            _i_bw = grade.skin(_img, _L2, _a2, _b2, _tg2, C, _pzr2)[3]

            check('★★ 「量」的掩膜标记 = face（`skin_meas_mask` 不许说谎）',
                  _i_new.get('skin_meas_mask') == 'face', str(_i_new.get('skin_meas_mask')))
            check('★★★★ 身体比脸暗时：**新行为**的 `_dl` ≈ 0（脸没被身体拖走）',
                  abs(float(_i_new['skin_dL'])) < 1.0,
                  '新 %.2f ｜ 旧 %.2f（靶 = 脸自己的亮度 60）'
                  % (_i_new['skin_dL'], _i_old['skin_dL']),
                  '旧行为把身体圈进 `_sel` ⇒ `_aL` 偏低 ⇒ `_dl` 被推成正的大值 ⇒'
                  ' **脸被推得更高（"脸太白"的来源）**')
            check('★★★★ 同图同靶：**旧行为**的 `_dl` 明显更大（差值就是那个 bug 的量级）',
                  float(_i_old['skin_dL']) - float(_i_new['skin_dL']) > 2.0,
                  'Δ = %+.2f L*' % (_i_old['skin_dL'] - _i_new['skin_dL']),
                  '两条一样 ⇒ 说明那条 bug 没被真正修掉（或测试图没造对）')
            check('★★ 改身体权重 ⇒ 只动"作用"，「量」一动不动（`_dl` 不变）',
                  abs(float(_i_bw['skin_dL']) - float(_i_new['skin_dL'])) < 1e-6,
                  'BODY_W 0.8→0.2：_dl %.4f → %.4f' % (_i_new['skin_dL'], _i_bw['skin_dL']))

            # ---- 主人裁定（`SKIN_ABS_OWNER`）----
            C.SKIN_ABS_OWNER = 'facegain'
            C.FACE_GAIN_ENABLE = True
            _i_o1 = grade.skin(_img, _L2, _a2, _b2, _tg2, C, _pzr2)[3]
            check('★★★ 裁定生效：`SKIN_ABS_OWNER="facegain"` ⇒ L4 **不写**脸的绝对靶',
                  _i_o1.get('skin_abs_owner') == 'facegain'
                  and abs(float(_i_o1['skin_dL'])) < 1e-9
                  and abs(float(_i_o1['skin_dC'])) < 1e-9
                  and abs(float(_i_o1['skin_dH'])) < 1e-9,
                  'owner=%s dL=%.3f dC=%.3f dH=%.3f'
                  % (_i_o1.get('skin_abs_owner'), _i_o1['skin_dL'], _i_o1['skin_dC'], _i_o1['skin_dH']),
                  '不归零 ⇒ 又变成"脸增益和 L4 两处写同一个量"（打架的根因）')
            C.SKIN_ABS_OWNER = 'grade'                 # 故意造冲突（脸增益还开着）
            _i_o2 = grade.skin(_img, _L2, _a2, _b2, _tg2, C, _pzr2)[3]
            check('★★★ 真冲突（owner=grade 且脸增益开着）⇒ 记进报告，不静默',
                  bool(_i_o2.get('skin_owner_conflict')) and _i_o2.get('skin_abs_owner') == 'facegain',
                  'conflict=%s owner=%s' % (_i_o2.get('skin_owner_conflict'), _i_o2.get('skin_abs_owner')),
                  '静默的话就是"改了没反应"第 4 类：两个开关都在写，谁赢看不出来')
        finally:
            C.GRADE_SKIN_BODY_W = _bw0
            C.GRADE_SKIN_MEAS_FACE = _mf0
            C.SKIN_ABS_OWNER = _ow0
            C.FACE_GAIN_ENABLE = _fg0
    finally:
        C.GRADE_ENABLE = _ge0


# ---------------------------------------------------------------------------
# 脸增益（09-29 转正）
# ---------------------------------------------------------------------------

def t_facegain():
    """★★ 09-29「脸增益」**转正**（`config.FACE_GAIN_ENABLE = True`）。

    它改的是**每张的出图** ⇒ 静默失效 / 静默乱动都没人看得出来 ⇒ 这组钉四件事：
      ① 开关真的在 `config` 里、且默认开；
      ② 闸门卡在**最终掩膜**（脸 ∪ 身体皮肤）的像素数上 —— 真脸 0 px 也**照样动**
         （`DSCF1629` 就是）；掩膜小到量不准才不动。
         ⚠ 残留风险（未解决，如实记）：画面里有**大块皮肤但没脸**时仍会被当脸提亮。
      ③ 有靶 ⇒ 真的把脸搬到靶，而且**只动脸**（掩膜外不动）；
      ④ 没有靶 ⇒ 走普通 `render`（不启用，逐位同旧行为）。
    """
    from . import facegain, targets

    check('config.FACE_GAIN_ENABLE 这个键真的在', hasattr(C, 'FACE_GAIN_ENABLE'),
          '当前 %r' % getattr(C, 'FACE_GAIN_ENABLE', None),
          '缺它 ⇒ 代码里 `getattr(cfg, …)` 永远取那个字面默认')
    # ★★★★★ 09-29 SV 裁定：**去掉「认人/认脸」这一步** ⇒ 这一格的判据**反过来**了
    #   （原来是"转正了、默认开"；现在要钉的是"已停用、默认关、**代码还在**"）。
    check('★★★★★ 「认人/认脸」总闸 `FACE_STEP_ENABLE` 在、且默认**关**',
          hasattr(C, 'FACE_STEP_ENABLE') and not bool(getattr(C, 'FACE_STEP_ENABLE', True)),
          'FACE_STEP_ENABLE=%r' % getattr(C, 'FACE_STEP_ENABLE', '★ 缺键'),
          '缺它 / 开着 ⇒ 每张白付 0.8~7 秒（mediapipe / birefnet）+ 多出脸与身体的动作')
    check('★★★★ 脸增益**已停用**（默认关；代码保留、不删）',
          not bool(getattr(C, 'FACE_GAIN_ENABLE', False)),
          'SV 原话：「去掉认人认脸的相关步骤 肤色层和这个肤色增益 跑的太慢了也没有达到我想要的效果」')
    check('★★★★ 身体闭环**已停用**（默认关；代码保留、不删）',
          not bool(getattr(C, 'SKIN_GAP_ENABLE', False)),
          '随总闸一起停 —— 它是挂在脸增益第二段上的')
    check('★★ 两个"摆着看的"旧键已清干净（`FACE_GAIN_MIN_PX` / `FACE_GAIN_SKIP_BODY_ONLY`）',
          not hasattr(C, 'FACE_GAIN_MIN_PX') and not hasattr(C, 'FACE_GAIN_SKIP_BODY_ONLY'),
          '掩膜下限现在叫 FACE_GAIN_MIN_MASK_PX=%r' % getattr(C, 'FACE_GAIN_MIN_MASK_PX', None),
          '留着它们 ⇒ 改了没反应（已知第 4 类"拧了没反应"）')

    # ★★ 09-29 SV 拍板：**身体皮肤权重 0.5 → 0.8**（原话"身体皮肤×0.5 有点少"）。
    #   这一个权重**同时管两处**：① `facegain._mask()` 的掩膜 = 脸 ∪ 身体皮肤×它；
    #   ② `grade.py` 的 L4 肤色层，修正量也乘它。⇒ 改它 = 一次改两处，别只改一边。
    _bw = float(getattr(C, 'GRADE_SKIN_BODY_W', -1.0))
    check('★★ 身体皮肤权重 = 0.8（SV 09-29 拍板，0.5 少了）',
          abs(_bw - 0.8) < 1e-9,
          'GRADE_SKIN_BODY_W=%r ｜ facegain.BODY_W=%r'
          % (_bw, float(getattr(facegain, 'BODY_W', -1.0))),
          '身体皮肤只做到半路 ⇒ 脖子/手臂跟脸差一截 ⇒ SV 报过的"突兀"；'
          '本项影响脸增益掩膜 + L4 肤色层两处')
    check('★ 兜底值与真值一致（`facegain` 不许自己留一份旧值）',
          abs(float(getattr(facegain, 'BODY_W', -1.0)) - _bw) < 1e-9,
          'facegain.BODY_W=%r vs config=%r' % (float(getattr(facegain, 'BODY_W', -1.0)), _bw),
          '两份不一样 ⇒ 以后只改 config 会"改了一半"（脸 0.8、身体还是旧值）')

    # ---- ② 掩膜契约：闸门卡在「真脸」，不是「最终掩膜」 ----
    h, w = 180, 240
    z = np.zeros((h, w), np.float64)
    sk = z.copy()
    sk[h // 2:, :] = 1.0                       # 下半张全是"身体皮肤"
    fs = z.copy()
    fs[60:110, 90:150] = 1.0                   # 一小块真脸（3000 px）
    pz = {'masks': {'face_skin': fs, 'skin': sk}}

    check('★★ 只有身体皮肤、真脸 0 px ⇒ **照样动**（不许被筛掉）',
          facegain._mask({'masks': {'face_skin': z.copy(), 'skin': sk}}, (h, w, 3), C) is not None,
          '真脸 0 但并集 %d px（身体权重 %s）' % (int((sk * float(getattr(C, 'GRADE_SKIN_BODY_W', 0.5)) > 0.05).sum()), getattr(C, 'GRADE_SKIN_BODY_W', '?')),
          'DSCF1629 就是这样一张（掩膜全靠身体皮肤撑）而它是 09-29 实测**收住**的 —— '
          '拿"真脸为 0"当闸门会把好案例一起筛掉（09-29 我差点这么干）')
    _tz = z.copy()
    _tz[80:100, 110:130] = 1.0                 # 400 px 的掩膜：太小，量不准
    check('★ 掩膜小到量不准（< FACE_GAIN_MIN_MASK_PX）⇒ 不动',
          facegain._mask({'masks': {'face_skin': _tz}}, (h, w, 3), C) is None,
          '下限 %r px' % getattr(C, 'FACE_GAIN_MIN_MASK_PX', None))
    m = facegain._mask(pz, (h, w, 3), C)
    # ★★ 09-29：身体权重抬到 0.8 后，"峰值归一"的天平**会往身体倒**（本用例身体 21600 px、
    #   脸只有 3000 px）⇒ 脸处从 0.947 掉到 0.847。**但脸拿到的修正量没少** ——
    #   `GRADE_SKIN_W_REF=0.70` 会把权重归一（`min(w / 0.70, 1)`）⇒ 0.847/0.70 > 1 ⇒ 仍顶满。
    #   ⇒ 判据卡**这个契约**（脸处 ≥ W_REF），不卡"脸处 > 0.9"（那只是替旧权重留影）。
    check('★ 真脸够大 ⇒ 掩膜出来、并上身体皮肤，且**脸拿满修正**（≥ `GRADE_SKIN_W_REF`）',
          m is not None and float(m[85, 120]) >= float(C.GRADE_SKIN_W_REF)
          and float(m[h - 3, 3]) > 0.2,
          ('脸处 %.2f（W_REF %.2f ⇒ 修正量顶满）· 身体处 %.2f'
           % (float(m[85, 120]), float(C.GRADE_SKIN_W_REF), float(m[h - 3, 3])))
          if m is not None else '掩膜没出来',
          '身体那半张没进掩膜 ⇒ 脸亮、脖子手臂暗 ⇒ SV 报过的"突兀"；'
          '★ 注意：脸处读数会随身体权重下降（峰值归一的副作用），但只要 ≥ W_REF 就不亏')
    if m is None:
        return                                  # 上面已经红了；后面依赖掩膜，跑下去只会抛异常

    # ★★★ 09-29：**「量」和「作用」是两张不同的掩膜**（`facegain._masks` 的两个返回值）。
    #   原来"量"也用并集 ⇒ 调大 `GRADE_SKIN_BODY_W` 会改"闭环盯着谁" ⇒ **真脸被带偏**
    #   （实测 7 张单变量：6 张里 4 张出界，最差 ΔE00 10.54）。
    _ma, _mm = facegain._masks(pz, (h, w, 3), C)
    check('★★★ 「量」的掩膜**只用脸**（身体不许进来拉动闭环）',
          _ma is not None and _mm is not None
          and float(_mm[85, 120]) > 0.5 and float(_mm[h - 3, 3]) < 0.1,
          ('量：脸处 %.2f · 身体处 %.2f ｜ 作用：脸处 %.2f · 身体处 %.2f'
           % (float(_mm[85, 120]), float(_mm[h - 3, 3]),
              float(_ma[85, 120]), float(_ma[h - 3, 3]))) if _mm is not None else '掩膜没出来',
          '身体进了"量"的那张 ⇒ 调大身体权重会改"闭环盯着谁" ⇒ 真脸被带偏（09-29 实测最差 10.54）')
    _bw_keep = float(getattr(C, 'GRADE_SKIN_BODY_W', 0.8))
    try:
        C.GRADE_SKIN_BODY_W = 0.5 if _bw_keep > 0.65 else 0.8
        _ma2, _mm2 = facegain._masks(pz, (h, w, 3), C)
    finally:
        C.GRADE_SKIN_BODY_W = _bw_keep
    check('★★ 改身体权重 ⇒ **只动"作用"那张**，「量」那张一动不动',
          _ma2 is not None and _mm2 is not None
          and abs(float(_mm[h - 3, 3]) - float(_mm2[h - 3, 3])) < 1e-9
          and abs(float(_mm[85, 120]) - float(_mm2[85, 120])) < 1e-9
          and abs(float(_ma[h - 3, 3]) - float(_ma2[h - 3, 3])) > 0.1,
          ('量：身体处 %.2f → %.2f（应一动不动）｜ 作用：身体处 %.2f → %.2f'
           % (float(_mm[h - 3, 3]), float(_mm2[h - 3, 3]),
              float(_ma[h - 3, 3]), float(_ma2[h - 3, 3]))) if _mm2 is not None else '掩膜没出来',
          '两张一起变 ⇒ 身体权重又在偷偷改"闭环盯着谁"（09-29 那个坑）')
    _ma3, _mm3 = facegain._masks({'masks': {'face_skin': z.copy(), 'skin': sk}}, (h, w, 3), C)
    check('★★ 没有真脸（`DSCF1629` 那种）⇒ 「量」**回退成并集**（不许整张不动）',
          _mm3 is not None and _mm3 is _ma3 and float(_mm3[h - 3, 3]) > 0.2,
          ('回退后身体处 %.2f（与"作用"同一张：%s）'
           % (float(_mm3[h - 3, 3]), _mm3 is _ma3)) if _mm3 is not None else '掩膜没出来',
          '不回退 ⇒ 真脸 0 px 的片子整个不动作 ⇒ 把 `DSCF1629`（实测收住、ΔE00 0.95）一起筛掉')

    # ---- ΔE00 的尺子（09-29 栽过：方法名写错 ⇒ `except` 静默换成欧氏） ----
    _pair = ((50.0, 0.0, 0.0), (60.0, 30.0, -10.0))
    _d00 = facegain._de00(*_pair)
    _e76 = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(*_pair))))
    check('★★ ΔE00 不许等于欧氏 ΔE76（方法名写成 `CIEDE2000` 就会静默退化）',
          abs(_d00 - _e76) > 0.5,
          'ΔE00 %.2f · ΔE76 %.2f · 尺子 %s' % (_d00, _e76, facegain._DE_METRIC[0]),
          '两者相等 ⇒ `colour.delta_E` 抛了、`except` 把它静默换成欧氏（报出来的"ΔE00"是假数）')

    # ==================================================================
    # ★★★ 09-29（C2）：**第二段闭环（身体）+ 掩膜来源**的契约
    # ==================================================================
    _gap_keys = ('SKIN_GAP_ENABLE', 'SKIN_GAP_PROBE', 'SKIN_GAP_DAMP', 'SKIN_GAP_STEP',
                 'SKIN_GAP_TOTAL', 'SKIN_GAP_MAXIT', 'SKIN_GAP_STOP', 'SKIN_GAP_SIG_MAX_REL',
                 'SKIN_GAP_MIN_PX', 'SKIN_GAP_MIN_MEAS_PX')
    check('★★ 掩膜来源键在、取值合法',
          str(getattr(C, 'FACE_GAIN_MASK_SRC', '')).lower() in ('baseline', 'decoded'),
          '当前 %r' % getattr(C, 'FACE_GAIN_MASK_SRC', None),
          '键缺 / 拼错 ⇒ `apply` 里 `getattr` 永远取字面默认 ⇒ 掩膜又回到 RAW 上算，'
          '白裙子 / 游乐设施又被当皮肤（实测 +54% / +89%）')
    check('★★★ 掩膜来源**默认**是可信输入（`baseline` = 引擎出图，不是在很暗的 RAW 上算）',
          str(getattr(C, 'FACE_GAIN_MASK_SRC', '')).lower() == 'baseline',
          '当前 %r' % getattr(C, 'FACE_GAIN_MASK_SRC', None),
          'C1 三点定级顺手查出的真账：同一张成片换掩膜量，「脸↔身体的差」能从 −12.99 变 −0.30')
    check('★★ 身体闭环那一套键都在（`SKIN_GAP_*`）',
          all(hasattr(C, k) for k in _gap_keys),
          '缺：%s' % [k for k in _gap_keys if not hasattr(C, k)],
          '缺一个 ⇒ 那一项静默退回 `facegain` 里的兜底值 ⇒ 改 config 没反应（第 4 类坑）')
    _sg = targets.skin_gap_target(_PRESET) if hasattr(targets, 'skin_gap_target') else None
    check('★ 这条预设的**身体靶**解析得出 `(dL, dC, dH)`（挂在作者身上）',
          _sg is None or (len(tuple(_sg)) == 3 and all(np.isfinite(_sg))),
          '%s ⇒ %r' % (_PRESET, _sg),
          '解析不出 ⇒ 身体闭环静默不启用（"改了没反应"）')

    # ---- 掩膜来源：'baseline' 真的会去重算；拿不到 ⇒ 回退老来源且不崩 ----
    from . import face as _face_mod
    _src_keep = str(getattr(C, 'FACE_GAIN_MASK_SRC', 'decoded'))
    _parse_orig = _face_mod.parse
    _lin_t = _lin_from_disp(_gray_img(seed=83))
    _fs_syn = np.zeros((h, w), np.float64)
    _fs_syn[60:110, 90:150] = 1.0                 # 3000 px 的"脸"
    _sk_syn = np.zeros((h, w), np.float64)
    _sk_syn[h // 2:, :] = 1.0                     # 21600 px 的"全身皮肤"
    _pz_syn = {'masks': {'face_skin': _fs_syn, 'skin': _sk_syn}}
    _pz_none = {'masks': {'face_skin': np.zeros((h, w), np.float64),
                          'skin': np.zeros((h, w), np.float64)}}
    _ft_t = targets.face_lab_target(_PRESET)
    try:
        C.FACE_GAIN_MASK_SRC = 'baseline'
        _face_mod.parse = lambda _d: _pz_syn                       # 假装基线上认得出
        _o1, _g1 = presets.render_with_face(_lin_t, _PRESET, C, pz=_pz_none,
                                            target_L=_ft_t[0], target_a=_ft_t[1], target_b=_ft_t[2])
        check('★★★ 掩膜来源 = `baseline` ⇒ `apply` **自己重算掩膜**（不用调用方给的那张）',
              str(_g1.get('mask_src')) == 'baseline' and int(_g1.get('mask_px') or 0) > 0
              and bool(_g1.get('applied')),
              'mask_src=%r · mask_px=%s · applied=%s' % (_g1.get('mask_src'), _g1.get('mask_px'),
                                                         _g1.get('applied')),
              '调用方给的是**很暗的 RAW**上算的那张 ⇒ 拿它决定"给哪里加密度"就是在给白裙子加密度')
        _face_mod.parse = lambda _d: (_ for _ in ()).throw(RuntimeError('模拟模型缺失'))
        _o2, _g2 = presets.render_with_face(_lin_t, _PRESET, C, pz=_pz_syn,
                                            target_L=_ft_t[0], target_a=_ft_t[1], target_b=_ft_t[2])
        check('★★ 基线上分不出来 ⇒ **回退老来源**（不崩、报告里写明为什么）',
              str(_g2.get('mask_src') or '').startswith('decoded(回退') and bool(_g2.get('applied')),
              'mask_src=%r · applied=%s' % (_g2.get('mask_src'), _g2.get('applied')),
              '回退不写理由 ⇒ 以后只看到"动了但结果怪"，查不到掩膜到底是哪来的')
    finally:
        _face_mod.parse = _parse_orig
        C.FACE_GAIN_MASK_SRC = _src_keep

    # ---- 身体闭环：给靶 ⇒ 身体往靶走、**脸不动**；不给 ⇒ 逐位同旧行为 ----
    # ★★★★★ 09-29 SV 裁定后，生产默认是 `SKIN_GAP_ENABLE=False`（身体闭环随认人一起停）。
    #   但**代码保留了、没删** ⇒ 机制契约必须继续验 ⇒ 本段**显式把开关打开来跑**，跑完还原。
    #   （上面那三条"默认关、代码保留"的判据已经把生产口径钉住了。）
    _gap_keep = bool(getattr(C, 'SKIN_GAP_ENABLE', False))
    C.SKIN_GAP_ENABLE = True
    try:
        C.FACE_GAIN_MASK_SRC = 'decoded'                  # 这一组量"闭环本身"，掩膜用给定的
        _ob, _gb = presets.render_with_face(_lin_t, _PRESET, C, pz=_pz_syn,
                                            target_L=_ft_t[0], target_a=_ft_t[1], target_b=_ft_t[2])
        _og, _gg = presets.render_with_face(_lin_t, _PRESET, C, pz=_pz_syn,
                                            target_L=_ft_t[0], target_a=_ft_t[1], target_b=_ft_t[2],
                                            gap_target=(2.1, 0.9, -3.6))
        check('★ 没给 `gap_target` ⇒ **不做第二段**（报告里写明理由、出图逐位同旧行为）',
              not _gb.get('body_applied') and 'skin_gap' in str(_gb.get('body_note') or '')
              and int(_gb.get('body_px') or 0) == 0,
              'body_applied=%s · note=%r' % (_gb.get('body_applied'), _gb.get('body_note')),
              '没挂靶还硬做 ⇒ 就是在给身体瞎掰；不写理由 ⇒ 看不出为什么没做')
        _drift = _gg.get('body_face_drift')
        _norm0 = float(np.linalg.norm(np.asarray(_gg.get('body_target'), np.float64)
                                      - np.asarray(_gg.get('body_before'), np.float64)))
        _norm1 = float(np.linalg.norm(np.asarray(_gg.get('body_err'), np.float64)))
        check('★★★ 给了 `gap_target` ⇒ 身体**真的被推到靶附近**（误差变小、密度在限内）',
              bool(_gg.get('body_applied')) and int(_gg.get('body_px') or 0) >= int(C.SKIN_GAP_MIN_MEAS_PX)
              and float(_gg.get('body_delta_absmax') or 0) <= float(C.SKIN_GAP_TOTAL) + 1e-9
              and _norm1 < _norm0 - 0.3,
              '身体 px %s · 累计密度 %s（|max| %s）· 误差 %.2f → %.2f · 轮数 %s'
              % (_gg.get('body_px'), _gg.get('body_delta'), _gg.get('body_delta_absmax'),
                 _norm0, _norm1, _gg.get('body_iters')),
              '推不动 ⇒ 拿脸的响应矩阵去推身体（实测响应可差 8 倍）；超限还继续 ⇒ "硬掰"')
        check('★★★ 第二段**脸一个像素都不动**（作用范围只在 `skin − face_skin`）',
              _drift is not None and float(np.abs(np.asarray(_drift, np.float64)).max()) < 1.0,
              '脸漂移(L/C/H) %s ｜ 身体自己动了 %s' % (_drift, _gg.get('body_delta')),
              '脸被带偏 ⇒ 两段闭环互相打架 ⇒ 上一段的靶白定（1772 实测过 −1.9）')
        check('★ 报告里有 `any_applied`（身体可以**单独**动 ⇒ 判"动没动"不能只看 `applied`）',
              bool(_gg.get('any_applied'))
              and bool(_gg.get('any_applied')) == bool(_gg.get('applied') or _gg.get('body_applied')),
              'any_applied=%s ｜ applied=%s ｜ body_applied=%s'
              % (_gg.get('any_applied'), _gg.get('applied'), _gg.get('body_applied')),
              '只看 `applied` ⇒ "脸 0 轮、身体动了"的那种张会被当成"没动"，'
              '参照物/缓存判据全对不上（`_pure_engine_same` 就靠它）')
        _ob2, _ = presets.render_with_face(_lin_t, _PRESET, C, pz=_pz_syn,
                                           target_L=_ft_t[0], target_a=_ft_t[1], target_b=_ft_t[2])
        _noise2 = float(np.max(np.abs(np.asarray(_ob2) - np.asarray(_ob))))
        _og2, _gg2 = None, None
        try:
            C.SKIN_GAP_ENABLE = False
            _og2, _gg2 = presets.render_with_face(
                _lin_t, _PRESET, C, pz=_pz_syn, target_L=_ft_t[0], target_a=_ft_t[1],
                target_b=_ft_t[2], gap_target=(2.1, 0.9, -3.6))
        finally:
            C.SKIN_GAP_ENABLE = True                     # ← 本段开头把开关打开了，这里还原到"打开"
        _dd = float(np.max(np.abs(np.asarray(_og2) - np.asarray(_ob))))
        check('★ `SKIN_GAP_ENABLE=False` ⇒ 第二段**整段不跑**（出图回到"没有第二段"那张）',
              not _gg2.get('body_applied') and '关着' in str(_gg2.get('body_note') or '')
              and _dd <= max(3.0 * _noise2, 5e-3),
              'body_applied=%s · note=%r · 与"没有第二段"那张的最大差 %.3g（引擎自噪声 %.3g）'
              % (_gg2.get('body_applied'), _gg2.get('body_note'), _dd, _noise2),
              '关了还动（超出引擎自身噪声）⇒ 开关是摆着看的'
              '（本项目已经栽过好几类的"拧了没反应"）')
    finally:
        C.FACE_GAIN_MASK_SRC = _src_keep
        C.SKIN_GAP_ENABLE = _gap_keep                 # ★ 还原生产口径（默认关）

    # ---- ③④ 端到端（走的是生产同一条 `presets.render_with_face`） ----
    # ★★ 端到端必须用**接近真实比例**的掩膜（脸框 30x36 ≈ 2.5% 画幅）。
    #   羽化 σ = √(掩膜面积)/6 ⇒ **掩膜铺满画幅时，羽化会把整张都盖上**，"真·掩膜外"
    #   一个像素都不剩，这条检查就成了空转。09-29 我第一版拿 53% 的掩膜量出"掩膜外也动 0.27"
    #   —— 那 0.27 量的是**羽化环**（掩膜的一部分，本来就该动）：**是尺子错了，不是代码错了**。
    lin = _lin_from_disp(_gray_img(seed=83))
    _bh, _bw = 50, 50                             # ★ 必须 ≥ FACE_GAIN_MIN_MASK_PX（2500 px）
    _y0, _x0 = (h - _bh) // 2, (w - _bw) // 2
    _fs2 = np.zeros((h, w), np.float64)
    _fs2[_y0:_y0 + _bh, _x0:_x0 + _bw] = 1.0
    pzF = {'masks': {'face_skin': _fs2}}          # ★ 只给脸 ⇒ 羽化不会盖满全图
    mF = facegain._mask(pzF, (h, w, 3), C)
    _in = (mF > 0.5) if mF is not None else np.zeros((h, w), bool)
    _out = (mF <= 1e-9) if mF is not None else np.zeros((h, w), bool)
    check('★ 端到端那张图的"真·掩膜外"占得住（否则下面那条是空转）',
          mF is not None and float(_out.mean()) > 0.20,
          '掩膜内 %.1f%% · 真外 %.1f%%' % (100 * _in.mean(), 100 * _out.mean()),
          '真外太少 ⇒ 羽化把整张盖住了，量"溢出"等于没量')
    ft = targets.face_lab_target(_PRESET)
    check('★ 这条预设的**脸靶**在（L* / a* / b* 三件）', bool(ft), '解析出 %r' % (ft,),
          '靶不在 ⇒ 脸增益静默不启用（"改了没反应"）')
    if not ft or float(_out.mean()) <= 0.20:
        return

    # ★★ 09-29（C2）：这一组量的是**闭环本身**（给定掩膜 ⇒ 迭代到靶），
    #   所以把掩膜来源钉成 `decoded`（= 用我传进去的 `pzF`）。
    #   "来源 = baseline 时会去重算 / 拿不到会回退" 是上面那两条单独的契约。
    _src_e2e = str(getattr(C, 'FACE_GAIN_MASK_SRC', 'decoded'))
    C.FACE_GAIN_MASK_SRC = 'decoded'
    try:
        base = np.asarray(presets.render(lin, _PRESET, C))
        noise = float(np.max(np.abs(base - np.asarray(presets.render(lin, _PRESET, C)))))
        out, gi = presets.render_with_face(lin, _PRESET, C, pz=pzF, target_L=ft[0],
                                           target_a=ft[1], target_b=ft[2])
        check('★ 有靶 ⇒ 报告说它真的动了（applied + 迭代轮数）',
              bool(gi.get('applied')) and gi.get('de00') is not None,
              'iters=%s · ΔE00=%s · 真脸 %s px' % (gi.get('iters'), gi.get('de00'), gi.get('face_px')),
              '没动 ⇒ 整段闭环是空转的，而报告里还写着"开了"')
        _de0 = facegain._de00(np.asarray(gi['lab_before'], np.float64),
                              np.asarray(gi['target'], np.float64))
        check('★ 收敛：ΔE00 真的变小（不是乱动）',
              gi.get('de00') is not None and float(gi['de00']) < _de0 - 0.5,
              'ΔE00 %.2f → %.2f（%s 轮）' % (_de0, float(gi['de00'] or _de0), gi.get('iters')),
              '没变小 ⇒ 响应矩阵不是这一版标定的那个 / 掩膜与迭代用的不是同一个')
        _d = np.abs(np.asarray(out) - base).max(axis=-1)
        _din, _dout = float(_d[_in].mean()), float(_d[_out].max())
        check('★★ 只动脸：掩膜内明显变动、**真·掩膜外**一动没动',
              _din > 0.02 and _dout <= max(5.0 * noise, 5e-3),
              '掩膜内均 %.4f · 真外 max %.4f · 引擎自噪声 %.4f' % (_din, _dout, noise),
              '真外也动 ⇒ 这个"局部层"在污染整张（当年 L4 就是这么坏事的）')
        out2, gi2 = presets.render_with_face(lin, _PRESET, C, pz=pzF)          # 不给靶
        check('★ 没有靶 ⇒ 走普通 `render`（不启用，逐位同旧行为）',
              not gi2.get('applied')
              and float(np.max(np.abs(np.asarray(out2) - base))) <= max(noise * 2.5, 5e-3),
              'applied=%s' % gi2.get('applied'))
    finally:
        C.FACE_GAIN_MASK_SRC = _src_e2e


def t_targets():
    """靶按预设分组（`targets.py`）：换预设必须换靶。"""
    from . import targets

    check('★ 滨田 / 増田 两条预设各自有专属靶（不是落回 _default）',
          targets.for_stock('Pro400H清风')['own'] and targets.for_stock('Portra400薄荷')['own'])
    check('★ 没有专属靶的预设落回 _default（不报错、有靶）',
          targets.for_stock('C200过曝')['own'] is False
          and targets.for_stock('C200过曝')['black_shape'] is not None)
    b, z = targets.for_stock('Pro400H清风'), targets.for_stock('Portra400薄荷')
    # ★★ 09-29 晚：`sh_abs / hi_abs` **换了口径**（「分带 median − 整张 median」，见
    #   `targets.json` 的 `_split_abs_note`）⇒ 数值比老口径小约 6 倍（老 ~−9 ⇒ 新 ~−1）。
    #   老判据 `|暗Δa 差| > 1.0` 是**旧量纲**下调的阈值；在新量纲下"差 1.0 格"已经相当于
    #   两条预设的暗部 chroma 靶差一个数量级 —— **阈值过时，不是靶被复制了**。
    #   这条契约的本意只是「**不是把同一个文件复制了两份**」（一个文件一个靶）⇒ 改成：
    #   黑位差 > 1.0（**影调量纲、本轮没换口径**，照旧）+ 分色四项里**最大的一项**差 > 0.25
    #   （0.25 远高于 JSON 浮点噪声、又远低于 1 格的感知量级，正好是"查重"该有的尺度）。
    _sp = [abs(b['sh_abs'][i] - z['sh_abs'][i]) for i in range(2)] \
        + [abs(b['hi_abs'][i] - z['hi_abs'][i]) for i in range(2)]
    check('★★ 两条预设的靶**真的不一样**（一个文件一个靶，不是复制）',
          abs(b['black_shape'] - z['black_shape']) > 1.0 and max(_sp) > 0.25,
          '黑位 %.1f vs %.1f · 分色最大差 %.2f（暗Δa %+.2f vs %+.2f / 亮Δa %+.2f vs %+.2f）'
          % (b['black_shape'], z['black_shape'], max(_sp),
             b['sh_abs'][0], z['sh_abs'][0], b['hi_abs'][0], z['hi_abs'][0]),
          '两条预设的靶一模一样 ⇒ 多半是把同一个文件复制了两份（靶没分开）')
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
    # ★ 这一段断言测的是**分色 / 混色（L2/L3）**，所以显式把 scope 设成 'all' ——
    #   当前阶段的默认是 'skin'（只跑肤色 L4），不设的话这几条会全部过期变红。
    _scope0 = getattr(C, 'GRADE_SCOPE', 'all')
    # ★★ 09-29：原来只有 `GRADE_SCOPE` 被还原，`GRADE_ENABLE` **在 `finally` 里被硬写成 True**
    #   ⇒ 这一组跑完，后面所有组看到的都是"颜色层开着"，而 `_GRADE_ON`（进口时读的）还是 False
    #   ⇒ `t_mask_contract` / `t_contract` 里那些 `else`（"关掉"分支）全部错位变红
    #   —— 4 条老账里有 3 条是这么来的。**开关必须成对还原**。
    _ge0 = bool(getattr(C, 'GRADE_ENABLE', True))
    # ★★★ 09-29：**彩度守恒那条契约的前提是 `GRADE_SAT=1.0`**（"只重新分配、不改总量"）。
    #   本会话把默认改成 0.72（落地 A）⇒ 契约**前提变了**、旧写法没显式设它 ⇒ 必然假红
    #   （实测 33.65 → 23.42 = −30.4%，正好是 ×0.72 的量级，**不是 bug**）。
    #   ⇒ 按纪律：**在这里显式把它设成 1.0 来跑**（跑完还原），判据数字一个字不改。
    _sat0 = float(getattr(C, 'GRADE_SAT', 1.0))
    C.GRADE_SCOPE = 'all'
    # ① 关掉 ⇒ 逐位不变（不能"说关还偷偷动一点"）
    C.GRADE_ENABLE = False
    try:
        off, info = grade.apply(disp, C)
    finally:
        C.GRADE_ENABLE = True          # 下面几条要测"开着"的样子（函数结束时会还原成 _ge0）
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
    C.GRADE_SAT = 1.0                  # ★ 09-29：契约前提（只重新分配）——显式设，跑完还原
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
    C.GRADE_SAT = _sat0                # ★ 09-29：还原到进来时的值（别把 0.72 落成 1.0）

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

    # ⑤ ★ scope='skin' ⇒ 只跑肤色：分色/混色必须**逐位不生效**（这是"分阶段跑"的钉子）
    _rgb = np.random.RandomState(77)
    _c = np.stack([_rgb.rand(96, 96) * 0.55 + 0.22 for _ in range(3)], -1)
    _c[..., 1] = np.clip(_c[..., 1] * 1.05, 0, 1)
    C.GRADE_SCOPE = 'skin'
    _o_skin, _i_skin = grade.apply(_c, C)
    C.GRADE_SCOPE = 'all'
    _o_all, _ = grade.apply(_c, C)
    C.GRADE_SCOPE = _scope0
    C.GRADE_ENABLE = _ge0              # ★ 09-29：开关成对还原（漏了它 ⇒ 后面三组全部错位变红）
    _lab_s = color.to_lab(np.ascontiguousarray(_o_skin))
    _lab_a = color.to_lab(np.ascontiguousarray(_o_all))
    _ms = float(np.median(np.hypot(_lab_s[..., 1], _lab_s[..., 2])))
    _ma = float(np.median(np.hypot(_lab_a[..., 1], _lab_a[..., 2])))
    check('★★ scope=skin ⇒ 分色/混色**不生效**（只肤色）；scope=all ⇒ 两者都生效',
          abs(_ms - _ma) > 1e-6 and _i_skin.get('scope') == 'skin',
          '整张彩度中位：skin %.2f vs all %.2f ；报告 scope=%s' % (_ms, _ma, _i_skin.get('scope')),
          '两者完全一样 ⇒ 分色/混色没被真的跳过（"分阶段跑"这条就废了）')


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


def t_no_face_step():
    """★★★★★ 09-29 SV 裁定：**去掉「认人 / 认脸」这一步** —— 四件事必须钉死。

    SV 原话：「去掉认人认脸的相关步骤 肤色层和这个肤色增益 **跑的太慢了也没有达到我想要的效果**」
    ⇒ 关掉 `config.FACE_STEP_ENABLE` 之后：

      ① 整条链**一次都不调** `face.parse`（不跑 mediapipe、不跑 birefnet-portrait）
         —— 这是"慢"的主因之一，0.8~7 秒/张；
      ② **后门也堵上** —— `grade.apply` / `region.masks` 在"没传 parsed"时**不许自己补算**
         （否则把 `GRADE_ENABLE` 一开，"认人"又静默回来了）；
      ③ 关掉它**不该**把"跨度到靶"弄丢 —— `_scene_engine` 那条全局兜底 `"*"`
         （`density_curves_morph` ＝ 当前把跨度送到共识靶 81.9 的**唯一手段**）必须照旧命中；
      ④ `scene` 该降级的**如实降级**（`back`/`shot`/`face` 为空），该活的照样活（`exp`/`span`）。
    """
    from . import face as _face, grade, pipeline, region, scene, targets as _TS

    check('★ 总闸默认关', not bool(getattr(C, 'FACE_STEP_ENABLE', True)),
          'FACE_STEP_ENABLE=%r' % getattr(C, 'FACE_STEP_ENABLE', '★ 缺键'))

    calls = []
    _orig = _face.parse
    _step0 = bool(getattr(C, 'FACE_STEP_ENABLE', True))
    _ge0 = bool(getattr(C, 'GRADE_ENABLE', False))

    def _spy(d):
        calls.append(tuple(np.asarray(d).shape[:2]))
        return None                       # ★ 不真跑模型：本组只数"有没有被调"

    _face.parse = _spy
    try:
        # ① 整条链
        s = _mk_sample(_gray_img(seed=91))
        try:
            pipeline.cache.clear()
        except Exception:                                        # noqa: BLE001
            pass
        calls.clear()
        pipeline.run_from(s, stock=_PRESET, style='中性调')
        check('★★★★★ 总闸关 ⇒ 整条链**一次都不调** `face.parse`',
              len(calls) == 0, '调了 %d 次：%s' % (len(calls), calls[:3]),
              '还在调 ⇒ SV 裁定的"去掉"没做到；每张白付 0.8 秒（mediapipe）~ 7 秒（birefnet）')

        # ② 后门
        _img = np.asarray(_gray_img(seed=92), np.float64)
        calls.clear()
        region.masks(_img, C)                                    # 直接调（旧行为：没给就自己算）
        C.GRADE_ENABLE = True                                    # 故意开起来，逼它走那条老分支
        grade.apply(_img, C, stock=_PRESET, parsed=None)
        check('★★★★ **没有后门**：`region.masks` / `grade.apply`（没传 parsed）也不许自己补算掩膜',
              len(calls) == 0, '调了 %d 次：%s' % (len(calls), calls[:3]),
              '后门开着 ⇒ 以后把 GRADE_ENABLE 打开时，"认人"又静默回来，白付一次分割')
        C.GRADE_ENABLE = _ge0

        # ③④ 场景：该降级的降级，该活的活
        _sc = scene.classify_after(_img, None, C)
        check('★★ 总闸关 ⇒ `back`（光位）/ `shot`（景别）为空、`face`（脸可见度）= none',
              _sc.get('back') is None and _sc.get('shot') is None and _sc.get('face') == 'none',
              'back=%r ｜ shot=%r ｜ face=%r' % (_sc.get('back'), _sc.get('shot'), _sc.get('face')),
              '这是**如实降级**，不是崩 —— 用这三轴的地方只有 `_scene` 那两条（改肤色层旋钮）')
        check('★★ 但 `exp` / `span` 照旧有值（它们用整张统计，不碰掩膜）',
              _sc.get('exp') is not None and _sc.get('span') is not None,
              'exp=%r ｜ span=%r' % (_sc.get('exp'), _sc.get('span')))
        _ov = _TS.scene_engine(_sc, stock=_PRESET, cfg=C)
        _mo = (_ov or {}).get('print_render.density_curves_morph.active')
        check('★★★★★ `_scene_engine` 的全局兜底 `"*"` 仍然命中（那条 morph ＝ 跨度到靶的唯一手段）',
              bool(_mo), 'morph.active=%r ｜ 本次覆盖键 %s' % (_mo, list((_ov or {}).keys())[:4]),
              '它靠全局 "*" 生效、**不依赖任何轴** ⇒ 关掉认人**不该**把跨度弄丢；'
              '红了说明"跨度 81.87 到靶"这件事被这一步带走了')
    finally:
        _face.parse = _orig
        C.FACE_STEP_ENABLE = _step0
        C.GRADE_ENABLE = _ge0


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

    # ★★★★★ 09-29 SV 裁定后，生产默认是 `FACE_STEP_ENABLE=False`（认人整段不进链）⇒
    #   这一组"按图去重 / 只喂解码后那张"的契约在**默认口径下是空转的**（一次都不调，`_n_dec=0`）。
    #   但**代码保留了、没删** ⇒ 机制契约必须继续验。⇒ 本组**显式把总闸打开来跑**，
    #   跑完在 `finally` 里还原。这样两种状态都钉住：
    #     · 默认关 ⇒ 见上面 `t_no_face_step`（一次都不调 / 没后门 / 不丢跨度）；
    #     · 一旦有人把总闸打开 ⇒ 本组保证"掩膜算一次、喂的是解码后那张"仍然成立。
    _step_keep = bool(getattr(C, 'FACE_STEP_ENABLE', True))
    C.FACE_STEP_ENABLE = True
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
        if _GRADE_ON:
            check('报告里标明掩膜来自外部（skin_mask_src = given）',
                  gi.get('skin_mask_src') == 'given', str(gi.get('skin_mask_src')))
        else:
            check('★ 颜色层关掉 ⇒ `grade.apply` 直接原样返回（连掩膜来源键都不产生）',
                  gi.get('applied') is False and gi.get('skin_mask_src') is None,
                  str(gi.get('note')))

        # ② 跑整条链：**解码后那张只算一次**，而且喂的是**解码后**那张图。
        #    ★★★ 09-29（C2）：`FACE_GAIN_MASK_SRC='baseline'` 时 `facegain` 会**再算一份** ——
        #      那一份是**引擎基线**，和"解码后"是**两张不同的图** ⇒ 不是"重复算"，
        #      是"给另一张图算一份"。所以这里的判据从"总共 1 次"改成**按图去重**：
        #      · 解码后那张 **恰好 1 次**（老契约不变）；
        #      · 引擎基线那张 **最多 1 次**，且**只在脸增益开着 + 来源=baseline + 有脸靶**时才有。
        s = _mk_sample(_gray_img(seed=77))
        calls.clear()
        r = pipeline.run_from(s, stock=_PRESET, style='中性调')
        got = list(calls)
        _med = [c[1] for c in got]
        _m0 = float(np.median(s.disp))
        _n_dec = sum(1 for m in _med if abs(m - _m0) < 1e-9)
        _fg_on = bool(getattr(C, 'FACE_GAIN_ENABLE', False))
        _src = str(getattr(C, 'FACE_GAIN_MASK_SRC', 'decoded')).lower()
        try:
            from . import targets as _TS
            _ft_on = bool(_TS.face_lab_target(_PRESET))
        except Exception:                                        # noqa: BLE001
            _ft_on = False
        _want = 1 + (1 if (_fg_on and _src == 'baseline' and _ft_on) else 0)
        check('★★★ 人脸掩膜**按图去重**：解码后那张 1 次 + 引擎基线那张（C2 新加）0~1 次',
              _n_dec == 1 and len(got) <= _want,
              '共 %d 次（期望 ≤%d）｜ 其中解码后 %d 次 ｜ 中位 %s'
              % (len(got), _want, _n_dec, [round(m, 4) for m in _med]),
              '解码后 >1 ⇒ 还在重复算（白付 273 ms/次）；总量 >%d ⇒ 有第三次冗余分割' % _want)
        check('★★★ 喂进去的**第一份**是**解码后**那张图，不是胶片引擎的成片',
              _n_dec == 1 and abs(_med[0] - _m0) < 1e-9,
              '第一份喂进去的中位 %.4f ；解码后那张 %.4f' % (_med[0] if _med else float('nan'), _m0),
              '喂成片 ⇒ 链尾发白、分割认不出脸（老路专门避开这件事，'
              '新路 09-26 之前又撞上了）')
        _gi2 = ((r.report.get('tone') or {}).get('grade') or {})
        if _GRADE_ON:
            check('★ 整条链的报告里 skin_mask_src = given（掩膜走的是解码后那一份）',
                  _gi2.get('skin_mask_src') == 'given', str(_gi2.get('skin_mask_src')))
        else:
            check('★ 颜色层关掉（`GRADE_ENABLE=False`）⇒ 报告里如实写明"没跑这一层"',
                  _gi2.get('applied') is False and '关' in str(_gi2.get('note') or ''),
                  str(_gi2.get('note')))

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
        C.FACE_STEP_ENABLE = _step_keep          # ★ 还原生产口径（默认关）—— 别把总闸留在我这儿


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
    _sc = {'key': 'v1|x', 'exp': '暗', 'span': '平', 'back': '顺平光',
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
    # ★★ 09-29：引擎这一步现在有**两条入口** —— `presets.render`（普通）与
    #   `presets.render_with_face`（脸增益，**默认开**）。只插桩前者 ⇒ 走脸增益那条路时
    #   `calls` 会**空着**，把"引擎跑了"误报成"引擎没跑"（这条就是这么红的）。
    #   `render_with_face` 不给靶时内部还会调一次 `render` ⇒ **相邻同名去重**。
    def _mark(tag, fn):
        def _w(*a, **k):
            if not calls or calls[-1] != tag:
                calls.append(tag)
            return fn(*a, **k)
        return _w

    if _AFTER:
        _f, _p0, _pf0 = tone.settle_finished, presets.render, presets.render_with_face
        tone.settle_finished, presets.render, presets.render_with_face = (
            _mark('tone', _f), _mark('presets', _p0), _mark('presets', _pf0))
    else:
        _t0, _p0, _pf0 = tone.apply, presets.render, presets.render_with_face
        tone.apply, presets.render, presets.render_with_face = (
            _mark('tone', _t0), _mark('presets', _p0), _mark('presets', _pf0))
    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=11)), stock=_PRESET, style='中性调')
    finally:
        if _AFTER:
            tone.settle_finished, presets.render, presets.render_with_face = _f, _p0, _pf0
        else:
            tone.apply, presets.render, presets.render_with_face = _t0, _p0, _pf0
    if _AFTER and _TONE_ON:
        check('★★ 曝光风格跑在胶片引擎**之后**（控制不了成片亮度，只能事后收）',
              calls[:2] == ['presets', 'tone'], '调用序: %s' % calls[:4],
              '顺序反了 = 又回到"在引擎之前调亮度"，实测那样三条档只拉开 8.8（靶上该 29.4）')
    elif _AFTER:
        check('★ 影调层关掉 ⇒ 调用序里只有引擎、**没有** tone（别偷偷把这一层加回来）',
              calls[:2] == ['presets'], '调用序: %s' % calls[:4])
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
    if _AFTER and _TONE_ON:
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
    elif _AFTER:
        check('★ 影调层关掉（`TONE_ENABLE=False`）⇒ 报告里如实写明"没跑这一层"',
              t.get('applied') is False and '关' in str(t.get('note') or ''),
              str(t.get('note')))
        # ★★ 09-29：这条契约的本意是「**影调层**关掉后没有别的层再偷偷动像素」——
        #   它的前提是**颜色层也关着**。本会话把 `GRADE_ENABLE` 默认打开（落地 A）⇒
        #   带着它出图必然 ≠ 纯引擎（**不是 bug，是契约前提变了**）。
        #   ⇒ 按纪律：**在检查里显式把颜色层关掉来比**（比完还原到进来时的值），
        #     契约与判据数字原样保留 —— 这样"影调层关着"这条契约在任何默认值下都还被钉着。
        _ge_k = bool(getattr(C, 'GRADE_ENABLE', True))
        C.GRADE_ENABLE = False
        try:
            _r_pure = pipeline.run_from(_mk_sample(_gray_img(seed=17)), stock=_PRESET, style='暗调')
        finally:
            C.GRADE_ENABLE = _ge_k
        check('★★ 关掉影调层 ⇒ 出图**就是纯引擎输出**（后两层不许偷偷动像素）',
              _pure_engine_same(_r_pure, _mk_sample(_gray_img(seed=17)), '暗调'),
              '与"只跑 presets.render"的差超出了引擎自身噪声',
              '关了这一层却还在改像素 ⇒ 开关没接对')
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
    # ★★ 09-29：这条要**跟着阶段走**。当前阶段 `TONE_ENABLE=False`（只做胶片引擎）
    #   ⇒ 换曝光风格本来就**不该改画面**（只换了标签），原来那条"两档画面必须不同"
    #   是影调层开着时的判据 ⇒ 在老账里一直红着。反过来钉"关着就必须一样"，
    #   正好能抓住"影调层偷偷跑起来了"。
    _ds = float(np.max(np.abs(r2.disp - r3.disp)))
    if _TONE_ON:
        check('两档出来的画面真的不同', _ds > 0.01, '最大差 %.4f' % _ds)
    else:
        check('★ 影调层关着（`TONE_ENABLE=False`）⇒ 换曝光风格画面照旧一样'
              '（只换标签，不许偷偷改像素）',
              _ds < 5e-3, '最大差 %.4f（引擎自身噪声量级）' % _ds,
              '换了个曝光风格画面就变了 ⇒ 影调层在偷偷跑')

    # ★★ 09-29：**脸增益开关也必须进键** —— 它改的是出图本身。
    #   不进键 ⇒ 常驻进程里把它一开，仍会命中"没开脸增益"的旧缓存 ⇒ "拧了没反应"（第 4 类）。
    _fg0 = bool(C.FACE_GAIN_ENABLE)
    try:
        C.FACE_GAIN_ENABLE = not _fg0
        r5 = pipeline.run_from(s, stock=_PRESET, style='中性调', cache=c)
    finally:
        C.FACE_GAIN_ENABLE = _fg0
    check('★★ 换脸增益开关 ⇒ **不**命中（否则就是"拧了没反应"）',
          not r5.report['stage_cache']['hit'],
          '开关 %s → %s，命中=%s' % (_fg0, not _fg0, r5.report['stage_cache']['hit']))

    # ★★ 09-29：**肤色层的"形状旋钮"也必须进键**（`GRADE_SKIN_BODY_W` 尤其）。
    #   它改的是"脸 ∪ 身体皮肤"的掩膜形状 + L4 修正量 = **直接改出图**。
    #   ⚠ 不进键 ⇒ 常驻进程里改了这个值仍命中旧缓存 ⇒ 画面照旧 = "拧了没反应"（第 4 类）。
    _bw0 = float(getattr(C, 'GRADE_SKIN_BODY_W', 0.5))
    _bw1 = 0.5 if abs(_bw0 - 0.8) < 1e-9 else 0.8
    try:
        C.GRADE_SKIN_BODY_W = _bw1
        r6 = pipeline.run_from(s, stock=_PRESET, style='中性调', cache=c)
    finally:
        C.GRADE_SKIN_BODY_W = _bw0
    check('★★ 换身体皮肤权重 ⇒ **不**命中（它改掩膜形状 + L4 修正量）',
          not r6.report['stage_cache']['hit'],
          'GRADE_SKIN_BODY_W %.1f → %.1f，命中=%s'
          % (_bw0, _bw1, r6.report['stage_cache']['hit']),
          '不进键 ⇒ 改了这个值画面照旧 ⇒ "拧了没反应"（与 `GRADE_ENABLE` 同一类坑）')

    # ★★ 09-29（B 清账）：L4 新增的两个键**也必须进键**（同一类坑，先堵上）。
    #   现在 `GRADE_ENABLE=False` ⇒ 它们不影响画面；但一旦把 L4 打开，
    #   改 `GRADE_SKIN_MEAS_FACE`（「量」用哪张掩膜）或 `SKIN_ABS_OWNER`（谁写脸的绝对靶）
    #   就是在改修正量 ⇒ 不进键照样"拧了没反应"。
    _mf0 = bool(getattr(C, 'GRADE_SKIN_MEAS_FACE', True))
    _ow0 = str(getattr(C, 'SKIN_ABS_OWNER', 'facegain'))
    try:
        C.GRADE_SKIN_MEAS_FACE = not _mf0
        r7 = pipeline.run_from(s, stock=_PRESET, style='中性调', cache=c)
        C.GRADE_SKIN_MEAS_FACE = _mf0
        C.SKIN_ABS_OWNER = 'grade' if _ow0 != 'grade' else 'facegain'
        r8 = pipeline.run_from(s, stock=_PRESET, style='中性调', cache=c)
    finally:
        C.GRADE_SKIN_MEAS_FACE = _mf0
        C.SKIN_ABS_OWNER = _ow0
    check('★★ 换「量」的掩膜口径（`GRADE_SKIN_MEAS_FACE`）⇒ **不**命中',
          not r7.report['stage_cache']['hit'],
          '命中=%s' % r7.report['stage_cache']['hit'])
    check('★★ 换「脸的绝对靶」主人（`SKIN_ABS_OWNER`）⇒ **不**命中',
          not r8.report['stage_cache']['hit'],
          '命中=%s' % r8.report['stage_cache']['hit'],
          '不进键 ⇒ 以后打开 L4 时改这两个键画面照旧 ⇒ "拧了没反应"第 4 类')


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
