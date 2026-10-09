# -*- coding: utf-8 -*-
r"""自检 —— 动完任何一层都要跑。不依赖任何样片，纯合成图 + 不变量。

  python -m svFilm.selftest

★ 09-29 起这条链只剩：
  **解码 + 白平衡 + 护栏 → 判场景 → 胶片引擎(0.3.4) → 颜色层（混色 → 分色）→ 出图**。

  影调层（`tone.py`）/ 肤色层（`grade.skin()`）/ 认人认脸（`face.py` · `facegain.py` · `region.py`）
  **整段删除**（不是关开关）⇒ 那几百条检查随模块一起删了（它们验的对象已经不存在）。
  另立一组 `t_dropped_layers` 钉"**真的删了**"。
"""
import os
import sys

import numpy as np

from . import color, config as C, io, pipeline, presets

# ★ 09-23：凡是要点名「一条胶片风格」的地方就用这一条（别把中文名写死到各处）。
_PRESET = 'Portra400薄荷'

# ★ 颜色层（`grade.py`）当前开着。它关掉之后报告里没有 `d_sh` / `c_gain` 这些键
#   ⇒ 断言必须跟着开关走，否则会把"关掉这一层"误报成"功能坏了"。
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
    """颜色层关掉时，`pipeline` 的出图应当**就是** `presets.render` 的输出。

    ⚠ 引擎的颗粒是随机的 ⇒ 判据只能是"差值在引擎自身的噪声量级内"，
      **不能**要求逐位相等。参照量 = 同一输入连跑两次纯引擎的差值。
    ★★ 09-29：参照物必须带上**生产那份引擎覆盖**（`_scene_engine`，例：全局那条
      `density_curves_morph` 把跨度 76.2 拉到 81.87）。不带 ⇒ 拿"没有覆盖的引擎"去比
      "有覆盖的引擎"，差的 0.032 **全是覆盖带来的**，与颜色层无关。
    ★ `style` 参数保留只为兼容调用处签名 —— 影调层已删，不再需要它。
    """
    lin = np.clip(sample.lin, 0.0, None)
    a = np.asarray(res.disp)
    from . import targets as _TS
    _sc = res.report.get('scene')
    ov = _TS.scene_engine(_sc, stock=res.report['stock'], cfg=C) or None
    stock = res.report['stock']
    b = np.asarray(presets.render(lin, stock, C, overrides=ov))
    c = np.asarray(presets.render(lin, stock, C, overrides=ov))
    noise = float(np.max(np.abs(b - c)))          # 引擎自身不可复现的那点
    diff = float(np.max(np.abs(a - b)))
    return diff <= max(noise * 2.5, 5e-3)


def t_review_1008():
    r"""★★★ 10-08 代码评审后的**防复发**断言。

    这一组守的都是"接线断了但没人发现"的一类 —— 项目里已经栽过三次
    （`agx_particle_*` 字段改名、`print_render.density_curve_gamma` 被删、
    `service._DECODE_SIG_KEYS` 靠的 `ENTRY_*` 前缀被删光）。
    它们的共同点是：**没有任何东西会报错**。
    """
    import numpy as _np
    from . import config as C, grade, presets, service, targets

    # 造一张**各亮度段都非空**的合成图（纯随机图会让分带掩膜退化，测不出东西）
    _yy = _np.mgrid[0:64, 0:64][0] / 63.0
    _d = _np.clip(_yy[..., None].repeat(3, 2) * 0.7 + 0.15
                  + 0.02 * _np.random.RandomState(0).rand(64, 64, 3), 0.0, 1.0)

    # ---- ① 报告同形：关整层 vs 开着，键集合必须一致 ----
    _on = grade.apply(_d, C, stock='Ultramax400沉褐', scene=None, person=None)[1]
    _keep = C.GRADE_ENABLE
    C.GRADE_ENABLE = False
    try:
        _off = grade.apply(_d, C, stock='Ultramax400沉褐', scene=None, person=None)[1]
    finally:
        C.GRADE_ENABLE = _keep
    _miss = sorted(set(_on) ^ set(_off))
    check('★★★ 关颜色层时报告与开着时**键集合完全相同**', not _miss,
          '开着 %d 键 / 关着 %d 键；差异: %s' % (len(_on), len(_off), _miss or '无'),
          '两种形状 ⇒ 下游 `.get(key, 默认)` 一条路拿默认值、一条路拿真值，排查时误导')

    # ---- ② 白名单合并透传：split()/mix() 的每个自检键都要在 apply() 报告里 ----
    _r = _np.random.RandomState(1)
    _L2 = _r.rand(64, 64) * 60.0 + 20.0
    _a2 = _r.rand(64, 64) * 10.0 - 5.0
    _b2 = _r.rand(64, 64) * 10.0 - 5.0
    _tg = targets.for_stock('Ultramax400沉褐', None)
    _m = dict(Lm=float(_np.median(_L2)), am=float(_np.median(_a2)), bm=float(_np.median(_b2)))
    _i2 = grade.split(_L2, _a2, _b2, _tg, C, _m)[2]
    _i3 = grade.mix(_L2, _a2, _b2, _tg, C, _m)[3]
    _miss2 = sorted((set(_i2) | set(_i3)) - set(_on))
    check('★★★ 分色/混色返回的**每个**自检键都进了 `apply()` 报告（白名单合并）',
          not _miss2, '缺: %s' % (_miss2 or '无'),
          '这份手抄透传清单已经漏过两次（`split_resid` 一轮、'
          '`d_mid`/`d_deep`/`split_curve_a/b` 一轮），每次代价都是"白跑一轮真渲染"')

    # ---- ③ band_gain 长度：不足会**静默跳过**那几个色相带 ----
    _nb = len(grade.BANDS)
    _badb = [n for n in presets.names()
             if len(((targets.for_stock(n, None) or {}).get('band_gain')) or [0] * _nb) < _nb]
    check('★★ 每条预设的 `band_gain` 长度 ≥ 色相带数（%d）' % _nb, not _badb,
          '不足: %s' % (_badb or '无'),
          '不足时 `grade.mix` 用 `if bi < len(_bg)` **静默跳过**，不报错也不进报告')

    # ---- ④ 解码签名不能再是空的（`ENTRY_*` 那次的教训）----
    _gone = [k for k in service._DECODE_SIG_KEYS if not hasattr(C, k)]
    check('★★★ 解码签名有键、且键真的在 config 里', bool(service._DECODE_SIG_KEYS) and not _gone,
          '键=%s 缺=%s' % (list(service._DECODE_SIG_KEYS), _gone or '无'),
          '老写法靠 `ENTRY_` 前缀取键，前缀被删光后恒为空 ⇒ 机制静默失效、无人发现')

    # ---- ⑤ 段缓存键必须覆盖**全部** GRADE_*（"拧了没反应"的根）----
    _sig = dict(C.key_signature(C))
    _gk = [k for k in dir(C) if k.startswith('GRADE_')]
    _missg = [k for k in _gk if k not in _sig]
    check('★★★ 段缓存键覆盖**全部** `GRADE_*`（%d 个）' % len(_gk), not _missg,
          '漏: %s' % (_missg or '无'),
          '漏了就"拧了没反应"——`GRADE_SPLIT_ENABLE` 的注释恰恰承诺"一键回退这一段"')

    # ---- ⑥ 印相中灰配平必须**显式钉住**，不许吃 vendor 的 schema 默认 ----
    _p = presets.digested('Ultramax400沉褐', C)
    check('★★ `normalize_print_exposure` / `print_exposure_compensation` 显式钉为 True',
          bool(_p.enlarger.normalize_print_exposure)
          and bool(_p.enlarger.print_exposure_compensation),
          'norm=%s comp=%s' % (_p.enlarger.normalize_print_exposure,
                               _p.enlarger.print_exposure_compensation),
          '它是整张落点的命门；吃默认值 ⇒ 换 vendor 版本会**静默位移一档亮度**')

    # ---- ⑧ 可叠加的轴覆盖（`{"add": Δ}`）：数据自洽 + 组合行为正确 ----
    #   ★ 10-08：`targets.scene_engine` 原来用 `out.update(blk)` ⇒ **后匹配的轴赢、不叠加**。
    #     而 `span=平` 与 `back=逆光` 实测是**两个相加的因子** ⇒ 必须能叠。已加 `{"add": Δ}` 约定。
    _d = targets.load()
    _se = _d.get('_scene_engine') or {}
    _star = _se.get('*') or {}
    _bad_add = []
    for _k, _blk in _se.items():
        if not isinstance(_blk, dict):
            continue
        for _kk, _vv in _blk.items():
            if isinstance(_vv, dict) and 'add' in _vv:
                _b = _star.get(_kk)
                if not isinstance(_b, (int, float)) or isinstance(_b, bool):
                    _bad_add.append('%s.%s（基准 %r）' % (_k, _kk, _b))
    check('★★★ 每个 `{"add": Δ}` 轴覆盖的键在 `"*"` 层里都有**数值基准**',
          not _bad_add, '有问题: %s' % (_bad_add or '无'),
          '加法需要一个数值底。没有底 ⇒ 该 add 会被静默忽略（"写了没反应"），'
          '所以必须静态钉住：**要叠加的键，先在 `"*"` 里给基准值**')
    _GN = 'print_render.density_curves_morph.gamma_factor'
    _g1 = targets.scene_engine({'span': '平', 'exp': None, 'back': None, 'overwhite': None},
                               stock='Ultramax400沉褐', cfg=C).get(_GN)
    _g2 = targets.scene_engine({'span': None, 'exp': None, 'back': None, 'overwhite': None},
                               stock='Ultramax400沉褐', cfg=C).get(_GN)
    check('★★ 只用 `span=平` 时确实命中（其余轴为 None 也不受影响）',
          _g1 is not None and _g2 is not None and abs(float(_g1) - float(_g2)) > 1e-9,
          'span=平 → %s ／ 无 span → %s' % (_g1, _g2),
          '这是"逐场景参数真的接线了"的最小证据')
    # ★ **证明叠加真的生效**：临时往 `_scene_engine` 里塞一个 `back=正逆光` 的 `{"add": Δ}`，
    #   断言"两轴同时命中 = 两轴各自之和"。跑完还原（与 `t_scene` 注入 `_scene` 同一手法）。
    #   ⚠ 不这么做的话，"支持叠加"就只是注释里的一句话 —— 本项目最恨"说了没接上"。
    _inj = {'print_render.density_curves_morph.gamma_factor': {'add': 0.07}}
    try:
        _se['back=正逆光'] = dict(_inj)
        _a = float(targets.scene_engine({'span': '平', 'exp': None, 'back': '正逆光',
                                         'overwhite': None}, stock='Ultramax400沉褐',
                                        cfg=C).get(_GN))
        _b = float(targets.scene_engine({'span': None, 'exp': None, 'back': '正逆光',
                                         'overwhite': None}, stock='Ultramax400沉褐',
                                        cfg=C).get(_GN))
    finally:
        _se.pop('back=正逆光', None)
    check('★★★ 两轴同时命中时**真的叠加**（注入 {"add":0.07} 验证，跑完还原）',
          abs(_a - (float(_g1) + 0.07)) < 1e-9 and abs(_b - (float(_g2) + 0.07)) < 1e-9,
          'span=平+正逆光 %.4f（应 %.4f）／ 只正逆光 %.4f（应 %.4f）'
          % (_a, float(_g1) + 0.07, _b, float(_g2) + 0.07),
          '`span=平` 与 `back=逆光` 实测是**相加的两个因子**（组内单调：正逆光+平 69.77 < '
          '正逆光+正常 74.62 < 正逆光+大 78.09）⇒ 原来的 `out.update()` 让后匹配的轴**盖掉**'
          '前面的 ⇒ 会少补一个因子。这条断言钉住"叠加不许退化成覆盖"')

    # ---- ⑦ config 真的进了 `_params_for` 的键（否则第一次调用者的 cfg 永久污染）----
    _v0 = float(presets.digested('Ultramax400沉褐', C).enlarger.m_filter_shift)
    _t0 = C.PRESET_FILTER_M_TRIM
    C.PRESET_FILTER_M_TRIM = _t0 + 3.0
    try:
        _v1 = float(presets.digested('Ultramax400沉褐', C).enlarger.m_filter_shift)
    finally:
        C.PRESET_FILTER_M_TRIM = _t0
        presets.clear_cache()
    check('★★★ 改 config 的 `PRESET_*` ⇒ 参数对象**立刻重建**（cfg 进了缓存键）',
          abs(_v1 - (_v0 + 3.0)) < 1e-6,
          'trim %.1f→%.1f 时 m_shift %.3f→%.3f' % (_t0, _t0 + 3.0, _v0, _v1),
          '老版本键里只有 name ⇒ 首次调用者的 config 永久污染缓存 ⇒ A/B 试验两边一样')


