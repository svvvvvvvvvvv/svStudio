# -*- coding: utf-8 -*-
"""LR 侧求解器：**七步，一次算一次验，不闭环**。

    ① 定"看哪里"（选择：脸 / 人 / 整幅）
    ② 曝光 Exposure      → 脸进合理区（T4）
    ③ 高光 Highlights    → 不爆白（T1）
    ④ 曲线              → **只修不变量 + 落地意图**（不是去对别人的分位）
    ⑤ 全局饱和           → 只在整幅明显偏灰时
    ⑥ 点颜色            → 肤色（T5 + 意图）
    ⑦ 验收三门

设计出处：分工设计 第六部分 §31-§36 + 第三部分（没有"该到多少"）。

★ 曲线的语义（2026-10-29 改）：
  旧：拿"某一批成片的分位"当靶，造一条曲线把它对上。
  新：**没有靶**。曲线只做两件事——
      (a) 修不变量：爆白就压低端、堵黑就抬趾部
      (b) 落地意图：更亮/更暗 ⇒ 小幅整体位移
  且每锚点位移 ≤3 L*（S3）。**不做形状匹配。**

本文件只算值、只判定，**不直接驱动 LR**（驱动在 lrsink.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .curve import plan_curve

# 规划预算（验收预算见 invariants.S3['anchor_max'] = 3.0）
# 差 0.3 是给"16 点 LUT 插值 + 取整"的误差留的余量（实测 3.0 -> 3.14）
S3_BUDGET_PLAN = 2.7
from .invariants import T1, T2, T4
from .intent import Intent

# ---------------------------------------------------------------- 常数与出处
#
# ★ 中间调斜率与"曝光后那一态"：**能推**，已移到 expose.py（曝光前后共用一套）
#   `eval_slope` / `ev_for_dL` 在这里保留同名导出，老调用点不用改。
from .expose import (LAB_SLOPE_K, after_ev, eval_slope, ev_for_dL,
                     headroom_note)
#
# ★ 高光端斜率：理论值不适用（印相肩部压缩），只能实测。实测 8~16 L*/EV。
#   见 design 文档「高光端斜率」与 DSCF1289 那组实测（8~16 L*/EV）。
SLOPE_HIGH_MIN, SLOPE_HIGH_MAX = 8.0, 16.0
#
# ★ 点颜色的**亮度**：推满（lum=+1）实测能抬多少 L*（2026-10-29 实测，n=1）
#   DSCF1627：脸 45.7 → 54.8（+9.1），背景 39.8 → 40.3（+0.5）⇒ 只动肤色，不动背景。
#   ⚠ 单点线性外推，只用来给**起点**；真正的量靠出片验收（同 ③ 的做法）。
PC_LUM_L_PER_UNIT = 9.1
# 局部提脸只补"全局曝光补不动的差额"，且不许把脸推过头
PC_LUM_MAX = 1.0
# ★ 单步上限（10-29 单张实测）：灵敏度**随起点变**——同一张图 lum 0.44 给出 +10.1 格，
#   而 lum 1.0 只给 +9.1 格（起点 L 不同）。单点线性外推会过冲 ⇒ 每步封顶 0.25，
#   不够下一轮再加（阶梯本来就有轮次）。
PC_LUM_STEP = 0.25

# ★ 逆光的肤色彩度补偿（2026-10-29 SV 目检：「逆光下的肤色有点沉闷」）。
#   逆光时脸大部分在阴影里，提亮 + 收白之后暗部被抬、彩度看着更灰。
#   这是**风格剂量**（不是靶）：起点给 +0.15，靠目检定够不够。
PC_BACKLIT_SAT = 0.15

# ★ 点颜色：**每一次推满（±1）能补多少比例**——这是**政策**，不是物理常数。
#   原因：Point Color 的 SatScale 没有公开传递函数（见 lrmodel.py）。
#   政策含义：缺口 ≤ 这个比例时，一次推满；缺口更大时分多次/报欠账。
PC_FULL_CLOSES = 0.35
#
# ★ 意图：±1 意图 = ±0.5 档。这是**接口定义**（"+1 是什么意思"），不是物理断言。
INTENT_EV_PER_UNIT = 0.5
#
# ★ 落区边界要留的余量：脸进区就停，但**目标点要离边界 ≥ 测量容差**，
#   否则量出来刚过线、下一张又掉出去。容差 L* ±1.0（量化下限）⇒ 取 1.5。
EDGE_MARGIN_L = 1.5
#
# ★★ 落点容差（2026-10-29 真跑实测，n=5）：按模型给的曝光，实际落点误差
#   |残差| 中位 0.92 / P90 2.30 / **最大 2.88** —— 模型斜率 13~15 L*/EV，
#   实际响应 11.4~18.5（散 ±30%）。目标贴边 1.5 格时，误差一来就掉到区外
#   （DSCF1627：给 +0.88 档，模型说 56.5，实际 54.2，差 0.8 没进区）。
#   ⇒ 目标点改成 `下界 + 落点容差`，容差取 3.5（覆盖实测最大 2.88）。
LAND_TOL_L = 3.5
#
# ★ 逆光补偿（设计 §28 L3「逆光 ≠ 顺光」）。同一 L* 的脸在逆光下**看起来更暗**
#   （背景压着）。10-29 目检：1289 光比 0.91，提 0.34 档到 56.8 仍显暗。
#   ⇒ 逆光时目标点再往区内侧推 4 L*，走「提曝光 + 收白」两步（§31 ②→③）。
BACKLIT_EXTRA_L = 4.0
#
# ★ 目标点上限：只在区间**中段**，不许贴着上界追（否则一动就进"过曝区"）。
LAND_CAP_L = 70.0
#
# ★ 脸的"够大"判据（H2 降级链用）：face_w_native ≥ 40 px 才算能当锚。
#   出处：手掩膜守卫 [0.005, 0.70] 与旧链实测（脸太小则颊读数不可靠）。
FACE_MIN_W = 40.0
#
# ★ 光位判据（H4）—— 2026-10-29 重写：旧版用「脸 ÷ 背景**中位**」是错的。
#   逆光时背景中位落在暗部（树皮/石墙/树荫），太阳照到的那片才是亮的：
#   DSCF1608 脸 46.8 / 背景中位 28.8 ⇒ 旧判据说"脸比背景亮 = 顺光"，
#   而它其实是逆光（背景 p90 = 71.5，脸比亮部低 24.6）。
#
#   现在用**背景亮部**（测量层 `bg.L_p90`）：
#     gap  = 背景 p90 − 脸 L      脸比最亮那片背景低多少
#     span = 背景 p90 − 背景 p10  背景自己有没有明暗跨度（既有树荫又有太阳）
#   逆光 = gap 大且 span 大；顺光 = gap 很小；其余 = unknown（**宁可弃权，不猜**）。
#
#   ⚠ 这是近似判据，不是物理测量。真逆光的直接证据是**轮廓光**（脸外圈比脸亮），
#     测量层还没量那个，所以保留 unknown 这条退路。
LIGHT_GAP_BACKLIT = 15.0
LIGHT_GAP_FRONT = 8.0
LIGHT_SPAN_MIN = 55.0
# 老数据（没有 bg.L_p90）退回旧比值，只作兼容，**标记为不可靠**
LIGHT_BACKLIT, LIGHT_FRONTLIT = 1.0, 1.3



@dataclass
class LRPlan:
    """一张图的 LR 参数（单位就是 LR 面板上的数）。"""
    exposure_ev: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    saturation: float = 0.0
    vibrance: float = 0.0
    curve: Optional[List[Tuple[int, int]]] = None
    point_color: Optional[Dict[str, float]] = None
    point_color_rgb: Optional[list] = None      # ②处取样色（过完曲线后），造 19 数字要用它
    notes: Dict[str, str] = field(default_factory=dict)

    def as_xmp_scalars(self) -> Dict[str, float]:
        return dict(Exposure2012=self.exposure_ev, Highlights2012=self.highlights,
                    Shadows2012=self.shadows, Whites2012=self.whites,
                    Blacks2012=self.blacks, Saturation=self.saturation,
                    Vibrance=self.vibrance)


# ---------------------------------------------------------------- ① 看哪里

@dataclass
class LookAt:
    """「看哪里」的结果：**带理由**，不许静默选一个档。"""
    kind: str                 # face | matrix
    source: str               # 读的是哪个量（如 'skin.L' / 'img.p50'）
    why: str
    light: str = 'unknown'    # backlit | frontlit | unknown（H4）

    def __str__(self):
        return '%s（%s）%s' % (self.kind, self.source,
                              '' if self.light == 'unknown' else ' · ' + self.light)


def light_state(rec: dict, given: Optional[str] = None) -> str:
    """光位（H4）：**由人给，不由测量猜**。

    为什么改成这样（2026-10-29 两轮实测）：
      · 旧判据「脸 ÷ 背景中位」：逆光时背景中位落在暗部 ⇒ 把 DSCF1608（真逆光）判成顺光；
      · 新判据「脸 vs 背景亮部 p90」：画面里有天空时 p90≈天空 ⇒ 把 DSCF1307
        （脸被太阳直照，明显顺光）判成逆光。
      两个方向都出反例 ⇒ 单张图**没有**可靠的自动判据。真正可靠的证据是轮廓光，
      测量层还没量它。**宁可弃权，也不猜。**

    `given`：人给的 `backlit` / `frontlit`（按批，`cli run --light`）。
    没给就 unknown —— 于是所有"逆光才做"的动作（落点 +4、肤色 +0.15）都不会误触发，
    但测量证据（`light_gap` / `light_span`）照记，供人判断时参考。
    """
    bg = rec.get('bg') or {}
    face = (rec.get('skin') or {}).get('L')
    hi, lo = bg.get('L_p90'), bg.get('L_p10')
    if hi is not None and face is not None:
        rec['light_gap'] = round(float(hi) - float(face), 1)
        rec['light_span'] = round(float(hi) - float(lo if lo is not None else 0.0), 1)
    if given in ('backlit', 'frontlit'):
        rec['light_why'] = '人给（--light %s）' % given
        return given
    if given == 'auto':
        # 明确要求"自己猜"时才用判据，并标明是近似
        gap = rec.get('light_gap')
        span = rec.get('light_span')
        if gap is not None and span is not None:
            rec['light_why'] = '自动判据（近似，已两次出反例，不建议用）'
            if gap >= LIGHT_GAP_BACKLIT and span >= LIGHT_SPAN_MIN:
                return 'backlit'
            if gap <= LIGHT_GAP_FRONT:
                return 'frontlit'
    return 'unknown'


def pick_lookat(rec_engine: dict, light: Optional[str] = None) -> LookAt:
    """H2 降级链：脸 → 人 → 整幅。**每一档都要解释为什么，降级了要写出来。**

    2026-10-29 修（原代码的两个毛病）：
      ① `person` 档 `cur = None` ⇒ 直接返回 0 档 —— 等于"没脸的图整批不动曝光"，静默；
      ② 判"有人"用的是 `env_share < 0.95`（背景占比），跟"有没有人"不是一回事。

    现在：
      · 脸够大 → face（读 skin.L，r²=0.890，手册 §15.1）
      · 有脸但太小 → 设计要"人"的读数，**测量层目前不给人的亮度** ⇒
        明确降级到整幅，并把"人读数缺"写进理由（不假装做了 person 档）
      · 没有人脸 → 整幅。整幅的**落点没有不变量**（T4 是给脸的）⇒ 曝光弃权，
        只保 T1/T2（那是 Highlights/曲线的活）。弃权也要写出来（§7 失败与弃权）。
    """
    skin = rec_engine.get('skin') or {}
    fw = rec_engine.get('face_w_native') or 0
    light_s = light_state(rec_engine, given=light)
    if skin.get('L') is not None and fw >= FACE_MIN_W:
        return LookAt('face', 'skin.L',
                      '有脸且够大（face_w %.0f ≥ %.0f）' % (fw, FACE_MIN_W), light_s)
    if skin.get('L') is not None or (rec_engine.get('face_mask_share') or 0) > 0:
        return LookAt('matrix', 'img.p50',
                      '有脸但太小（face_w %.0f < %.0f）：人的读数测量层还没接 ⇒ 降级整幅，'
                      '不假装有 person 档' % (fw, FACE_MIN_W), light_s)
    return LookAt('matrix', 'img.p50', '未检到脸 ⇒ 整幅；整幅落点没有不变量 ⇒ 曝光弃权', light_s)


# ---------------------------------------------------------------- ② 曝光

@dataclass
class Exposure:
    """曝光这一步的结果：值 + 物理上限 + 欠账。"""
    ev: float = 0.0
    want_ev: float = 0.0              # 为了把脸放进区想要的档数（含意图）
    cap_headroom: Optional[float] = None   # 引擎报的"还能抬几档"（**只作记录，不当上限**）
    cap_white: Optional[float] = None      # 把 p95 推到白平台还差几档（物理上限，只作提示）
    debt_ev: float = 0.0              # 差多少档没给
    why: str = ''


def _cap_white(rec_engine: dict) -> Optional[float]:
    """第二道预算：把 p95 推到白平台还差几档。

    为什么用 p95 而不是 p99：p99 已经贴死在 99.9（本批实测），拿它算上限永远是 0，
    等于不许提亮；p95 是"5% 到顶"的位置，用它当白端预算才有分辨力。
    ⚠ 这是**近似**：真实肩部会压缩，所以它偏保守（真能提的比它多一点）。
    """
    p95 = (rec_engine.get('img') or {}).get('p95')
    if p95 is None:
        return None
    s = eval_slope(p95)
    return (100.0 - float(p95)) / s if s > 1e-9 else None


def solve_exposure(rec_engine: dict, target_L: float = 72.0,
                   L_lo: float = None, L_hi: float = None,
                   intent: Optional[Intent] = None,
                   light: Optional[str] = None) -> Exposure:
    # `target_L` 只为兼容旧调用保留，**已经不参与计算**：
    # 脸是约束不是目标（§28 L1），落点只由 T4 的区边界决定。
    """② 曝光落点。**脸是约束，不是目标**（设计 §28 L1）。

    2026-10-29 中午重写（SV 当面纠正之后，对着 §19~§32）：
      · **曝光/影调是 LR 的事**（§19 分工表），引擎只管卷纸/质感/白点。
      · 所以这里的上限**只有物理的一条**：把 p95 推到白平台（L*100）就再也提不动了。
      · ~~引擎余量 headroom_ev 当硬上限~~ —— 已删。那不是设计概念：
        §21 引擎自带的中灰归一**就是余量保证机制**；它若不够是卷/纸或入口 WB 的问题，
        不是"LR 少提一点"能补的。
      · 提完会爆怎么办：**不是在这里否决**，是交给 ③ Highlights 收白（§31 ②→③，
        §28 L2「提完之后 clip 仍 < T1，否则先收 Highlights」）。
        只有 Highlights 也收不回来，才按 §32 ③ 回引擎要余量。
    """
    lo = T4['L_lo'] if L_lo is None else L_lo
    hi = T4['L_hi'] if L_hi is None else L_hi
    la = pick_lookat(rec_engine, light=light)

    ex = Exposure(cap_headroom=rec_engine.get('headroom_ev'),   # 只记录，不设限
                  cap_white=_cap_white(rec_engine))

    if la.kind != 'face':
        ex.why = '弃权：%s（整幅没有落点不变量，曝光不动）' % la.why
        return ex

    cur = (rec_engine.get('skin') or {}).get('L')
    if cur is None:
        ex.why = '弃权：脸读数为空'
        return ex

    # ---- ① 约束式：只在出区时动。目标 = 下界 + 落点容差（+逆光补偿）
    #   ★ 不再"刚进区 + 1.5"贴边：实测落点误差最大 2.88，贴边必然掉出去
    margin = LAND_TOL_L + (BACKLIT_EXTRA_L if la.light == 'backlit' else 0.0)
    if cur < lo:
        target = min(lo + margin, LAND_CAP_L)
        want_dL, side = target - cur, '暗于下界'
    elif cur > hi:
        want_dL, side = (hi - LAND_TOL_L) - cur, '亮于上界'
    else:
        want_dL, side = 0.0, '区内'
    ex.want_ev = ev_for_dL(want_dL, cur)

    # ---- 意图：只改方向与幅度，不许把脸推出人样（S4）
    if intent is not None:
        ex.want_ev += getattr(intent, 'brighter', 0.0) * INTENT_EV_PER_UNIT
        newL = cur + ex.want_ev * eval_slope(cur)
        if newL > hi - EDGE_MARGIN_L:
            ex.want_ev -= ev_for_dL(newL - (hi - EDGE_MARGIN_L), cur)
        elif newL < lo + LAND_TOL_L:
            ex.want_ev += ev_for_dL((lo + LAND_TOL_L) - newL, cur)

    # ---- 光位说明（H4）：**两个分支都要带**，它讲的是"该怎么走"，不是"能不能提"
    light_hint = ''
    if la.light == 'backlit':
        light_hint = '（逆光：提曝光 → 再收 Highlights，两步）'
    elif la.light == 'frontlit' and cur < lo:
        light_hint = '（顺光但脸低 ⇒ 先查入口 WB，别靠曝光硬提）'

    # ---- ② 按脸给满；白端那截**交给 ③ Highlights 收**，不在这里砍
    #   ★ 为什么不砍：§31 的顺序是 ② 曝光 → ③ 收白，③ 用的就是"压亮部、不动中间调"的工具。
    #     在这里按 p95 砍，等于假定 Highlights 不存在 —— 那 ② 永远提不动，脸永远进不了区。
    #     `cap_white` 只当**距离提示**：越过它就意味着"提完必须收白"，写进 why 让 ③ 接手。
    ex.ev = ex.want_ev
    over = (ex.ev - ex.cap_white) if ex.cap_white is not None else 0.0
    if want_dL == 0.0 and (intent is None or abs(ex.want_ev) < 1e-9):
        ex.why = '区内（脸 %.1f）⇒ 不动' % cur
    elif over > 1e-9:
        ex.why = ('%s（脸 %.1f，给 %+.2f 档）⇒ 提完 p95 会过白平台 %.2f 档，'
                  '**必须由 ③ Highlights 收白**（③ 出片验 clip 是否 < %.1f%%）%s'
                  % (side, cur, ex.ev, over, T1['clip_max'], light_hint))
    else:
        ex.why = '%s（脸 %.1f，给 %+.2f 档）%s' % (side, cur, ex.ev, light_hint)
    ex.ev = round(float(ex.ev), 3)
    ex.want_ev = round(float(ex.want_ev), 3)
    return ex


# ---------------------------------------------------------------- ③ 收白
#
# ★ 为什么这里**没有公式**（2026-10-29 查过，别再来一遍）：
#   · Adobe 官方只写「Highlights / Shadows 按亮度区域自适应、会保护其它区域」，
#     并明说它们「比一条曲线复杂得多」——**没有公开传递函数**。
#     → https://helpx.adobe.com/lightroom-classic/desktop/help/tone-control-adjustment.html
#   · 第三方近似（agx-photo 的 basic_tone：weight=(u−0.5)/0.5，out=u+weight·h/100·0.5，
#     sRGB 伽马域）是对**手感**的仿写，不是 Adobe 的算法；拿它算幅度只会得到一个
#     "看起来很精确"的假值。→ https://docs.rs/agx-photo/0.1.0/.../basic_tone
#   ⇒ 结论同 lrmodel.py H1：**只当方向用，幅度靠"加到目标为止"的验收**。
#   ⇒ 所以 ③ 输出的是「起点档（对分第一步）+ 阶梯 + 验收量」，**不是"算出来的幅度"**。

HL_END = -100.0                     # 滑杆下限（Adobe：−100..+100）
HL_SEARCH_MID = -50.0               # 起点 = 量程中点：响应单调 ⇒ 对分最省步数，且不给假精度
HL_STOP_STEP = 12.5                 # 对分到这个步长就停（1/8 量程，再细没有画面意义）


def solve_highlights(pred: dict, clip_max: Optional[float] = None,
                     policy: Optional[float] = None,
                     face_L: Optional[float] = None):
    """③ 高光收白：**在 ①'（提完曝光之后）上判**，不在 ① 上判。

    ★ 优先级（2026-10-29 定）：**脸 > 背景高光**。
      脸必须进区、且不许为了收白把脸压暗；背景过曝**可以接受**，只记账不硬顶。
      所以这里判的是"要不要收、从哪里起收"，**判不了"必须收到多少"**。

      · 不用收      ⇒ 0
      · 要收        ⇒ 起点 `HL_SEARCH_MID`（对分第一步），并写明这是搜索起点
      · 脸已贴顶    ⇒ 那是**主体丢了**（收白救不回），单列，不算背景的账
      · 只是背景爆  ⇒ 收白尽力，收不住就接受（不是欠账，不是流程错）

    **不算**具体压多少：`Highlights` 没有公开传递函数（见上面那段与 lrmodel.py H1）。
    编一个斜率比不给更糟 —— 那会把"没验过"包装成"算准了"。
    """
    cm = T1['clip_max'] if clip_max is None else clip_max
    lb = float(pred.get('clip_lb') or 0.0)
    c0 = float(pred.get('clip0') or 0.0)
    over = float(pred.get('over_ev') or 0.0)
    wl = float(pred.get('white_over_L') or 0.0)
    fL = pred.get('pred', {}).get('skin', {}).get('L')
    fL = face_L if face_L is not None else fL

    face_blown = fL is not None and fL >= HL_FACE_BLOWN_L
    blown_at_1 = c0 > cm            # ① 本身就爆
    need = face_blown or blown_at_1 or (lb > cm) or (wl > 1e-9) or (over > 1e-9)
    if not need:
        return 0.0, None

    start = float(policy if policy is not None else HL_SEARCH_MID)
    pieces = []
    if face_blown:
        pieces.append('**脸已经贴顶（L %.1f ≥ %.0f）⇒ 主体丢了**：收白救不回细节，'
                      '这要往上游查（入口曝光/场景），不是这里能修的' % (fL, HL_FACE_BLOWN_L))
    if blown_at_1 and not face_blown:
        pieces.append('① 背景就爆（clip %.2f%% > %.1f%%）——脸优先，背景爆可接受，'
                      '收白只是让它别太扎眼' % (c0, cm))
    if wl > 1e-9 and not blown_at_1:
        pieces.append('上端过白 %.1f L*（未截断，按 p95/p99 的线性平移算）' % wl)
    if lb > cm and not blown_at_1:
        pieces.append('爆白下界 %.2f%% > %.1f%%' % (lb, cm))
    if over > 1e-9:
        pieces.append('曝光超引擎余量 %.2f 档' % over)
    return start, (
        '%s ⇒ 收白。给 %+.0f：这是**对分搜索的第一步**（量程中点），'
        'Highlights 无公开传递函数，幅度不能算（lrmodel.py H1）。'
        '阶梯的**停止条件是脸**：脸一掉就停（再压就是拿脸换背景）；'
        '收到 %.0f 还收不住 ⇒ 接受背景过曝，不回引擎'
        % ('；'.join(pieces), start, HL_END))


# 脸被烧掉的门槛（L*）：到这个程度已经没有皮肤层次可救，属于主体丢失
HL_FACE_BLOWN_L = 99.0
# 收白允许把脸压暗多少（L*）；超过就是拿脸换背景
HL_FACE_DROP_MAX = 1.0


def next_highlights(prev: float, clip_pct: float,
                    face_after: Optional[float] = None,
                    face_before: Optional[float] = None,
                    p95_after: Optional[float] = None,
                    p95_before: Optional[float] = None,
                    clip_max: Optional[float] = None):
    """③ 的阶梯：**按"脸优先"判进退**（2026-10-29 晚重写）。

    退出的四个出口，按优先级排：
      1. **脸被压暗了**（Δ > 1 L\\*）   ⇒ **回退**（脸优先；宁可背景更爆）
      2. 没脸读数但上端掉到比 ① 还低  ⇒ 当压过头处理（降级判据，不是主判据）
      3. `clip` 收住了                 ⇒ 停，达标
      4. 走到 −100 仍超                ⇒ **接受背景过曝**，记账完事

    ★ 不再有"回引擎要余量"这个出口 —— 回引擎实际只会整幅压暗，等于拿脸换背景，
      与"脸优先"相反，也越过了分工（引擎不管逐图落点）。

    返回 `(下一档 | None, 说明)`。None = 不用再动。
    """
    cm = T1['clip_max'] if clip_max is None else clip_max

    # 1) 脸被压暗 ⇒ 回退（不是"停着不动"：不动就等于默认接受脸已经暗了）
    if face_after is not None and face_before is not None:
        drop = face_before - face_after
        if drop > HL_FACE_DROP_MAX:
            cand = prev / 2.0
            if abs(cand) < HL_STOP_STEP:
                return 0.0, ('③ 归零：收白把脸压暗了 %.1f L\\*（%.1f → %.1f），'
                             '留在 %+.0f 已无意义 —— **脸优先**，按 0 处理'
                             % (drop, face_before, face_after, prev))
            return cand, ('③ 回退到 %+.0f：收白把脸压暗了 %.1f L\\*（%.1f → %.1f）——'
                          '**脸优先**，宁可背景更爆也不压脸'
                          % (cand, drop, face_before, face_after))

    # 2) 上端掉太多（只在没有脸读数时当判据）
    over_white = (face_after is None and p95_after is not None
                  and p95_before is not None and p95_after < p95_before - 1.0)
    if over_white:
        cand = prev / 2.0
        if abs(cand) < HL_STOP_STEP:
            return None, ('③ 压过头：上端 %.1f 已低于 ① 的 %.1f，回退幅度也到最小步长'
                          '⇒ 按 0 处理' % (p95_after, p95_before))
        return cand, ('③ 压过头：上端 %.1f 低于 ① 的 %.1f ⇒ 回退到 %+.0f'
                      % (p95_after, p95_before, cand))

    # 3) 收住了
    if clip_pct <= cm:
        return None, '③ 达标：clip %.2f%% ≤ %.1f%%（%+.0f 够用）' % (clip_pct, cm, prev)

    # 4) 还超：加大；到尽头就接受
    cand = prev - (abs(HL_END) - abs(prev)) / 2.0
    if abs(cand) >= abs(HL_END) - 1e-9 or abs(cand - prev) < HL_STOP_STEP:
        if abs(prev) >= abs(HL_END) - 1e-9:
            return None, ('③ 收白到尽头 %+.0f，背景仍爆（clip %.2f%% > %.1f%%）⇒ '
                          '**接受**：脸没掉就不算账，记下爆了多少即可'
                          % (prev, clip_pct, cm))
        return HL_END, ('③ 还差（clip %.2f%% > %.1f%%）⇒ 走到尽头 %+.0f 再验'
                        % (clip_pct, cm, HL_END))
    return cand, ('③ 还差（clip %.2f%% > %.1f%%）⇒ 加大到 %+.0f（脸没掉，可以继续）'
                  % (clip_pct, cm, cand))

# ---------------------------------------------------------------- ④ 曲线

def solve_curve(rec_engine: dict, intent: Optional[Intent] = None,
                budget: Optional[float] = None,
                allow_clip: bool = True) -> Tuple[Optional[List[Tuple[int, int]]], dict]:
    """**只修不变量 + 落地意图**，不对任何"别人的分位"造形。

    `allow_clip=False`：**在 LR 全链里用这个**。§33 裁决「Highlights vs 曲线肩部 ⇒
    只用 Highlights，曲线肩部不动」——爆白是 ③ 的活，曲线再压一次就是两处同时动。

    （顺带一个坑：`after_ev` 的 `pred['img']` 里**没有 `clip_pct`**，
    所以流程里这个分支本来就没触发过；那是碰巧对，不是设计对。现在显式写死。）

    返回 (16 点曲线 | None, 说明)。None = 不需要曲线或预算内无解。
    """
    img = rec_engine.get('img') or {}
    q = {str(k): img.get('p%d' % k) for k in (5, 25, 50, 75, 95)}
    if not all(isinstance(v, (int, float)) for v in q.values()):
        return None, {'why': '缺分位'}
    _ = intent          # 明确：曲线不看意图
    # ★ 规划预算要比验收预算紧一点：16 点 LUT 的插值 + 取整会把**实际**位移放大
    #   （实测：规划 3.0，量到 3.14）。留 0.3 的余量。
    if budget is None:
        budget = max(0.5, S3_BUDGET_PLAN)
    _ = budget
    src = dict(q)
    dst = dict(q)

    # ★ 意图**不由曲线落地**（2026-10-29 修正：曝光与曲线都加一次 = 意图被算两次）。
    #   亮暗意图归曝光滑杆；曲线只修不变量。

    # (a) 不变量：爆白压低端（**流程里关掉，见 §33**）、堵黑抬趾部
    clip = img.get('clip_pct') or 0.0
    fixed, unmet = [], {}
    if allow_clip and clip > T1['clip_max']:
        want = clip - T1['clip_max']            # 图便宜：按可见剪裁比例给（不是靶）
        d = min(budget, want)
        dst['95'] -= d
        dst['75'] -= d * 0.5
        fixed.append('压白端 %.2f' % d)
        if want > d + 1e-9:
            unmet['白端'] = round(want - d, 2)
    p1 = img.get('p1') or 0.0
    if p1 < T2['p1_min']:
        want = T2['p1_min'] - p1
        d = min(budget, want)
        dst['5'] += d
        dst['25'] += d * 0.5
        fixed.append('抬黑端 %.2f' % d)
        if want > d + 1e-9:
            unmet['黑端'] = round(want - d, 2)

    if src == dst:
        return None, {'why': '无需修正（不变量都过、意图为空）'}
    pts, info = plan_curve(src, dst, budget=budget)
    info['fixed'] = fixed
    if unmet:
        # 曲线上限 3 L* 修不了的量 ⇒ 这不是曲线的活（S3/S5：回引擎要余量）
        info['unmet'] = unmet
    return pts, info


# ---------------------------------------------------------------- ⑤ 饱和

def solve_saturation(rec_engine: dict, intent: Optional[Intent] = None) -> float:
    """只在"整幅明显偏灰"或意图要浓淡时动；幅度小，且**不许用来补肤色**。"""
    d = getattr(intent, 'denser', 0.0) if intent else 0.0
    return max(-15.0, min(15.0, d * 15.0))


# ---------------------------------------------------------------- ⑥ 点颜色

def solve_point_color(rec: dict, intent: Optional[Intent] = None,
                      C_min: float = 10.0,
                      face_deficit_L: float = 0.0,
                      backlit: bool = False) -> Optional[Dict[str, float]]:
    """肤色剂量（批次一套）。**新体系里没有"肤色靶"**，所以只做两件事：

      (a) 不变量：肤色 C 低于 C_min（发灰）⇒ 推一点
      (b) 意图：更浓/更淡
    色相（shift）默认不动 —— 没有靶就没有"该转多少度"。
    """
    skin = rec.get('skin') or {}
    C = skin.get('C')
    if C is None:
        return None
    scale = 0.0
    if C < C_min:
        # 缺口比例 ÷ "推满能补多少"（**政策**，非物理）；夹 ±1
        gap = C_min / max(C, 1e-6) - 1.0
        scale = gap / PC_FULL_CLOSES
    d = getattr(intent, 'denser', 0.0) if intent else 0.0
    scale += d * 0.5                       # 意图只加一半幅度，另一半交给整体饱和（政策）
    # ★ 脸部亮度差额（`face_deficit_L` > 0 = 还差多少才进区）：
    #   全局曝光提不动的那一段，用**只在肤色范围**的亮度补 —— 不动背景，
    #   所以不会触发"背景变亮 → 收白 → 压脸"那条链。只给起点，量靠出片验收。
    lum = 0.0
    if face_deficit_L > 0.5:
        lum = min(PC_LUM_MAX, face_deficit_L / PC_LUM_L_PER_UNIT)
    # 逆光：肤色补一点彩度（阴影侧被抬平之后发灰）
    if backlit:
        scale += PC_BACKLIT_SAT
    return dict(scale=max(-1.0, min(1.0, scale)), shift=0.0, lum=round(float(lum), 3))


def next_point_lum(prev: float, face_after: Optional[float], face_target: float,
                   sens: float = PC_LUM_L_PER_UNIT) -> Tuple[Optional[float], str]:
    """⑥ 的阶梯：**用肤色亮度补脸的最后一段**（只动肤色、不动背景）。

    这是「以人为本」的第三段（②全局曝光 → ③收白 → ⑥肤色亮度）：
    全局曝光提不动的差额，在这里补，代价只有"肤色范围内的亮度变了"。

    `sens` = 推满 1.0 能抬多少 L*（实测单点 9.1，n=1）；所以这一步是**按实测补差**，
    不是按模型算 —— 每次都用上一轮出片量到的脸去算下一步。

    返回 (下一档 | None, 说明)。None = 不用再动。
    """
    if face_after is None:
        return None, '⑥ 没有脸读数 ⇒ 不动'
    deficit = float(face_target) - float(face_after)
    if deficit <= 0.5:
        return None, '⑥ 脸已到位（%.1f ≥ %.1f）⇒ 不动' % (face_after, face_target)
    if abs(prev) >= PC_LUM_MAX - 1e-9:
        return None, ('⑥ 肤色亮度已推满 %+.1f 仍差 %.1f L\\* ⇒ 记为欠账'
                      '（这条链到此为止；再加就不是"只动肤色"了）' % (prev, deficit))
    step = min(PC_LUM_MAX, abs(prev) + PC_LUM_STEP,
               abs(prev) + deficit / max(sens, 1e-6))
    if step <= abs(prev) + 1e-9:
        return None, '⑥ 算出来的增量已小于最小步长 ⇒ 不动'
    return round(-step if prev < 0 else step, 3), (
        '⑥ 脸还差 %.1f L\\*（%.1f < %.1f）⇒ 肤色亮度推到 %+.2f（按实测 %.1f L\\*/单位）'
        % (deficit, face_after, face_target, step, sens))


# ---------------------------------------------------------------- 主流程

def solve_one(rec_engine: dict, rec_after_ev: Optional[dict] = None,
              light: Optional[str] = None,
              rec_final: Optional[dict] = None, intent: Optional[Intent] = None,
              log=print) -> LRPlan:
    """单图的 LR 计划。

    2026-10-29 修（顺序 + 口径，都对着设计 §35 的数据接口）：
      · **先算 ①'（提完曝光之后的态），再拿它判收白**。原来 `rec_after_ev`
        永远是 None ⇒ `solve_highlights` 看的是 ① 的 clip，先收白后算曝光，顺序倒了。
        现在没人传就用 `expose.after_ev` 现算（纯算，不渲、不驱动 LR）。
      · 点颜色改吃 **①'** 的肤色（比 ① 更接近 ②）；② 的真值仍然算不了（点颜色无函数）。
      · 每一步的理由都落进 `notes`，弃权/欠账/降级一律显式写，不许静默。
    """
    plan = LRPlan()
    la = pick_lookat(rec_engine, light=light)
    plan.notes['lookat'] = '%s — %s' % (la, la.why)
    # 光位：**只记人给的那个**，外加测量旁证（gap/span）供人判断，不拿判据代替人
    if la.light != 'unknown':
        plan.notes['light'] = ('逆光（人给）⇒ 落点再推 %.0f L*，提完必须预备收白'
                               % BACKLIT_EXTRA_L
                               if la.light == 'backlit' else
                               '顺光（人给）⇒ 只做微调，不额外推落点')
    else:
        plan.notes['light'] = '未给（不猜；旁证见下）'
    if la.light == 'unknown' and rec_engine.get('light_gap') is not None:
        plan.notes['light_evidence'] = (
            '光位未给（不认识画面）｜旁证：脸比背景亮部低 %.1f L*、背景跨度 %.1f L*'
            % (rec_engine.get('light_gap'), rec_engine.get('light_span') or 0.0))

    ex = solve_exposure(rec_engine, intent=intent, light=light)
    plan.exposure_ev = ex.ev
    plan.notes['exposure'] = ex.why
    if ex.debt_ev > 1e-9:
        plan.notes['exposure_debt'] = (
            '要 %+.2f 档，给了 %+.2f ⇒ 欠 %.2f 档（引擎余量 %s / p95 到白平台 %s）；'
            '这不是 LR 能顶的，回去查引擎余量或换肩部更宽的卡'
            % (ex.want_ev, ex.ev, ex.debt_ev,
               '—' if ex.cap_headroom is None else '%.2f' % ex.cap_headroom,
               '—' if ex.cap_white is None else '%.2f' % ex.cap_white))
    if ex.cap_headroom is not None:
        plan.notes['headroom'] = headroom_note(ex.cap_headroom)

    # ---- ①' ：提完曝光之后的态（纯算）
    pred = rec_after_ev or after_ev(rec_engine, ex.ev)
    if pred.get('notes'):
        plan.notes['after_ev'] = '；'.join(pred['notes'])

    hv, hnote = solve_highlights(pred)
    plan.highlights = round(hv, 1)
    if hnote:
        plan.notes['highlights'] = hnote
        # ③ 的账：起点档是"待验"的，没有实测之前不许当成已解决。
        # ★ 主判据是**脸**（脸优先）：clip 只是次要条件
        plan.notes['highlight_verify'] = (
            '③ 出片要量的：**脸 L 掉没掉**（不许暗 > %.1f L\\*，这是停手线）· '
            'clip_pct（< %.1f%% 最好，收不住可接受）· p50 不许动（§33）'
            % (HL_FACE_DROP_MAX, T1['clip_max']))
        if '主体丢了' in hnote:
            plan.notes['highlight_debt'] = hnote
        elif '背景就爆' in hnote:
            plan.notes['highlight_accept'] = (
                '背景过曝按取舍接受：脸优先，收白收到"脸开始掉"就停；收不住也不回引擎（回引擎只会整幅压暗 = 拿脸换背景）')

    # 曲线看的是 ①'（等价于"提完曝光再修不变量"），否则曲线会去修一个已经不存在的问题
    rec_for_curve = dict(rec_engine)
    rec_for_curve['img'] = dict(pred['pred']['img'])
    if rec_for_curve['img'].get('p1') is None:
        rec_for_curve['img']['p1'] = (rec_engine.get('img') or {}).get('p1')
    pts, info = solve_curve(rec_for_curve, intent=intent, allow_clip=False)
    # ↑ allow_clip=False：爆白归 ③（§33「Highlights vs 曲线肩部 ⇒ 只用 Highlights」）
    plan.curve = pts
    plan.notes['curve'] = ('无需' if pts is None else
                           '修正 %s ｜ 最大位移 %.2f' % (info.get('fixed') or '意图',
                                                       info.get('max_abs_delta') or 0))
    plan.saturation = round(solve_saturation(rec_engine, intent), 1)

    # 点颜色吃 ①' 的肤色（② 的真值算不了，这是最接近的一层）
    rec_pc = rec_final or dict(rec_engine, skin=pred['pred']['skin'])
    pc = solve_point_color(rec_pc, intent=intent,
                           backlit=(la.light == 'backlit'))
    # ★ 三个分量任一非零都要带上（10-29 真跑：只判 scale/shift ⇒ 只有 lum 时被丢掉，
    #   ⑥ 的阶梯永远拿不到 point_color，整段是死的）
    if pc and (abs(pc['scale']) > 1e-6 or abs(pc['shift']) > 1e-6
               or abs(pc.get('lum') or 0.0) > 1e-6):
        plan.point_color = pc
        rgb = (rec_pc.get('skin') or {}).get('rgb')
        if rgb:
            from .curve import apply_curve_rgb
            plan.point_color_rgb = list(apply_curve_rgb(rgb, plan.curve or []))
            plan.notes['point_color_rgb_src'] = '②处（①的取样色过曲线 LUT）'
    return plan
