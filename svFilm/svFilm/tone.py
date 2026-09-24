# -*- coding: utf-8 -*-
r"""曝光风格 —— **svFilm 唯一负责的那件事**（09-23 SV 重新划边界）。

边界：svFilm = 曝光 + 影调；胶片感（负片 / 相纸 / 颗粒 / 柔光 / 光晕）归 spektrafilm。
所以这一层只做两件事，**都在线性域、都在 spektrafilm 之前**：

  ① **落点** —— 整张有多亮（中位 L\*）。做法：乘一个曝光倍数。
  ② **跨度** —— 从暗到亮铺多开（黑位 L5）。做法：绕落点做一条 gamma（只改反差，不动落点）。

★ 为什么必须在 spektrafilm **之前**（不能之后）：
   曝光是"给胶片多少光"，是"曝光 → 显影 → 密度"这条因果链最前面的那一环。
   在之后改 = 对已经印好的照片再翻拍调增益，物理上不存在"冲好了再曝光"；
   而且到显示域 + 8bit 就没有高光余量了（digitalFilm 切白 11~20% 就是这个根因）。

## ★★★ 三条风格的数值是**从大师真片量出来的**（不是拍脑袋）

素材：`E:\Debug_svStudio\_debug\analysis\master_resurvey.json`（1172 张大师成片的
Lab 分位，**排除「情书(电影截图)」28 张** ⇒ 1144 张）。

量法：按每张自己的**中位亮度 L50** 排序，取排序位置 **P30 / P50 / P70** 附近各 ~230 张，
量这批照片的 L5 / L25 / L50 / L75 / L95 的中位 ⇒ 就是下面这张表。
（不用 P20/P80：那两档是"真的偏亮/偏暗的片子"，做通用风格档太极端。）

| 档 | 黑位 L5 | L25 | 落点 L50 | L75 | 亮部 L95 | 中段反差 L75−L25 |
|---|---|---|---|---|---|---|
| 暗调 | 8.7 | 20.9 | **40.5** | 66.9 | 90.9 | 46.7 |
| 中性调 | 10.8 | 33.1 | **58.8** | 79.0 | 93.7 | 46.0 |
| 高长调 | 14.1 | 44.2 | **69.9** | 85.3 | 95.5 | 40.6 |

参考：鹿井 32 张 = L5 7.5 / L50 61.4 / L95 93.4（≈ 中性调，但黑位更低）。
"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C

_EPS = 1e-6


# ---------------------------------------------------------------------------
# 三条风格
# ---------------------------------------------------------------------------

STYLES = {
    '暗调': dict(
        mid_L=40.5, black_L=8.7, white_L=90.9,
        desc='整张压下来、暗部厚，适合逆光和傍晚',
    ),
    '中性调': dict(
        mid_L=58.8, black_L=10.8, white_L=93.7,
        desc='大师真片的中位水平，最稳的一条',
    ),
    '高长调': dict(
        mid_L=69.9, black_L=14.1, white_L=95.5,
        desc='整体亮、从暗到亮铺得开，通透明快',
    ),
}

DEFAULT = '中性调'
ORDER = ('高长调', '中性调', '暗调')


def names():
    return list(ORDER)


def has(name):
    return str(name) in STYLES


def get(name):
    """风格名 -> 那组靶值。名字不认得 ⇒ 回中性调（**不静默走"不动"**）。"""
    return STYLES.get(str(name)) or STYLES[DEFAULT]


# ---------------------------------------------------------------------------
# 落点补偿：spektrafilm 那一道会把亮度再搬一次
# ---------------------------------------------------------------------------
# 我们打的是**成片**的靶，但能动手的地方在 spektrafilm **之前** ⇒ 必须知道
# 「这一条预设会把中位亮度搬多少」。这个数由 `tools/calib_exposure.py` 跑出来
# （9 条预设 × 几张基准图，量成片 L50 − 输入 L50 的中位），写进 `config.PRESET_MID_SHIFT`。
# ⚠ 没有这一条 ⇒ 选同一档曝光风格、换胶片风格，画面亮度会跟着卷漂（9 条卷响应本来就不同）。
def mid_shift_of(preset_name, cfg=C):
    tbl = getattr(cfg, 'PRESET_MID_SHIFT', None) or {}
    return float(tbl.get(str(preset_name), 0.0))


# ---------------------------------------------------------------------------
# 量与解
# ---------------------------------------------------------------------------

def measure(lin):
    """量线性图的亮度分位。返回 (Y, {5:.., 50:.., 95:..})。"""
    Y = np.maximum(color.Y_of(np.asarray(lin, np.float64)), 0.0)
    p = color.pct_of(Y, (5.0, 50.0, 95.0))
    return Y, p


class ToneCurve:
    """单调分段线性曲线，定义在 **log2 亮度域**（log 域插值才不会把暗部压扁）。"""

    def __init__(self, t_in, t_out):
        t_in = np.maximum.accumulate(np.asarray(t_in, np.float64))
        t_out = np.maximum.accumulate(np.asarray(t_out, np.float64))
        # 严格递增：把重合的结点用极小量推开（合并会悄悄丢掉一段 ⇒ 曲线上少一个锚点）
        self.t_in = t_in + np.arange(t_in.shape[0]) * 1e-7
        self.t_out = t_out

    def __call__(self, y):
        return np.exp2(np.interp(_t(y), self.t_in, self.t_out))

    def gain(self, y):
        """输出/输入 比值。用于 RGB **同步**缩放（色相不动）。"""
        return self(y) / np.maximum(np.asarray(y, np.float64), _EPS)


def _t(y):
    """lin 亮度 -> log2 域（曲线的定义域）。"""
    return np.log2(np.maximum(np.asarray(y, np.float64), _EPS))


def ev_needed(lin, style=DEFAULT, cfg=C, preset=None):
    r"""整张的中位搬到这一档的靶要补几档（正值 = 要提亮）。

    给「脸锚点」用：锚点算的是**脸**要补几档，这里算的是**整张**要补几档，
    `pipeline` 拿这两个数合成最终曝光（风格定基准，脸做有限幅的修正）。
    ⚠ 一步解出、不用迭代：L\* 与 Y 一一对应，而 Y 随曝光线性缩放。
    """
    st = get(style)
    _, p = measure(lin)
    y50 = max(p[50.0], _EPS)
    Tm = float(np.clip(color.lin_of_L(float(st['mid_L']) - mid_shift_of(preset, cfg)), _EPS, None))
    return float(np.log2(Tm / y50))


def solve(lin, style=DEFAULT, cfg=C, preset=None, ev_bias=0.0):
    r"""解这一张要的曲线：把 **L5 / L50 / L95 三个分位**搬到这一档的靶。

    `ev_bias`：**额外**补的档数（由「脸锚点」算出来，见 `pipeline`）。
      正值 = 在风格靶的基础上再提一点（脸偏暗时），负值 = 收一点（脸已经够亮时）。

    为什么是三点而不是"曝光 + gamma"两个自由度：
      两条不同的片子暗部宽窄差别很大（实测有的 L5→L50 只差 20 个 L\*、有的差 50+），
      只用 gamma 去凑黑位 ⇒ 平的那张要 γ>2 才够，那是**拉伸噪声**，不是调影调。
      三点曲线在 log 域插值，**每个分位各归各的靶**，平的图也只是被温和地搬过去。

    单调性必须守住（不然曲线会翻折、暗部出现台阶）：
      输入的三点本来就是递增的，靶那三点也按 `黑 < 中 < 白` 夹过 ⇒ 曲线必然单调。
    """
    st = get(style)
    Y, p = measure(lin)
    y5, y50, y95 = max(p[5.0], _EPS), max(p[50.0], _EPS), max(p[95.0], _EPS)

    # 靶（打的是**成片**）⇒ 减掉这一条预设自己会搬的那一份
    mid_L = float(st['mid_L']) - mid_shift_of(preset, cfg)
    # ★ 脸锚点要的那点偏移在**线性域**加（EV→L\* 的换算随亮度变，在线性域乘才是准的）
    Tm = float(np.clip(color.lin_of_L(mid_L) * (2.0 ** float(ev_bias or 0.0)), _EPS, None))
    Tb = float(np.clip(color.lin_of_L(float(st['black_L'])), _EPS, None))
    Tw = float(np.clip(color.lin_of_L(float(st['white_L'])), _EPS, None))
    # 单调钳：靶必须 黑 < 中 < 白（留 2% 余量，别让三点粘在一起）
    Tb = min(Tb, Tm * 0.98)
    Tw = max(Tw, Tm * 1.02)

    curve = ToneCurve([_t(_EPS), _t(y5), _t(y50), _t(y95)],
                      [_t(_EPS), _t(Tb), _t(Tm), _t(Tw)])
    return dict(curve=curve, style=(str(style) if has(style) else DEFAULT),
                mid_L=mid_L, Tb=Tb, Tm=Tm, Tw=Tw,
                y5=y5, y50=y50, y95=y95,
                L5_in=float(color.L_of_lin(y5)),
                L50_in=float(color.L_of_lin(y50)),
                L95_in=float(color.L_of_lin(y95)))


def apply(lin, style=DEFAULT, cfg=C, preset=None, ev_bias=0.0):
    r"""lin 进 lin 出。返回 (lin_out, info)。

    逐像素的缩放系数**只由亮度 Y 算出、RGB 同步乘同一个数** ⇒ 色相不动、彩度关系不动
    （换 Lab 改 L* 会顺带改彩度，那是"动颜色"，不是这里该做的事）。
    """
    lin = np.asarray(lin, np.float64)
    s = solve(lin, style, cfg, preset=preset, ev_bias=ev_bias)
    cap = float(getattr(cfg, 'TONE_MAX_GAIN_EV', 3.0))

    Y = np.maximum(color.Y_of(lin), _EPS)
    g = np.clip(s['curve'].gain(Y), 2.0 ** -cap, 2.0 ** cap)   # 保险丝：一头太狠就是拉伸噪声
    out = lin * g[..., None]

    # 通道超 1 时朝亮度方向收彩度，**绝不硬裁**（硬裁会变色相）
    out = fit_gamut(out, color.Y_of(out))
    out = np.clip(out, 0.0, None)

    Y2 = np.maximum(color.Y_of(out), 0.0)
    p2 = color.pct_of(Y2, (5.0, 50.0, 95.0))
    s.pop('curve', None)
    s.update(
        applied=True,
        # 中位那一点的增益换成"几档"报出来（人话：这一张总共提/压了多少）
        # ⚠ 两个 `interp` 都在 **log2 域** ⇒ 差值本身就是 EV，别再除以线性值
        ev=float(np.median(np.log2(np.maximum(g, 1e-9)))),
        ev_mid=float(np.interp(
            _t(s['y50']), _t(np.array([_EPS, s['y5'], s['y50'], s['y95']])),
            np.array([_t(_EPS), _t(s['Tb']), _t(s['Tm']), _t(s['Tw'])])) - _t(s['y50'])),
        L5_out=float(color.L_of_lin(p2[5.0])),
        L50_out=float(color.L_of_lin(p2[50.0])),
        L95_out=float(color.L_of_lin(p2[95.0])),
    )
    return out, s


def fit_gamut(lin_rgb, Y=None):
    """线性域色域适配：通道超 1 时朝亮度方向收彩度，绝不硬裁（硬裁会变色相）。"""
    r = np.asarray(lin_rgb, np.float64)
    if Y is None:
        Y = color.Y_of(r)
    Y = np.clip(Y, 0.0, 0.999999)
    mx = r.max(axis=-1)
    over = mx > 1.0
    if not np.any(over):
        return r
    k = (mx - Y) / np.maximum(1.0 - Y, 1e-6)
    k = np.where(over, np.maximum(k, 1.0), 1.0)
    return Y[..., None] + (r - Y[..., None]) / k[..., None]



# ---------------------------------------------------------------------------
# 作用在**成片**上的三条档（`config.TONE_AFTER_ENGINE = True`）
# ---------------------------------------------------------------------------
# 和上面 `STYLES` 的区别：`STYLES` 打的是**绝对靶**（大师真片量出来的 L5/L50/L95），
# 只在"动作在引擎之前"时说得通；动作挪到引擎之后，改的都是**相对量** ——
# 把成片的 L5 / L50 / L95 各自往下搬多少。
#
# ★★ 数值是**量出来的**，不是拍的：拿 `_debug/analysis/master_resurvey.json` 里
#    **鹿井 32 张**（SV 的主参考）做**内容归一**对比 —— 比较"分位 − 中位"的形状：
#
#      | 形状 | L5−L50（黑位） | L95−L50（亮部） | L75−L25（中段） |
#      | 鹿井 32 张 | **−53.1** | **+31.8** | 51.9 |
#      | 我们（919 十张的底） | **−42.1** | **+31.1** | 50.4 |
#      | 差 | **−11.0** | **+0.7** | +1.5 |
#
#    ⇒ **亮部已经在位（差 0.7），不需要压高光；黑位浅了 11，要往下压。**
#      （5 位大师 708 张合起来是 −46.6 / +29.3，我们的亮部同样在带内、黑位同样偏浅。）
#    ⚠ 之前"压高光 + 提阴影"那套方向是**反的** —— 会把亮部压离鹿井、
#      同时把黑位抬得比鹿井更浅（就是"发灰发糊"的来源）。
#    ⚠ 为什么用**内容归一**（分位减中位）而不是绝对亮度：绝对亮度绑内容 + 绑曝光
#      （大师的 L50 61.4 是他自己的场景和他自己的曝光），直接对齐会"把所有片拽成同一灰"。
REL = {
    '高长调': dict(ev_down=0.00, hi_down=0.0, bl_down=6.0,
                 desc='黑位往鹿井带的下沿收（最多 6），中位/亮部不动'),
    '中性调': dict(ev_down=0.00, hi_down=0.0, bl_down=11.0,
                 desc='黑位收到鹿井 32 张的中位形状（最多 11）'),
    '暗调': dict(ev_down=0.20, hi_down=3.0, bl_down=14.0,
               desc='中位压下来、黑位再深一点'),
}

# 鹿井 32 张的**内容归一**黑位形状（L5 − L50 的中位）。黑位只往它收，**只压不提**。
# ⚠ 为什么不直接压一个全局常数：我们片子的形状散得很开（实测同一批 10 张从 −31.9 到 −58.7），
#   鹿井集中在 −46 ~ −57 ⇒ 全局压 11 会把本来就深的那两张压到 −69（死黑）。
#   ⇒ 只补"离带还差的那一段"，已经在带内/更深的**一个像素都不动**。
TARGET_BLACK_SHAPE = -53.1

# ★ 绝对黑位下限（L*）。为什么光有"形状"不够：形状 = L5 − L50，而**中位低的片子**
#   （实测那批里中位 47.7 的），按形状收到 −53 会算到 L* 为**负** ⇒ 死黑一片、细节全丢。
#   鹿井那 32 张的中位普遍在 61 上下，他 L5 的绝对中位是 **7.5**（P25 5.6）
#   ⇒ 下限取 4（略低于他的 P25，留一点余地）。
#   ⚠ 中位低于 ~57 的片会被这道下限拦住 —— 那时形状对不满是**物理上到不了**，不是 bug。
TARGET_BLACK_FLOOR_L = 4.0


def rel_of(name, cfg=C):
    """取这一档的三个力度（名字不认得 ⇒ 回默认档，不静默乱走）。"""
    tbl = getattr(cfg, 'TONE_REL', None) or REL
    return tbl.get(str(name)) or tbl.get(DEFAULT) or REL[DEFAULT]


def settle_finished(disp, style=DEFAULT, cfg=C):
    """在**成片**（显示域）上做曝光风格：压曝光 / 压高光 / 提阴影。

    三点（L5 / L50 / L95）在 log2 亮度域插值 —— 和 `solve()` 同一个曲线机器，
    区别只是这里的靶是**相对当前的成片**算出来的，不是某个绝对数。

    @returns {(numpy.ndarray, dict)} 出图 + 报告（进去多少、出来多少，能自查）
    """
    st = rel_of(style, cfg)
    lin = color.s2l(np.clip(np.asarray(disp, np.float64), 0.0, 1.0))
    Y, p = measure(lin)
    y5, y50, y95 = (max(p[5.0], _EPS), max(p[50.0], _EPS), max(p[95.0], _EPS))

    # 三个分位各自往下搬：黑位 / 中位 / 亮部（`*_down` 都是"往下搬多少"，
    # ⚠ 黑位**负值 = 往上提** —— 别再用"提阴影"那种说法，方向容易搞反）
    # ★ 黑位**只往鹿井的形状收、只压不提**：离带还差多少就补多少（最多补 `bl_down`），
    #   已经在带内或更深的**一个像素都不动**。理由见 `TARGET_BLACK_SHAPE`。
    _L5, _L50 = float(color.L_of_lin(y5)), float(color.L_of_lin(y50))
    _need = max(0.0, (_L5 - _L50) - TARGET_BLACK_SHAPE)      # >0 = 离带还差这么多
    _bl = float(np.clip(min(float(st['bl_down']), _need), -30.0, 30.0))
    # 绝对黑位下限：别压穿（中位低的片子按形状算会到负数 ⇒ 死黑）
    _bl = min(_bl, _L5 - float(getattr(cfg, 'TARGET_BLACK_FLOOR_L', 4.0)))
    _bl = max(_bl, -30.0)
    Tb = float(np.clip(color.lin_of_L(_L5 - _bl), _EPS, None))
    Tm = float(np.clip(color.lin_of_L(float(color.L_of_lin(y50))) * (2.0 ** -float(st['ev_down'])),
                       _EPS, None))
    Tw = float(np.clip(color.lin_of_L(float(color.L_of_lin(y95)) - float(st['hi_down'])), _EPS, None))
    # 单调钳：靶必须 黑 < 中 < 白（留 2% 余量）
    Tb = min(Tb, Tm * 0.98)
    Tw = max(Tw, Tm * 1.02)

    curve = ToneCurve([_t(_EPS), _t(y5), _t(y50), _t(y95)], [_t(_EPS), _t(Tb), _t(Tm), _t(Tw)])
    out = np.clip(color.l2s(np.clip(lin * curve.gain(Y)[..., np.newaxis], 0.0, None)), 0.0, 1.0)

    info = dict(
        style=str(style),
        ev_down=float(st['ev_down']), hi_down=float(st['hi_down']),
        bl_down=float(st['bl_down']), bl_applied=float(_bl),
        bl_need=float(_need), bl_shape_in=float(_L5 - _L50),
        L5_in=float(color.L_of_lin(y5)), L50_in=float(color.L_of_lin(y50)),
        L95_in=float(color.L_of_lin(y95)),
        L5_out=float(color.L_of_lin(Tb)), L50_out=float(color.L_of_lin(Tm)),
        L95_out=float(color.L_of_lin(Tw)),
        applied=True,
    )
    return out, info