def t_knob_effect():
    r"""★★★★★ **每个 `GRADE_*` 键都必须真的能改变输出**（"拧了没反应"的自动检测）。

    为什么要有这一组：本项目最痛的一类 bug 是**"改了没反应"**，而它有两种形态：
      ① **读了但读不到**（键名写错 / 被靶遮住 / 被上游归一化掉）—— `t_config_keys` 只管存在性；
      ② **读了但没效果**（写进了一个被闭环抵消、或窗根本覆盖不到的字段）。
    形态② 极难靠读代码发现。10-08 我就造了一个：`GRADE_HI_NEUTRAL` 的窗设在 L* 88~100，
      而画面最亮的像素在 **L\* 86** ⇒ 各档强度**输出逐位相同**，读码完全看不出问题。

    ⇒ 做法：**扰动测试**。逐键换一个值，跑同一张图，断言输出变了。
      · 用**两张**合成图（不同内容），**任一**张上有效即算"活"——
        因为有些键只在特定条件下才起作用（如 `GRADE_DEEP_*` 要有暗部）。
      · 报"可疑死键"清单；**不**直接判 FAIL（避免误报把自检变成噪声），
        但如果可疑键超过阈值就红 —— 那说明有人批量加了没接线的键。
    """
    import numpy as np
    from . import config as C, grade

    # 两张合成图：① 纵向渐变 + 暖偏（各亮度段都非空）② 多色相 + 宽彩度
    yy, xx = np.mgrid[0:56, 0:56]
    g1 = np.stack([np.clip(0.10 + 0.80 * yy / 55.0, 0, 1),
                   np.clip(0.09 + 0.72 * yy / 55.0, 0, 1),
                   np.clip(0.08 + 0.66 * yy / 55.0, 0, 1)], -1)
    r = np.random.RandomState(7)
    g2 = np.clip(0.25 + 0.5 * (0.5 + 0.5 * np.cos(6.283 * xx / 56.0))[..., None]
                 * np.array([1.0, 0.75, 0.45]) + 0.06 * r.rand(56, 56, 3), 0, 1)
    IMGS = (g1, g2)

    def out_of(img):
        o, _ = grade.apply(img, C, stock='Ultramax400沉褐', scene=None, person=None)
        return np.asarray(o, np.float64)

    base = [out_of(im) for im in IMGS]
    keys = sorted(k for k in dir(C) if k.startswith('GRADE_'))
    # 每个键的"扰动值"：布尔翻转；数值按量级放大/缩小；不足则用附近值
    def alt(k, v):
        if isinstance(v, bool):
            return [not v]
        if isinstance(v, (int, float)):
            cand = []
            for m in (1.6, 0.4, 2.5):
                cand.append(type(v)(v * m) if v else type(v)(0.5 if isinstance(v, float) else 1))
            cand.append(v + (1.0 if isinstance(v, float) else 1))
            cand.append(v - (1.0 if isinstance(v, float) else 1))
            return [c for c in cand if c != v]
        return []

    dead, live, skipped = [], [], []
    for k in keys:
        v0 = getattr(C, k)
        tested = False
        try:
            for nv in alt(k, v0):
                try:
                    setattr(C, k, nv)
                    for i, im in enumerate(IMGS):
                        if float(np.max(np.abs(out_of(im) - base[i]))) > 1e-6:
                            tested = True
                            break
                    if tested:
                        break
                except Exception:                                       # noqa: BLE001
                    pass
        finally:
            setattr(C, k, v0)
        if tested:
            live.append(k)
        elif isinstance(v0, (bool, int, float)):
            dead.append(k)
        else:
            skipped.append(k)
    # 已知"条件性 / 被靶遮住 / 确认已死"的键 —— **每条都要写明理由**。
    # ★ 新冒出来的死键**不在此列** ⇒ 会红。这才是这一组的价值。
    _KNOWN = {
        'GRADE_SH_A': '被靶遮住：`_default.sh_abs` 覆盖全部 10 条预设（见 config 死值警告）',
        'GRADE_SH_B': '同上',
        'GRADE_HI_A': '被靶遮住：`_default.hi_abs` 覆盖全部 10 条预设',
        'GRADE_HI_B': '同上',
        'GRADE_SAT': '被靶遮住：预设自己写了 `sat`（鹿井 0.8528）⇒ `mix()` 优先读靶',
        'GRADE_PERSON_DL': '条件性：要在 `apply(person=...)` 传掩膜才生效（合成图没传）',
        'GRADE_PERSON_W': '条件性：同上',
        'GRADE_SPLIT_DAMP': '**确认已死**：包内零读点（只被 `_debug` 归档副本读）—— 见 config 死键清单',
        'GRADE_SPLIT_MID_LIMIT': '**确认已死**：全仓库零读点 —— 见 config 死键清单',
        'GRADE_SPLIT_W_REF': '**确认已死**：包内零读点（只被 `_debug` 读）—— 见 config 死键清单',
        'GRADE_BAND_SOFTMAX_HI': '条件性：**只在 `GRADE_BAND_SOFTMAX=True` 时才被读**'
                                 '（扰动测试一次只拧一个键 ⇒ 关着时拧它当然没效果）。'
                                 '主开关本身**是活的**（已被同一条测试证明）',
    }
    _new = [k for k in dead if k not in _KNOWN]
    check('★★★★★ **没有新出现的**"读了但没效果"的键（扰动测试：拧一下必须动）',
          not _new, '活 %d / 共 %d ｜ **新死键: %s** ｜ 已知条件性/被遮/已死 %d 个'
          % (len(live), len(live) + len(dead), ', '.join(_new) or '无', len(_KNOWN)),
          '本项目的头号痛点是"拧了没反应"。新死键 ⇒ 该键读了但没效果'
          '（被靶遮住 / 被归一化抵消 / 作用窗覆盖不到）。'
          '★ 10-08 实例：`GRADE_HI_NEUTRAL` 的窗设在 L* 88~100，而画面最亮像素在 **L\\* 86** '
          '⇒ 各档强度**输出逐位相同**，只读代码完全看不出 ⇒ 已删。'
          '★ 若确认某键是"条件性"的，把它连**理由**一起登记进 `_KNOWN`，别直接放宽判据。')
    check('★ 而且已知清单不许膨胀（>12 个 ⇒ 有人在用登记表掩盖死键）',
          len(_KNOWN) <= 12, '已知 %d 个' % len(_KNOWN))


def t_synth_transfer():
    r"""★★★★★ **合成测试图的传递函数**（内容无关的回归基准）。

    为什么要有这一组：项目里**全部测量都在不可控的照片上做**，而"两组不同照片的分布差"
    **无法归因到渲染**（10-08 夜栽了五次）。合成测试图把"我们的变换"变成一**条可读的曲线**，
    且**真实照片从此只用于验证、不再用于拟合**。
    ⇒ 这一组同时是 **10-09 三个硬缺陷的回归锁**：
        ① **出口没有白**：旧曲线输入 L*100 → 输出 **88.7**（纯白被压到 RGB≈220）
        ② **暗部过冲**：旧曲线输入 L*20 → 输出 **10.8**（先压暗再交叉）
        ③ **中调鼓包**：旧曲线输入 45~55 抬 **+17**
      修法（已落 `targets._scene_engine["*"]`）：`lightness_compression` 0.7→**0.95**、
      `gamma_factor_slow` 1.3→**0.85**。实测：L100 88.7→**95.2**、L20 10.8→**20.2**、曲线单调。
    ⚠ 本组**不含逐场景覆盖**（用 `scene_engine(None, ...)` 的全局 `"*"` 层）——
      传递函数应当在**基线**上量，逐场景覆盖是"基线之上的条件修正"。
    """
    import numpy as np
    from . import color, grade, presets, targets

    W, H = 768, 900

    def s2l(c):
        c = np.asarray(c, np.float64)
        return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

    ch = np.zeros((H, W, 3), np.float64)
    ch[0:300] = np.linspace(0.0, 1.0, W)[None, :, None]              # ① 灰阶斜坡
    for i in range(16):
        ch[300:450, int(i * W / 16):int((i + 1) * W / 16)] = i / 15.0  # ② 16 级灰块
    for i in range(12):                                              # ③ 12 档肤色块
        hh = np.radians(55.0)
        lab = np.array([[[30.0 + i * 5.0, 20.0 * np.cos(hh), 20.0 * np.sin(hh)]]], np.float64)
        ch[450:620, int(i * W / 12):int((i + 1) * W / 12)] = np.clip(color.from_lab(lab)[0, 0], 0, 1)
    for i in range(12):                                              # ④ 色相环
        hh = np.radians(i * 30.0)
        lab = np.array([[[60.0, 35.0 * np.cos(hh), 35.0 * np.sin(hh)]]], np.float64)
        ch[620:750, int(i * W / 12):int((i + 1) * W / 12)] = np.clip(color.from_lab(lab)[0, 0], 0, 1)
    ch[750:900, 0:W // 3] = 0.0                                      # ⑤ 黑白灰
    ch[750:900, W // 3:2 * W // 3] = 0.5
    ch[750:900, 2 * W // 3:] = 1.0

    ov = targets.scene_engine(None, stock='Ultramax400沉褐', cfg=C)
    e = presets.render(s2l(ch), 'Ultramax400沉褐', C, overrides=ov)
    lab = color.to_lab(e)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    mm = dict(Lm=float(np.median(L)), am=float(np.median(a)), bm=float(np.median(b)))
    tg = targets.for_stock('Ultramax400沉褐', None)
    L2, a2, b2, _i = grade.mix(L, a, b, tg, C, mm, person=None)
    a3, b3, _s = grade.split(L2, a2, b2, tg, C, mm)
    lab2 = color.to_lab(np.clip(grade.gamut(L2, a3, b3), 0, 1))
    Lo, ao, bo = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    LABI = color.to_lab(np.ascontiguousarray(ch))
    Li, ai, bi = LABI[..., 0], LABI[..., 1], LABI[..., 2]

    def ramp(v):
        x = int(np.clip(v / 100.0 * (W - 1), 0, W - 1))
        return float(np.median(Lo[0:300, max(0, x - 2):x + 3]))
    L100, L50, L20 = ramp(100), ramp(50), ramp(20)
    mid = (slice(760, 890), slice(W // 3 + 8, 2 * W // 3 - 8))
    midb = float(np.median(bo[mid]))
    sk = []
    for i in range(12):
        sl = (slice(460, 610), slice(int(i * W / 12) + 10, int((i + 1) * W / 12) - 10))
        ci = float(np.hypot(np.median(ai[sl]), np.median(bi[sl])))
        co = float(np.hypot(np.median(ao[sl]), np.median(bo[sl])))
        ho = float(np.degrees(np.arctan2(np.median(bo[sl]), np.median(ao[sl]))) % 360)
        sk.append((float(np.median(Li[sl])), ci, co, ho))
    s50 = min(sk, key=lambda t: abs(t[0] - 50.0))
    skinC = s50[2] / max(s50[1], 1e-6)
    hh = [t[3] for t in sk if t[3] < 200]
    hsp = max(hh) - min(hh)
    xs = [int(v / 100.0 * (W - 1)) for v in range(0, 101, 5)]
    ys = [float(np.median(Lo[0:300, max(0, x - 2):x + 3])) for x in xs]
    bad_mono = [i for i in range(1, len(ys)) if ys[i] < ys[i - 1] - 0.05]

    check('★★★★★ 合成图 ①：**输入纯白 L*100 的输出必须 >= 94**（"出口没有白"的回归锁）',
          L100 >= 94.0, '输入 L*100 -> 输出 L*%.1f' % L100,
          '旧曲线只有 **88.7**（纯白被压到 RGB≈220）=> 这就是"白衬衫是奶色"的根。'
          '修法 = `io.output_gamut_compress.lightness_compression` 0.7->0.95')
    check('★★★★★ 合成图 ②：**暗部不许过冲**（输入 L*20 的输出须在 16~24）',
          16.0 <= L20 <= 24.0, '输入 L*20 -> 输出 L*%.1f（离 20 有 %+.1f）' % (L20, L20 - 20.0),
          '旧曲线是 **10.8**（-9.2 的过冲）=> 暗部先被压暗再交叉。'
          '修法 = `print_render.density_curves_morph.gamma_factor_slow` 1.3->0.85')
    check('★★★★ 合成图 ③：灰阶传递函数**单调无回折**',
          not bad_mono, '21 点采样，回折处 %s' % (bad_mono or '无'),
          '曲线回折 = 某些亮度区间"越亮越暗"，是影调映射的硬伤')
    check('★★★ 合成图 ④：中调抬升钉住（现状 %+.1f，已知偏大但无依据定论）' % (L50 - 50.0),
          abs(L50 - 67.7) <= 2.0, '输入 L*50 -> 输出 L*%.1f' % L50,
          '配方说"暗部上提"，但 +14 是否过头**没有依据** => 只钉住、不改')
    check('★★★ 合成图 ⑤：**灰阶偏蓝**钉住（现状 b*%+.1f，已知未解——来自上游）' % midb,
          abs(midb - (-6.1)) <= 2.0, '中灰块输出 b* = %+.1f' % midb,
          '实测：黑/中灰/白的输出 b* **全是负的** => 整条灰阶偏蓝。'
          '扫过 `sat`/`white_level`/`gamma`：**没有单一旋钮能修**（来自胶片/相纸/扫描链）')
    check('★★★ 合成图 ⑥：**肤色彩度倍率**钉住（现状 x%.2f，已知未解——来自上游）' % skinC,
          abs(skinC - 1.64) <= 0.2, '肤色块(C*20) -> 出 C*%.1f（x%.2f）' % (s50[2], skinC),
          '配方要求"肤色**低饱和**"，而管线把它放大 => 这是"肤色发黄发暗"的机器原因。'
          '⚠ 试过 `sat=0.60` 能压到 x1.15，但**同时把冷色压到 x0.64** —— '
          '而配方明确要求"蓝色**增**饱和" => **`sat` 不是对的那根杠杆**。'
          '真正的修法在上游（胶片/相纸/扫描的彩度），要单独设计')
    check('★★ 合成图 ⑦：肤色色相随明度的漂移钉住（现状 %.0f°，已知未解）' % hsp,
          hsp <= 45.0, '12 档肤色块输出的色相跨度 = %.1f°' % hsp,
          '同一个 Lab 色相 55° 的肤色块，**只改明度**，输出色相漂 %.0f° => '
          '一张脸的亮处与暗处会是两种颜色。这是"肤色难看"的另一半原因' % hsp)


def _main():
    groups = [
        ('胶片风格：9 条预设', t_presets),
        ('胶片风格：换一条真的换画面', t_preset_differs),
        ('二次调色：分色 + 混色（L2/L3）', t_grade),
        ('直方图（LR 画法：亮度 + RGB 叠加 + 5 个区）', t_hist),
        ('靶按预设分组', t_targets),
        ('可调键：config 里真的接上了（防"假旋钮"）', t_config_keys),
        ('场景判据：四轴 / 只在线性域判过曝 / 覆盖只加不减', t_scene),
        ('契约：颜色层在引擎之后 + 名字不认得要报错', t_contract),
        ('段缓存：同参数命中、换风格不命中', t_cache),
        ('服务：路由只剩该有的那几条', t_routes),
        ('★★★★★ 删层纪律：影调层 / 肤色层 / 认人认脸 **真的删了**', t_dropped_layers),
        ('★ 「人在哪」+ 光位：**只用低开销那条** / 判不出要弃权', t_person_light),
        ('★★★★★ 10-08 评审防复发：接线断了必须有人喊（缓存键/透传/报告同形）', t_review_1008),
        ('★★★★★ 每个 GRADE_* 键都要真能改变输出（扰动测试，防"拧了没反应"）', t_knob_effect),
        ('★★★★★ 合成测试图的传递函数（内容无关的回归基准：出口白/暗部不过冲/曲线单调）',
         t_synth_transfer),
        ('★★★★★ 连续调制 `{"by":…}` + **光位不驱动影调**（10-09）', t_by_modulation),
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
    #      ★★★ 09-30：**原来这条自检验的就是"我们写进去的那个名字"**（`agx_particle_area_um2`）
    #      ⇒ 写进去、读出来、对上了 ⇒ **假绿**，整整一轮"调颗粒到靶"是空的。
    #      ⇒ 现在**必须验 vendor 真正读的那个名字**（源码 `model/grain.py` 里的
    #        `grain.particle_area_um2`），并断言带 `agx_` 的死属性**根本不该存在**。
    #      （"拿同一个数验两边"是验不出"没人读它"的 —— 见技能 §135。）
    p = presets.digested(_PRESET)
    d = presets.load_raw(_PRESET)
    check('颗粒真落到 vendor 实读的字段（particle_*，不是 agx_particle_*）',
          abs(float(p.film_render.grain.particle_area_um2)
              - float(d['grain']['particle_area_um2'])) < 1e-9,
          '%.3f vs %.3f' % (p.film_render.grain.particle_area_um2,
                            d['grain']['particle_area_um2']),
          '颗粒字段对不上 ⇒ 有人改了 vendor 的字段名（0.3.2 叫 agx_particle_*、0.3.4 叫 particle_*）'
          ' —— 换 vendor 版本时 `presets._apply` 和这里要一起改')
    check('颗粒不许再有 agx_ 死属性（防回退到"写了没人读"）',
          not hasattr(p.film_render.grain, 'agx_particle_area_um2')
          and abs(float(p.film_render.grain.particle_scale[0])
                  - float(d['grain']['particle_scale'][0])) < 1e-9,
          'agx_ 残留=%s ｜ scale %.2f vs %.2f'
          % (hasattr(p.film_render.grain, 'agx_particle_area_um2'),
             p.film_render.grain.particle_scale[0], d['grain']['particle_scale'][0]),
          '又按老版本名写了 ⇒ vendor 读不到、画面不动（`GrainParams` 没有 __slots__，不会报错）')
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
    #     `效果debug\_归档_2026-09\2026-09-29\调研与设计_光晕与白平衡.md`，再回来改这里。
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
    #   ★ 10-08 追加：params 里的 m shift = `JSON print_m_filter_shift` **+ `PRESET_FILTER_M_TRIM`**
    #     （印相品红底座补偿，见 `config.py` 同名常量与 `presets.py` 接线处的注释）。
    #     ⇒ 断言必须按「JSON 原值 + 剂量」比，否则那条会红 —— 红的是**断言的算法**，不是接线。
    _trim = float(getattr(C, 'PRESET_FILTER_M_TRIM', 0.0))
    _wb, _wbb = [], []
    for n in ns:
        _q = presets.digested(n)
        _sim = presets.load_raw(n)['simulation']
        _wb.append((round(float(_q.enlarger.y_filter_shift), 3),
                    round(float(_q.enlarger.m_filter_shift), 3)))
        _wbb.append((round(float(_sim['print_y_filter_shift']), 3),
                     round(float(_sim['print_m_filter_shift']) + _trim, 3)))
    check('★ 整张冷暖基准落在印相滤片上（Y/M **真进 params**，不是假旋钮）', _wb == _wbb,
          ' / '.join('%s y=%.1f m=%.1f' % (n[4:] if len(n) > 4 else n, w[0], w[1])
                     for n, w in zip(ns, _wb))[:160],
          'JSON 写了但 params 里不是那个数 ⇒ 又是一组"改了没反应"的假旋钮；'
          '注意 m 端要比 `JSON + PRESET_FILTER_M_TRIM`（底座补偿）')
    check('★ 而且它**不是常量**（10 条的冷暖基准不全同）', len(set(_wb)) >= 2,
          '%d 种组合' % len(set(_wb)),
          '全同 ⇒ "逐预设的冷暖基准"是句空话；改冷暖请改 `simulation.print_y_filter_shift`'
          '（映射见 §`_note_load_raw`），别去动 `load_raw` 那三个死键')
    # ★★★★ 10-08 晚：配平基准**换源** —— 从"一对硬编码常数"改成**引擎数据库逐条查**。
    #   为什么换：`neutral` 的语义（作者 README）是「让 18% 灰在最终印相里完全中性的起始设置」，
    #   而引擎按 **(相纸, 放大机光源, 底片)** 三维查表。原来关掉 DB、把 public 0.3.2 的
    #   `fujifilm_pro_400h` 那一对发给**全部 10 条**（见 `config.PRESET_NEUTRAL_FROM_DB` 的注释）。
    #   实测代价：C200 三条差 −27(M)/−57(Y) ⇒ 它的 18% 灰印出来 **b* +30.0（偏黄）**；
    #   Ektar 差 −18/−11 ⇒ **a* +12.7**。换 DB 后 C200 的 18% 灰 = a* −0.8 / b* +1.4。
    #   ⚠ 各预设的 shift 已同步重表成「旧总量 − 新中性(DB)」⇒ **总滤片量不变、画面不变**
    #     （实测 10 条 18% 灰 Δa*/Δb*/ΔL 全部 = 0.00）。⇒ 这是换参数化，不是改观感。
    _dbn, _neu = [], []
    for n in ns:
        _q = presets.digested(n)
        _dbn.append(bool(_q.settings.neutral_print_filters_from_database))
        _neu.append((round(float(_q.enlarger.m_filter_neutral), 3),
                     round(float(_q.enlarger.y_filter_neutral), 3)))
    check('★★ 配平基准**走引擎数据库**（逐 相纸/光源/底片，不是一对硬编码常数）',
          all(_dbn), '%d/%d 条开着' % (sum(_dbn), len(_dbn)),
          '关掉就会退回"一对常数发给 10 条不同底片" —— 那正是 18% 灰偏黄 30 格的原因')
    check('★★ 而且它**逐卷真的不同**（证明是查表，不是又被换成了常数）',
          len(set(_neu)) >= 2,
          ' / '.join('%s M%.2f Y%.2f' % (n, v[0], v[1]) for n, v in list(zip(ns, _neu))[:4]),
          '全同 ⇒ 数据库那条路没生效（或 vendor 换版后 DB 没跟着换）')
    # 绝对锚：钉死一对已知的 DB 值，防「Y/M 对调」——自证式检查看不出来（09-23 踩过一次）。
    #   读 DB 的顺序是 `c_filter, m_filter, y_filter`（vendor `apply_database_neutral_print_filters`）
    #   ⇒ 0.3.4 库里 `kodak_portra_endura / TH-KG3 / kodak_portra_400` = [0, 51.568(M), 52.534(Y)]。
    _anchor = [v for n, v in zip(ns, _neu)
               if presets.load_raw(n)['simulation']['film_stock'] == 'kodak_portra_400'
               and presets.load_raw(n)['simulation']['print_paper'] == 'kodak_portra_endura']
    check('★★ 而且 Y/M **不许对调**（绝对锚：portra400+endura ⇒ M 51.568 / Y 52.534）',
          bool(_anchor) and all(abs(v[0] - 51.568) < 5e-3 and abs(v[1] - 52.534) < 5e-3
                                for v in _anchor),
          '%s' % (_anchor[:1] if _anchor else '没找到 portra400+endura 的预设'),
          '对调了整张偏色，而"逐卷不同"那条看不出来')

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
# 2. 删层纪律：影调层 / 肤色层 / 认人认脸 **真的删了**（不是关开关）
# ---------------------------------------------------------------------------

def t_dropped_layers():
    r"""★★★ 09-29 新分支 `drop-tone-and-skin`：三样东西**从代码里删掉**了。

    这组是"删干净了没有"的钉子 —— 防的是"关了开关但代码还在、谁哪天又把它接回去"：
      · **模块级**：`tone` / `face` / `facegain` / `region` **必须 import 不进来**；
      · **config**：`TONE_*` / `FACE_*` / `SKIN_*` / `PERSON_*` / `GRADE_SKIN_*` /
        `SCENE_BACK_*` / `SCENE_SHOT_*` 一个都不许剩，`STYLE` / `GRADE_SCOPE` /
        `GRADE_REGION_SCOPE` / `PRESET_MID_SHIFT` / `TARGET_*_FLOOR_L` 同样；
      · **pipeline 的代码里**不许再出现那几层的名字；
      · **服务路由 `/styles`** 必须没了。
    """
    import importlib
    import inspect

    for m in ('tone', 'face', 'facegain', 'region'):
        try:
            importlib.import_module('svFilm.%s' % m)
            _ok = False
        except ModuleNotFoundError:
            _ok = True
        check('★ 模块 `svFilm.%s` 已经删掉（import 不进来）' % m, _ok, '',
              '还在 ⇒ 影调层 / 肤色层 / 认人那套随时会被接回链上')

    # ★ 09-29 晚：`PERSON_*` / `SCENE_BACK_*` **不在这个黑名单里** ——
    #   它们是**重新加回来**的光位判据（只用低开销的"人在哪"，见 `t_person_light`）。
    _bad = [k for k in dir(C) if k.startswith(('TONE_', 'FACE_', 'SKIN_',
                                               'GRADE_SKIN_', 'ANCHOR_', 'SCENE_SHOT_'))]
    _bad += [k for k in ('STYLE', 'PRESET_MID_SHIFT', 'TARGET_BLACK_FLOOR_L',
                         'TARGET_HI_FLOOR_L', 'GRADE_REGION_SCOPE', 'GRADE_SCOPE')
             if hasattr(C, k)]
    check('★★ config 里那几个键一个都不剩（`PERSON_*` / `SCENE_BACK_*` 例外：光位加回来了）',
          not _bad, '还剩: %s' % (_bad or '无'),
          '留着就是"没人读的开关"—— 改了没反应（本项目第 4 类坑）')

    _src = '\n'.join(l.split('#')[0] for l in
                     inspect.getsource(pipeline.run_from).splitlines())
    _hit = [n for n in ('tone', 'facegain', 'FACE_STEP_ENABLE', 'TONE_ENABLE',
                        'GRADE_SCOPE', 'render_with_face', 'skin_gap') if n in _src]
    check('★★ pipeline **代码里**不再出现那几层的名字', not _hit,
          '命中: %s' % (_hit or '无'),
          '又接回去了 ⇒ 这不是"删掉"，是"关开关"')

    _ssrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'service.py'), encoding='utf-8').read()
    check('★ 服务里 `/styles` 路由确实删了', "u.path == '/styles'" not in _ssrc)


# ---------------------------------------------------------------------------
# 2.5 「人在哪」+ 光位：**只用低开销那条**（09-29 晚加回来）
# ---------------------------------------------------------------------------

def t_person_light():
    r"""光位（`back` 轴）—— 判据是 **09-27 的 v3（分块亮度场 + 全相对量）**，
    输入只用 `person.py` 那个 **~0.3 s 的粗"人在哪"**（**不跑人脸检测**）。

    这组钉三件事：
      ① **只用低开销那条** —— 源码里不许出现人脸检测 / birefnet / onnxruntime；
      ② **判据对得上** —— 逆光判得出逆光族、平光判得出面光；
      ③ ★★ **判不动时必须弃权**（`None`），**不许硬给一个"顺平光"** —— 那是 v1 的老毛病，
         扰动关一测就翻（v3 的立身之本）。
    """
    import inspect
    import re as _re

    from . import person as _pmod
    from . import scene

    # ① ★★★ 只做"人在哪"：源码里（**剥掉注释与文档串之后**）不许有人脸检测 / birefnet
    _raw = inspect.getsource(_pmod)
    _code = _re.sub(r'r?"""[\s\S]*?"""', '', _raw)
    _code = '\n'.join(l.split('#')[0] for l in _code.splitlines())
    _bad = [n for n in ('FaceDetector', 'yunet', 'YuNet', 'birefnet', 'onnxruntime',
                        'face_skin', 'landmark', 'FaceLandmarker') if n in _code]
    check('★★★ `person.py` **只做人在哪**：不许有人脸检测 / birefnet / onnxruntime',
          not _bad, '命中: %s' % (_bad or '无'),
          '又把高开销那条接回来了 ⇒ 不要那个开销')
    check('★ 低开销那条 = mediapipe `selfie_multiclass`（输入固定 256×256）',
          'selfie_multiclass' in _code and 'SEG_SIDE = 256' in _code,
          '模型 = %s' % os.path.basename(_pmod.SELFIE))
    check('★ 模型文件在仓库里（不靠 pip / 不靠下载）', os.path.exists(_pmod.SELFIE),
          _pmod.SELFIE)

    # ② 判据本身（合成图，不需要 mediapipe —— 直接喂假掩膜）
    H, W = 120, 160
    d = np.full((H, W, 3), 0.25)
    d[: H // 3] = 0.95                                  # 上方一大片亮 ⇒ 光在背后
    person = np.zeros((H, W), np.float64)
    person[H // 2:, W // 3: 2 * W // 3] = 1.0
    L = color.to_lab(d)[..., 0]

    _b1, _r1 = scene._light_position(d, L, person, C)
    check('★★ 上亮下暗、主体在下方 ⇒ 判成**逆光族**（正逆光 / 侧逆光）',
          _b1 in ('正逆光', '侧逆光'),
          'back=%s  E_tb=%+.1f E_bg=%+.1f clip_bg_blk=%.2f' % (
              _b1, _r1['E_tb'], _r1['E_bg'], _r1['clip_blk_off']),
          '判成面光 ⇒ 判据没接上（v3 的第一条触发条件是 E_tb ≤ −TS）')

    d2 = np.full((H, W, 3), 0.2)
    p2 = np.zeros((H, W), np.float64)
    p2[H // 3: 2 * H // 3, W // 3: 2 * W // 3] = 1.0
    d2[H // 3: 2 * H // 3, W // 3: 2 * W // 3] = 0.95   # 最亮的就是主体自己
    _b2, _r2 = scene._light_position(d2, color.to_lab(d2)[..., 0], p2, C)
    check('★ 最亮的就是主体自己 ⇒ **面光 / 顺平光**', _b2 == '面光/顺平光',
          'back=%s  E_tb=%+.1f E_bg=%+.1f spike=%.1f' % (
              _b2, _r2['E_tb'], _r2['E_bg'], _r2['spike']))

    # ③ ★★★ 弃权必须是合法输出（v1 最大的毛病就是"无论如何都硬给一个答案"）
    _b3, _r3 = scene._light_position(d, L, None, C)
    check('★★★ 没有"人在哪" ⇒ **弃权**（返回 None，不许硬判面光）',
          _b3 is None and _r3['why'], 'why=%s' % _r3['why'],
          '硬判 ⇒ 下游会照着一个假标签去改参数')
    tiny = np.zeros((H, W), np.float64)
    tiny[0:8, 0:8] = 1.0
    _b4, _r4 = scene._light_position(d, L, tiny, C)
    check('★★ 主体太小 ⇒ 同样**弃权**（`SCENE_BACK_MIN_SUB_PX` 兜着）',
          _b4 is None and _r4['why'], '主体 %d px · why=%s' % (int(tiny.sum()), _r4['why']))
    dark = np.zeros((H, W, 3))                          # 全黑 ⇒ 极端亮度，判不动
    _b5, _r5 = scene._light_position(dark, color.to_lab(dark)[..., 0], person, C)
    check('★★ 整张太暗 ⇒ **弃权**（`extreme_luma`）', _b5 is None, 'why=%s' % _r5['why'])
    allp = np.ones((H, W), np.float64)                  # 掩膜盖满 ⇒ 没有背景可比
    _b6, _r6 = scene._light_position(d, L, allp, C)
    check('★★ 只有主体、没有背景 ⇒ **弃权**（`no_subject`）',
          _b6 is None, 'why=%s' % _r6['why'])

    # ④ `classify`：不给"人在哪" ⇒ 只有光位那一位是 '-'，其余照常
    sc = scene.classify(d, C)
    check('★★ 不给"人在哪" ⇒ 只有光位弃权（`-`），exp / span / overwhite 照常',
          sc['back'] is None and sc['key'].split('|')[3] == '-' and sc['exp'] and sc['span'],
          'key=%s' % sc['key'])
    sc2 = scene.classify(d, C, person=person)
    check('★ 给了"人在哪" ⇒ key 里光位那一位不再是 `-`',
          sc2['key'].split('|')[3] != '-', 'key=%s' % sc2['key'])
    _need = ('E_lr', 'E_tb', 'E_span', 'E_bg', 'spike', 'z_span', 'clip_pct',
             'clip_blk_off', 'hot_blk_off', 'has_spike_src', 'person_pct', 'cx_diff', 'why')
    _miss = [k for k in _need if k not in sc2['raw']]
    check('★ 光位判据的 raw 里带着**全部诊断量**（事后标定阈值用）', not _miss,
          '缺: %s' % (_miss or '无'))

# ---------------------------------------------------------------------------
# 3. 契约
# ---------------------------------------------------------------------------



def t_hist():
    """直方图工具（`hist.py`）：LR 那四条曲线 + 5 个区 + 裁切三角。

    ⚠ 这一组是给"以后**看直方图不看数字**"兜底的 —— 图要是画错了，
      照着一张错的图做判断，比数字错还危险。
    """
    from . import hist

    # ① 四条曲线：长度 256、0~1、无 NaN
    c, clip = hist.channels(_gray_img(seed=31))
    check('★ 四条直方图曲线（亮度 + R/G/B）：各 256 bin、落在 0~1、无 NaN',
          len(c) == 4 and all(len(x) == 256 for x in c)
          and all(np.all(np.isfinite(x)) and x.min() >= 0.0 and x.max() <= 1.0 + 1e-9 for x in c),
          '亮度最大 %.2f' % float(np.max(c[0])))
    check('亮度那条跟 RGB 三条不是同一条（真算了，不是复制）',
          float(np.max(np.abs(c[0] - c[1]))) > 0.01)
    # ★ 纵轴必须是**固定口径**（满格 = 单档占画面 YMAX_RATIO），不是按每张图自己的最大值
    g = np.asarray(_gray_img(seed=31), np.float64)
    ys = hist._codes(hist._luma(g) / 255.0).ravel()   # ★ 用实现自己的口径复算（四舍五入）
    cnt = np.bincount(ys, minlength=256).astype(np.float64)
    want = (cnt.max() / (cnt.sum() * hist.YMAX_RATIO)) ** hist.Y_GAMMA
    got = float(np.max(hist.channels(g)[0][0]))      # [0]=四条曲线, [0][0]=亮度那条
    check('★★ 纵轴是**固定口径**（满格 = 单档占画面 %.0f%%），不是每张自己归一'
          % (hist.YMAX_RATIO * 100),
          abs(got - min(want, 1.0)) < 1e-6, '实到 %.4f  应到 %.4f' % (got, min(want, 1.0)),
          '改成"按每张图自己的最大值归一"的话每张刻度都不一样 ⇒ 跨图不可比')
    check('★ 同一张图喂两次峰值完全一样（口径稳定）',
          abs(float(np.max(hist.channels(g)[0][0])) - got) < 1e-12)

    # ② 端点（裁切）：全黑 ⇒ 阴影端亮；全白 ⇒ 高光端亮；中间灰 ⇒ 都不亮
    bk = hist.channels(np.zeros((32, 32, 3)))[1]
    wh = hist.channels(np.ones((32, 32, 3)))[1]
    gy = hist.channels(np.full((32, 32, 3), 0.5))[1]
    check('★ 全黑 ⇒ 阴影端要亮（LR 里那个左上的三角）', bk['lo']['any'] > 0.9,
          '%.3f' % bk['lo']['any'])
    check('★ 全白 ⇒ 高光端要亮（右上的三角）', wh['hi']['any'] > 0.9, '%.3f' % wh['hi']['any'])
    check('★ 全白 ⇒ 三个通道都到端点（`which` 应为 R+G+B）', wh['hi']['which'] == 'R+G+B',
          wh['hi']['which'])
    check('中间灰 ⇒ 两个三角都不亮',
          gy['lo']['any'] < 5e-4 and gy['hi']['any'] < 5e-4,
          '%.4f / %.4f' % (gy['lo']['any'], gy['hi']['any']))

    # ③ ★★★ 09-30 修的漏洞：老判据数 `luma == 255`（= 三通道同时 255 = 纯白），
    #   会把"只有单通道到端点"整类漏掉。实测代价：鹿井参照的蓝通道裁了 0.227%，
    #   老判据报 0.000%（三角根本不亮）。这一条就是防它回退。
    _b = np.zeros((16, 16, 3)); _b[..., 0] = 0.6; _b[..., 1] = 0.6; _b[..., 2] = 1.0
    _sb = hist.clip_stats(_b)
    _old = float((hist._codes(hist._luma(_b) / 255.0) == 255).mean())
    check('★★ 只有蓝通道到端点时必须报出来（老"纯白"判据会漏成 0）',
          _sb['hi']['which'] == 'B' and _sb['hi']['any'] > 0.9 and _old == 0.0,
          'which=%s any=%.2f  老判据=%.4f' % (_sb['hi']['which'], _sb['hi']['any'], _old))
    check('★ 端点的颜色 = 被裁通道的混色（R+G→黄、三个→白、没有→灰）',
          hist._clip_tint('R+G') == (255, 255, 151)
          and hist._clip_tint('R+G+B') == (255, 255, 255)
          and hist._clip_tint('') == hist.TRI_OFF,
          '%s / %s' % (hist._clip_tint('R+G'), hist._clip_tint('R+G+B')))
    check('★ 档位是四舍五入（与 `color.display_to_u8` 同口径，不是截断）',
          int(hist._codes(np.array([254.6 / 255.0]))[0]) == 255
          and int(hist._codes(np.array([254.4 / 255.0]))[0]) == 254)

    # ④ 出图：尺寸对、别炸
    im = hist.draw(_gray_img(seed=33), w=400, h=160, title='自检')
    check('画得出来、尺寸对', im.size == (400, 160), str(im.size))
    im_w = hist.waveform(_gray_img(seed=33), w=400, h=180, title='自检')
    check('★ 波形图也画得出来（横轴＝画面左右，纵轴＝亮度）', im_w.size == (400, 180),
          str(im_w.size))
    pn = hist.panel(_gray_img(seed=35), w=400, title='自检')
    check('缩略图 + 直方图 一体也画得出来', pn.size[0] == 400 and pn.size[1] > 160, str(pn.size))
    pnw = hist.panel(_gray_img(seed=35), w=400, title='自检', mode='wave')
    check('★ 一体版换波形图也画得出来（`mode="wave"`）', pnw.size[0] == 400 and pnw.size[1] > 160,
          str(pnw.size))
    st = hist.stack([(None, '参照', c, clip)], w=400)
    check('★ 只喂曲线（画"一组片的平均直方图"）也画得出来', st.size == (400, 240), str(st.size))

    # ⑤ 命令行入口别断（`python -m svFilm.hist` 以后要常用）
    check('命令行入口在（`python -m svFilm.hist`）', callable(hist._main))


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
    check('★★ `grade.apply` 接受 stock 参数（靶能传下去）',
          'stock' in __import__('inspect').signature(
              __import__('svFilm.grade', fromlist=['x']).apply).parameters)




def t_by_modulation():
    r"""★★★ 10-09 新增：**连续调制** `{"by": <程度量>, "delta": Δ}` + **"光位不驱动影调"**这条设计约束。

    这一组守两件性质相反的事：
      ① **新机制对**：`{"by": d, "delta": Δ}` 必须**连续**（d=0/0.25/0.5/1 线性）、
         必须能落到**预设基线**（典型用法就是调光晕幅度这种"预设里的值"）、
         且**拿不到程度量 ⇒ 什么都不加**（弃权 = 安全侧，这条最关键）。
      ② **旧毛病不许回来**：`_scene_engine` 里 **`back=` 轴块不许再出现影调参数**
         （`print_render.density_curves_morph.*`）。为什么钉这条 —— 10-09 体检结论：
         "离散档 + 动态参数 = 阶跃"，而且它解释力几乎全来自 `E_bg` **同义反复**
         （φ 见 `效果debug/2026-10-09/1009_光位分组体系_体检与设计.html` 与技能 §194）。
         ⚠ 这条断言是**故意的**：以后谁想再把光位接回影调，会当场红，逼他先看那段证据。
    """
    from . import config as _C
    from . import presets as _PR
    from . import scene as _S
    from . import targets

    d = targets.load()
    se = d.get('_scene_engine') or {}

    # ---- ① 静态：`{"by": …}` 的名字必须是 `scene.DEGREES` 里的 ----
    bad_name = []
    for k, blk in se.items():
        if k.startswith('_') or not isinstance(blk, dict):
            continue
        for kk, vv in blk.items():
            if isinstance(vv, dict) and 'by' in vv and vv.get('by') not in _S.DEGREES:
                bad_name.append('%s→%s' % (k, vv.get('by')))
    check('★★ `targets.json` 里每个 `{"by": …}` 用的都是 `scene.DEGREES` 里的名字',
          not bad_name, '非法: %s' % (bad_name or '无'),
          '名字写错 ⇒ `_deg()` 恒返回 0 ⇒ 调制**静默失效**（这正是本项目最怕的那类坑）')

    # ---- ② ★★★ 设计约束：光位不许驱动影调 ----
    off = []
    for k, blk in se.items():
        if not (isinstance(k, str) and k.startswith('back=') and isinstance(blk, dict)):
            continue
        off += ['%s.%s' % (k, kk) for kk in blk if 'density_curves_morph' in kk]
    check('★★★ `back=`（光位）轴块里**没有任何影调参数**（"光位不驱动影调"）',
          not off, '命中: %s' % (off or '无'),
          '光位→影调 已被证伪（同义反复 + 阶跃，§194）⇒ 要加回来先读那段证据')

    # ---- ③ 机制：注入一条 `{"by": ...}`（调**预设基线**、**逐通道** Δ），跑完还原 ----
    #   ⚠ 两个坑都是这条自检自己抓出来的：
    #     ① `back=*` 只在 **`back` 判出来**时命中（`_v is None ⇒ continue`）⇒
    #        **要"无条件"调制必须写进 `"*"` 层**，不能写 `back=*`；
    #     ② 光晕幅度在引擎里是 **0~1 的元组**（预设 JSON 的 8/2/0 是 ×100 过的）
    #        ⇒ Δ 必须**逐通道给列表**，给标量会默默改掉 R:G:B 比例。
    key = 'film_render.halation.halation_strength'
    base = list(getattr(*_PR._walk(_PR._params_for('Ultramax400沉褐', _C), key)))
    delta = [0.04, 0.02, 0.0]
    star = d.setdefault('_scene_engine', {}).setdefault('*', {})
    assert key not in star, '预计 `"*"` 层里没有这个键'
    star[key] = {'by': 'deg_back', 'delta': list(delta)}

    def _run(deg, hit=True):
        sc = {'back': ('侧光' if hit else None),
              'raw': {'light': ({'deg_back': deg} if deg is not None else {})}}
        return targets.scene_engine(sc, stock='Ultramax400沉褐', cfg=_C).get(key)

    try:
        got = [_run(x) for x in (0.0, 0.25, 0.5, 1.0)]
        want = [[b + dd * x for b, dd in zip(base, delta)] for x in (0.0, 0.25, 0.5, 1.0)]
        check('★★★ `{"by": …}` **连续**：d=0/0.25/0.5/1 ⇒ 逐通道 = 基线 + d·Δ',
              all(all(abs(a - b) < 1e-9 for a, b in zip(g, w)) for g, w in zip(got, want)),
              '基线 %s ⇒ %s' % (base, [list(g) for g in got]),
              '不是线性 ⇒ 没做成"连续插值"（还是阶跃）')
        check('★★ `"*"` 层里的 `{"by": …}` **无条件生效**（光位判不出来时也照调）',
              _run(1.0, hit=False) == want[-1],
              'back=None 时 %s（应 %s）' % (_run(1.0, hit=False), want[-1]),
              '写进 `back=*` 而不是 `"*"` ⇒ 判不出光位的那批片会漏掉（轴为 None 整轴跳过）')
        check('★★★ 程度量**拿不到** ⇒ 什么都不加（弃权 = 安全侧，绝不瞎加）',
              _run(None) == base, '%s（应 == 基线 %s）' % (_run(None), base),
              '缺程度量还硬加 ⇒ 判不出光位时会乱改画面')
        try:
            star[key] = {'by': 'deg_back', 'delta': 0.04}      # ← 故意给标量
            _run(1.0)
            _bad = False
        except TypeError:
            _bad = True
        check('★★ 基准是"逐通道列表"时给**标量 Δ** ⇒ **当场抛**（不许默默改 R:G:B 比例）',
              _bad, '标量 Δ 被 %s' % ('拒绝' if _bad else '接受了'),
              '静默接受 ⇒ 悄悄改掉三通道比例（自检第一版就踩了这个）')
        # 老契约不许破：`{"add"}` 的基准必须在前面的层里（否则忽略，不半生效）
        star.pop(key, None)
        se2 = d['_scene_engine'].setdefault('span=平', {})
        se2[key] = {'add': list(delta)}
        try:
            check('★★ 老契约不变：`{"add"}` 的基准不在前面的层里 ⇒ **忽略**（不半生效）',
                  targets.scene_engine({'span': '平', 'back': None, 'raw': {}},
                                       stock='Ultramax400沉褐', cfg=_C).get(key) is None,
                  '（`{"add"}` 只在基准已存在时生效 —— 与 `{"by"}` 不同，见 docstring）')
        finally:
            se2.pop(key, None)
    finally:
        star.pop(key, None)


def t_grade():
    """二次调色（`grade.py`）：关掉必须逐位恒等；开着必须按量到的方向动。"""
    from . import grade

    disp = _gray_img(seed=23)
    # ★★ 开关必须**成对还原**：这一组会临时改 `GRADE_ENABLE` / `GRADE_SAT` / `GRADE_SPLIT_ENABLE`，
    #   漏还原 ⇒ 后面几组看到的默认值全错位（09-29 那 4 条老账里有 3 条就是这么来的）。
    _ge0 = bool(getattr(C, 'GRADE_ENABLE', True))
    # ★★★ 09-29：**彩度守恒那条契约的前提是 `GRADE_SAT=1.0`**（"只重新分配、不改总量"）。
    #   本会话把默认改成 0.72（落地 A）⇒ 契约**前提变了**、旧写法没显式设它 ⇒ 必然假红
    #   （实测 33.65 → 23.42 = −30.4%，正好是 ×0.72 的量级，**不是 bug**）。
    #   ⇒ 按纪律：**在这里显式把它设成 1.0 来跑**（跑完还原），判据数字一个字不改。
    _sat0 = float(getattr(C, 'GRADE_SAT', 1.0))
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
    #   ★★ 10-08：`GRADE_DEEP_A/B` 是**风格化的暗部推色**（故意的、偏离靶的），
    #     与"这一层往靶收"是两件事 ⇒ 测这条契约时把它**显式设成 0**（跑完还原），
    #     与上面 `GRADE_SAT=1.0` 的处理同一个道理（契约的前提要显式摆出来）。
    #     ⚠ 老代码不用设，因为那根旋钮当时是**死的**（被闭环吃掉）；10-08 修活之后必须显式。
    _dk0 = (float(getattr(C, 'GRADE_DEEP_A', 0.0)), float(getattr(C, 'GRADE_DEEP_B', 0.0)))
    C.GRADE_DEEP_A, C.GRADE_DEEP_B = 0.0, 0.0
    try:
        _on0, _ = grade.apply(disp, C)
        a1, b1 = _split(_on0)
    finally:
        C.GRADE_DEEP_A, C.GRADE_DEEP_B = _dk0
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
    #     ⇒ 整张彩度虚涨 46%、画面发飘（脸崩了）。
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

    # ⑤ ★ 分色那一段的**单段开关**：关掉它 ⇒ 分色不动、**混色照跑**
    _rgb = np.random.RandomState(77)
    _c = np.stack([_rgb.rand(96, 96) * 0.55 + 0.22 for _ in range(3)], -1)
    _c[..., 1] = np.clip(_c[..., 1] * 1.05, 0, 1)
    C.GRADE_SPLIT_ENABLE = False
    _o_nosp, _i_nosp = grade.apply(_c, C)
    C.GRADE_SPLIT_ENABLE = True
    _o_sp, _ = grade.apply(_c, C)
    C.GRADE_ENABLE = _ge0              # ★ 开关成对还原（漏了它 ⇒ 后面几组全部错位变红）
    check('★★ `GRADE_SPLIT_ENABLE=False` ⇒ 分色那一段真的不生效（混色照跑）',
          _i_nosp.get('split_model') == 'off'
          and float(np.max(np.abs(_o_nosp - _o_sp))) > 1e-6,
          '关掉分色 vs 开着分色 最大差 %.3g ；报告 split_model=%s'
          % (float(np.max(np.abs(_o_nosp - _o_sp))), _i_nosp.get('split_model')),
          '两者完全一样 ⇒ 分色没被真的跳过（那个单段开关是摆着看的）')

    # ⑥ ★★ 09-30：**靶可以是「内容曲线」** —— 目标随画面自身的色偏查表，不再是常数。
    #   为什么：大师**自己那批图**的暗部 Δb 的 IQR 就有 4~8 格 ⇒ 固定点靶不可达。
    #   两条断言：
    #     · 配了 `_curves` 的预设 ⇒ `split_curve_used` 非空；没配 ⇒ 空（**向后兼容**）
    #     · 自变量真的在起作用 ⇒ 两张"整张 b*"不同的图，查出来的目标不同
    C.GRADE_ENABLE = True
    _o_nc, _i_nc = grade.apply(_c, C)                       # 不传 stock ⇒ 全局靶（无曲线）
    _o_wc, _i_wc = grade.apply(_c, C, stock='Portra400薄荷')  # 薄荷配了曲线
    check('★★ 没配曲线的路径 ⇒ `split_curve_used` 为空（向后兼容，仍走点靶）',
          not _i_nc.get('split_curve_used'),
          'curve_used=%r' % (_i_nc.get('split_curve_used'),))
    # ⚠ 09-30 深夜：`split_curve_used` **现在还会带五段曲线的 `z*` 键**（`_zone_curves`）——
    #   判"旧口径那几条"时必须**先滤掉 `z` 前缀**，否则条数永远对不上（自检当场红过）。
    _old_cv = lambda z: [k for k in (z.get('split_curve_used') or []) if not k.startswith('z')]
    _zs_cv = lambda z: [k for k in (z.get('split_curve_used') or []) if k.startswith('z')]
    check('★★ 配了曲线的预设 ⇒ 曲线靶被读到（薄荷 5 条：sh_b / hi_a / hi_b / md_a / md_b）',
          len(_old_cv(_i_wc)) == 5,
          'curve_used=%r  tgt_sh=%r' % (_i_wc.get('split_curve_used'), _i_wc.get('tgt_sh')))
    # ★★ 09-30 晚：`_cv_used` **按实际查到的报**（老写法只要配了 `_curves` 就一律报 4 条，
    #   某条缺了也照样报 ⇒ 排查"曲线到底生效没有"时会误判）。清风已删掉无信号的 sh_a/sh_b
    #   ⇒ 它只该报 hi_a/hi_b 两条。
    _o_qc, _i_qc = grade.apply(_c, C, stock='Pro400H清风')
    check('★ `_cv_used` 按实际查到的报（清风只剩 hi_a/hi_b ⇒ 2 条，不是"一律 4 条"）',
          len(_old_cv(_i_qc)) == 2,
          'curve_used=%r' % (_i_qc.get('split_curve_used'),),
          '仍报 4 条 ⇒ 又回到"缺了也不说"的老口径')

    # 自变量在起作用：造两张整张 b* 差很远的图（一暖一冷），曲线给的目标必须不同
    _warm = np.stack([np.full((64, 64), 0.62), np.full((64, 64), 0.56),
                      np.full((64, 64), 0.30)], -1)
    _cool = np.stack([np.full((64, 64), 0.35), np.full((64, 64), 0.55),
                      np.full((64, 64), 0.68)], -1)
    _o1, _i1 = grade.apply(_warm, C, stock='Portra400薄荷')
    _o2, _i2 = grade.apply(_cool, C, stock='Portra400薄荷')
    _d1 = float((_i1.get('tgt_sh') or [0, 0])[1])
    _d2 = float((_i2.get('tgt_sh') or [0, 0])[1])
    # 方向：**画面越黄（整张 b* 越高）⇒ 目标越蓝（Δb 越负）** —— 两位大师实测都单调。
    # ★★★ 09-30 深夜新增：**五段靶走的是"按画面查的曲线"**（`_zone_curves`）——
    #   没有它，五段靶会**静默退回固定值**（画面看不出报错，只是"那一段又不动了"）。
    check('★★★ 五段靶走"按画面查的曲线"（`_zone_curves` 命中 6 条 z*）',
          len(_zs_cv(_i_wc)) == 6,
          'z* 命中=%r' % (_zs_cv(_i_wc),),
          '一条都没命中 ⇒ `_zone_curves` 没读到（字段名/路径错了），五段靶静默退回固定值')
    check('★★★ 曲线靶真的随画面走：暖画面 vs 冷画面 ⇒ 暗部 Δb 的目标不同（且暖画面更蓝）',
          _d2 > _d1 + 0.2,
          '暖画面目标 %+.2f ｜ 冷画面目标 %+.2f ｜ x=%s / %s'
          % (_d1, _d2, _i1.get('curve_x'), _i2.get('curve_x')),
          '两者相同 ⇒ 曲线没被用上（自变量没接进去）')
    # ★★ 09-30 晚：**中间调也走曲线**（`md_a/md_b`）—— 它是"唯一该动态却还固定"的那两条
    #   （同一把尺子下：鹿井 中Δa IQR 2.47 相关 +0.72、中Δb IQR 4.42 相关 +0.60）。
    #   方向 = **递增**（画面越黄 ⇒ 中调相对越黄），与暗/亮带的递减相反 ⇒ 这里单独钉。
    _m1 = float((_i1.get('tgt_mid') or [0, 0])[1])
    _m2 = float((_i2.get('tgt_mid') or [0, 0])[1])
    check('★★★ 中调靶也随画面走（`md_b`）：暖画面的中调目标 > 冷画面（方向与暗带相反）',
          _m1 > _m2 + 0.2,
          '暖画面中调目标 %+.2f ｜ 冷画面 %+.2f' % (_m1, _m2),
          '两者相同 ⇒ md 曲线没接进去；方向反了 ⇒ 单调化方向写错了（中调是**递增**）')
    # ★★★ 09-30 晚：**约束带 3 → 5**（预设配了 `zone_abs` 时）。
    #   为什么钉：扩带是"眼睛看到的那两段（阴影/次高光）终于有人管"的关键，
    #   而它**只在配了 `zone_abs` 时生效** —— 一旦哪天 targets.json 的字段名写错，
    #   `split()` 会**静默退回三带**、画面照旧 ⇒ 必须当场红。
    check('★★★ 配了 `zone_abs` 的预设 ⇒ 分色走**五个带**（并且报告里带 5 段位移）',
          int(_i_wc.get('split_zones') or 0) == 5 and len(_i_wc.get('d_zones') or []) == 5,
          'split_zones=%r  d_zones=%r' % (_i_wc.get('split_zones'), _i_wc.get('d_zones')),
          '还是 3 ⇒ `zone_abs` 没读到（字段名/JSON 路径写错了，会静默退回老行为）')
    import numpy as _np5
    from . import targets as _T5
    _img5 = _np5.stack([_np5.full((64, 64), 0.35)] * 3, -1)
    _img5[..., 0] = 0.60
    _o5, _i5 = grade.apply(_img5, C, stock='C200透明')      # 这条预设**没有** `zone_abs`
    check('★★★ 没配 `zone_abs` 的预设 ⇒ **逐位走老三带**（向后兼容）',
          int(_i5.get('split_zones') or 0) == 0 and len(_i5.get('d_zones') or []) == 3,
          'split_zones=%r  len(d_zones)=%d' % (_i5.get('split_zones'),
                                               len(_i5.get('d_zones') or [])),
          '没配也走了 5 带 ⇒ 兼容分支坏了')
    # ★★ 09-30 晚：**「人物区域整体提亮」（`person_dl`）** —— 两条硬规矩。
    #   ⚠ 判据必须用**差分对照**：grade 本来就一直在动（混色+分色），
    #     拿"输出 vs 输入"比会把 grade 的正常动作算进来（第一版就是这么写错的，当场红）。
    #     正确做法 = 固定同一条链，**只翻 `person_dl`**，看差在哪。
    from . import targets as _TGT              # ⚠ 本函数作用域里只有 `_T`，没有 `targets`
    import numpy as _np2
    _img = _np2.stack([_np2.full((96, 96), 0.35)] * 3, -1)
    _img[..., 0] = 0.62                                       # 偏红的一块，保证有彩度
    _pmask = _np2.zeros((96, 96))
    _pmask[24:72, 24:72] = 1.0                                # 中间一块当"人"
    _tg0 = _TGT.for_stock('Portra400薄荷')
    _orig = _TGT.for_stock

    def _run(pdl, pz):
        _TGT.for_stock = lambda name, s2=None, _t=dict(_tg0, person_dl=pdl): dict(
            _t, stock=name, _scene_hits=[])
        try:
            return grade.apply(_img, C, stock='Portra400薄荷', person=pz)
        finally:
            _TGT.for_stock = _orig

    _o0, _i0 = _run(8.0, None)          # 有 person_dl、但**没有掩膜**
    _o1, _i1 = _run(0.0, None)          # 关掉 person_dl
    _a0, _ia = _run(8.0, _pmask)        # 有掩膜
    _a1, _ia1 = _run(0.0, _pmask)
    _L = lambda z: color.to_lab(_np2.asarray(z, _np2.float64))[..., 0]        # noqa: E731
    _d_nomask = float(_np2.max(_np2.abs(_np2.asarray(_o0) - _np2.asarray(_o1))))
    check('★★★ `person_dl` 写了、但 `person=None` ⇒ **一点不生效**（弃权，不许退化成全局）',
          _i0.get('person_on') is False and _d_nomask < 1e-9,
          'person_on=%r ｜ 翻 person_dl 后的最大差 %.3g' % (_i0.get('person_on'), _d_nomask),
          '没掩膜也动了 ⇒ 又变成"整张提亮"，早晚出割裂')
    _dk = _L(_a0) - _L(_a1)             # 只翻 person_dl 造成的 L* 差
    _d_in = float(_np2.median(_dk[30:66, 30:66]))
    _d_out = float(_np2.max(_np2.abs(_dk[0:12, 0:12])))
    check('★★★ 给了掩膜 ⇒ 只有**人身上**被抬起来、掩膜外**一点不动**',
          _ia.get('person_on') is True and _d_in > 3.0 and _d_out < 1e-6,
          '人身上 ΔL*=%.2f ｜ 掩膜外 ΔL*=%.3g ｜ person_pct=%r'
          % (_d_in, _d_out, _ia.get('person_pct')),
          '掩膜外也动 ⇒ 掩膜没乘上（会污染背景）；人身上没动 ⇒ 没接进去')
    C.GRADE_ENABLE = _ge0


def t_config_keys():
    """★★ 09-26：**可调参数只在 `config.py`** —— 防"假旋钮"。

    症状：代码里写的是 `getattr(cfg, 'X', 字面默认)`，而 `config.py` 里**没有 X 这个键**
    ⇒ 永远取那个字面默认，**改源码里那个常量毫无反应**。
    """
    from . import grade

    # ★ 影调层删掉后，原来那四个"假旋钮"（TARGET_BLACK_FLOOR_L / TARGET_HI_FLOOR_L /
    #   TONE_REL …）随之消失；这里改成钉**颜色层真正在用的**那些键。
    for k in ('GRADE_ENABLE', 'GRADE_SAT', 'GRADE_SPLIT_ENABLE', 'GRADE_SPLIT_LIMIT',
              'GRADE_SPLIT_ITERS', 'GRADE_SPLIT_NODES', 'GRADE_SPLIT_RANGE',
              'GRADE_C_MIN', 'GRADE_SH_A', 'GRADE_SH_B', 'GRADE_HI_A', 'GRADE_HI_B',
              'GRADE_DEEP_A', 'GRADE_DEEP_B', 'SPEK_PE_SHIFT',
              'GRADE_PERSON_W', 'GRADE_PERSON_DL'):
        check('config.%s 这个键真的在（不是 getattr 的裸默认）' % k,
              hasattr(C, k), '当前 %r' % getattr(C, k, None),
              '缺它 ⇒ 代码里 `getattr(cfg, …)` 永远取那个字面默认，'
              '改源码里同名常量**不会有任何反应**')

    # ★★ 光"键在"不够 —— 必须**真的读它**（键在但没人读 = 换了个人继续摆着看）
    _rng = np.random.RandomState(71)
    img = np.stack([_rng.rand(96, 96) * 0.6 + 0.2 for _ in range(3)], -1)
    img[..., 2] = np.clip(img[..., 2] * 0.75, 0, 1)          # 偏黄 ⇒ 有真彩度
    _l0 = C.GRADE_SPLIT_LIMIT
    try:
        C.GRADE_SPLIT_LIMIT = 0.0
        _n0, _ = grade.apply(img, C)
        C.GRADE_SPLIT_LIMIT = 12.0
        _n1, _ = grade.apply(img, C)
    finally:
        C.GRADE_SPLIT_LIMIT = _l0
    _dd = float(np.max(np.abs(_n0 - _n1)))
    check('★★ 改 config.GRADE_SPLIT_LIMIT **真的**改变分色的补量（键在、且被读到）',
          _dd > 1e-6, '限 0 与限 12 的最大差 %.3g' % _dd,
          '改了没反应 ⇒ 这个键还是"摆着看的"')



def t_scene():
    """场景判据（`scene.py`）：三轴都到得了 · key 带版本 · 源头过曝只在线性域判 ·
    `targets._scene` 覆盖**只加不减**、不给场景就一个字段不动。"""
    from . import scene, targets

    # ① 三轴都在，key 带版本号，且**同一张图判两次一样**
    sc = scene.classify(_gray_img(seed=91))
    check('场景判据给出三根轴 + 版本化的 key（改了判据就要作废缓存）',
          all(k in sc for k in scene.AXES) and sc['key'].startswith('v%d|' % scene.VERSION),
          'key = %s' % sc['key'])
    check('判据稳定：同一张图判两次 key 完全一样',
          scene.classify(_gray_img(seed=91))['key'] == sc['key'])
    check('★ 靠"人"的三根轴里：`back`（光位）**加回来了**，`shot` / `face` **仍然没有**',
          'back' in scene.AXES and not [k for k in ('shot', 'face') if k in scene.AXES],
          'AXES = %s' % (scene.AXES,),
          '09-29：光位用 `person.py`（~0.3 s 的粗"人在哪"）重建；景别 / 脸可见度随认人整套删了、没回来')

    # ② 每根轴的每一档都要**到得了**（落不到的分档 = 死的专家）
    #   ⚠⚠ 给的是**显示域**的值，而分档线在 **Lab L\*** 上 —— 中间隔着 sRGB 解码 + 立方根。
    #     "看起来中等"的 0.35 显示域其实是 L*≈38（落在「亮」档），在这儿红过两次。
    #     实测对应：0.087→L*7.3 / 0.19→L*20.1 / 0.95→L*95.6。
    dark = np.full((32, 32, 3), 0.087)
    mid = np.full((32, 32, 3), 0.19)
    bright = np.full((32, 32, 3), 0.95)
    flat = np.full((32, 32, 3), 0.35)
    wide = np.zeros((32, 32, 3)); wide[16:] = 1.0
    e = {scene.classify(x)['exp'] for x in (dark, mid, bright)}
    s = {scene.classify(x)['span'] for x in (flat, wide)}
    check('「曝光」三档都到得了（暗/正常/亮）', len(e) == 3, str(sorted(e)))
    check('「光比」至少两档到得了（平/大）', len(s) >= 2, str(sorted(s)))

    # ③ ★★ 源头过曝**只在线性域**判：显示域一样、线性不一样 ⇒ 结论必须不同
    #   ⚠ 判据是「通道最大值 ≥ 白点」，**不是**"三通道同时贴顶"（那样写永远不会响，
    #     因为显示域的"白"只是 sRGB 把 1.0 以上压到 255 的假象，见 `config` 里那段）。
    a = scene.classify(flat, lin=np.full((32, 32, 3), 0.5))['overwhite']
    b = scene.classify(flat, lin=np.full((32, 32, 3), 1.2))['overwhite']
    check('★★ 源头过曝在线性域判得出来（显示域一样、线性不一样 ⇒ 结论不同）',
          a is False and b is True, '线性 0.5 → %s ；线性 1.2 → %s' % (a, b),
          '只看显示域的话这两张"一样" ⇒ 这一轴就废了（成片那边早被重渲染压过了）')
    check('不给线性图 ⇒ overwhite = None（不硬猜）',
          scene.classify(flat)['overwhite'] is None)

    # ④ 场景覆盖：默认**一个字段都不动**；命中才盖、且只盖命中的那些
    base = targets.for_stock(_PRESET, None)
    _sc = {'key': 'v3|x', 'exp': '暗', 'span': '平', 'overwhite': False}
    same = targets.for_stock(_PRESET, _sc)
    check('★★ 没有 `_scene` 段时，给不给场景一个字段都不变（默认逐位不变）',
          same.get('sat') == base.get('sat') and same['_scene_hits'] == []
          and same.get('span') == base.get('span'),
          'hits=%s' % same['_scene_hits'])

    d = targets.load()
    _save = d.get('_scene')
    try:
        d['_scene'] = {'overwhite=过曝': {'sat': 1.23}, 'exp=*': {'split_limit': 9.9}}
        _hit = dict(_sc); _hit['overwhite'] = True
        tt = targets.for_stock(_PRESET, _hit)
        check('★★ 命中场景覆盖时字段真的盖上，且只盖命中的那些',
              tt['sat'] == 1.23 and tt['split_limit'] == 9.9
              and tt['_scene_hits'] == ['exp=*', 'overwhite=过曝'],
              'hits=%s' % tt['_scene_hits'])
        tt2 = targets.for_stock(_PRESET, _sc)
        check('★ 没命中的档不盖（overwhite=False ⇒ 不掉进 overwhite=过曝）',
              tt2['sat'] == base.get('sat') and tt2.get('split_limit') == 9.9,
              'sat %s（应还是 %s）' % (tt2['sat'], base.get('sat')))
    finally:
        if _save is None:
            d.pop('_scene', None)
        else:
            d['_scene'] = _save


def t_contract():
    # ① 颜色层跑在胶片引擎**之后**
    calls = []

    def _mark(tag, fn):
        def _w(*a, **k):
            if not calls or calls[-1] != tag:
                calls.append(tag)
            return fn(*a, **k)
        return _w

    from . import grade as _g
    _p0, _g0 = presets.render, _g.apply
    presets.render, _g.apply = _mark('presets', _p0), _mark('grade', _g0)
    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=11)), stock=_PRESET)
    finally:
        presets.render, _g.apply = _p0, _g0
    check('★★ 颜色层跑在胶片引擎**之后**（成片是显示域，只能事后染色、不能调曝光）',
          calls[:2] == ['presets', 'grade'], '调用序: %s' % calls[:4],
          '顺序反了 = 在引擎之前调亮度，控制不了成片亮度（实测三条档只拉开 8.8 / 该 29.4）')

    # ② 不认得的名字**当场报错**
    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=13)), stock='根本没有这一条')
        ok = False
    except KeyError:
        ok = True
    check('胶片风格名字不认得 ⇒ 当场报错（不是静默出一张别的）', ok,
          '', '"名字不认得就静默走默认"是本项目最阴的一类坑，出现过三次')

    # ③ 报告要说实话（进去多少 / 出来多少，能自查，不用读图）
    r = pipeline.run_from(_mk_sample(_gray_img(seed=17)), stock=_PRESET)
    check('★ 报告里带 `grade`（颜色层的量）与 `scene`（判出来的场景）',
          isinstance(r.report.get('grade'), dict) and 'scene' in r.report,
          '键: %s' % sorted(r.report.keys()))
    if _GRADE_ON:
        g = r.report['grade']
        check('★ 颜色层开着 ⇒ 报告里带着"动了多少"（能自查，不用读图）',
              bool(g.get('applied')) and 'L50_out' in g and 'd_sh' in g and 'c_gain' in g,
              'L50_out=%s' % g.get('L50_out'))

    # ④ 关掉颜色层 ⇒ 出图**就是纯引擎输出**（别的层不许偷偷动像素）
    _ge_k = bool(getattr(C, 'GRADE_ENABLE', True))
    C.GRADE_ENABLE = False
    try:
        _r_pure = pipeline.run_from(_mk_sample(_gray_img(seed=17)), stock=_PRESET)
    finally:
        C.GRADE_ENABLE = _ge_k
    check('★★ 关掉颜色层 ⇒ 出图**就是纯引擎输出**（不许偷偷动像素）',
          _pure_engine_same(_r_pure, _mk_sample(_gray_img(seed=17))),
          '与"只跑 presets.render"的差超出了引擎自身噪声',
          '关了这一层却还在改像素 ⇒ 开关没接对')

    check('成片没有 NaN / Inf 且在 [0,1]',
          bool(np.all(np.isfinite(r.disp))) and float(r.disp.min()) >= 0.0
          and float(r.disp.max()) <= 1.0)


def t_cache():
    s = _mk_sample(_gray_img(seed=19))
    c = pipeline.StageCache()
    r1 = pipeline.run_from(s, stock=_PRESET, cache=c)
    r2 = pipeline.run_from(s, stock=_PRESET, cache=c)
    check('同参数 ⇒ 命中缓存（切回来不用重跑）',
          bool(r2.report['stage_cache']['hit']))
    r4 = pipeline.run_from(s, stock='C200青蓝', cache=c)
    check('★ 换胶片风格 ⇒ **不**命中', not r4.report['stage_cache']['hit'])

    # ★★ 09-29：**开关本身必须进键** —— 它改的是出图本身（跑不跑颜色层）。
    #   不进键 ⇒ 常驻进程里改了一开，仍会命中"没开"的旧缓存 ⇒ "拧了没反应"（第 4 类）。
    _ge0 = bool(C.GRADE_ENABLE)
    try:
        C.GRADE_ENABLE = not _ge0
        r5 = pipeline.run_from(s, stock=_PRESET, cache=c)
    finally:
        C.GRADE_ENABLE = _ge0
    check('★★ 换颜色层开关 ⇒ **不**命中（否则就是"拧了没反应"）',
          not r5.report['stage_cache']['hit'],
          '开关 %s → %s，命中=%s' % (_ge0, not _ge0, r5.report['stage_cache']['hit']))

    # ★★★ 09-29：**靶 / 场景覆盖（引擎 overrides）也必须进键**。
    #   以前这两项因为 `_sc` 在缓存键那段还没定义而**永远是 None**（被 except 吞掉）
    #   ⇒ 改了 `targets.json`（靶或 `_scene_engine`）仍命中旧缓存 = "拧了没反应"第 4 类。
    from . import targets as _TS
    _d = _TS.load()

    _sat_save = (_d.get(_PRESET) or {}).get('sat')
    try:
        _d.setdefault(_PRESET, {})
        _d[_PRESET]['sat'] = 0.999 if _sat_save != 0.999 else 0.998
        r6 = pipeline.run_from(s, stock=_PRESET, cache=c)
    finally:
        if _sat_save is None:
            _d.get(_PRESET, {}).pop('sat', None)
        else:
            _d[_PRESET]['sat'] = _sat_save
    check('★★ 改这条预设的**靶**（`sat`）⇒ **不**命中（靶真的进了键）',
          not r6.report['stage_cache']['hit'],
          '命中=%s' % r6.report['stage_cache']['hit'],
          '不进键 ⇒ 改了靶仍命中旧缓存 = "拧了没反应"')

    _se_save = _d.get('_scene_engine')
    try:
        _star = dict((_se_save or {}).get('*') or {})
        _gf = 'print_render.density_curves_morph.gamma_factor'
        _star[_gf] = float(_star.get(_gf, 1.10)) + 0.05
        _d['_scene_engine'] = {'*': _star}
        r7 = pipeline.run_from(s, stock=_PRESET, cache=c)
    finally:
        if _se_save is None:
            _d.pop('_scene_engine', None)
        else:
            _d['_scene_engine'] = _se_save
    check('★★ 改 `_scene_engine`（引擎覆盖）⇒ **不**命中（overrides 真的进了键）',
          not r7.report['stage_cache']['hit'],
          '命中=%s' % r7.report['stage_cache']['hit'],
          '不进键 ⇒ 改了场景覆盖仍命中旧缓存 = "拧了没反应"第 4 类')


def t_routes():
    from . import service as svc
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'service.py'), encoding='utf-8').read()
    for p in ('/stocks', '/render', '/export', '/load', '/base', '/health'):
        check('路由 %s 在' % p, ("u.path == '%s'" % p) in src)
    for p in ('/params', '/bases', '/papers', '/styles'):
        check('★ 已经删掉的路由 %s 确实不在了' % p, ("u.path == '%s'" % p) not in src,
              '', '滑杆 / 相纸 / 成色基准都随新边界删了，接口不该还留着')
    check('默认端口还是 8765（台子那边钉着）', svc.DEFAULT_PORT == 8765, str(svc.DEFAULT_PORT))


if __name__ == '__main__':
    sys.exit(_main())
