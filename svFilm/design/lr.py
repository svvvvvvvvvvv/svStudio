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
# ★ 脸的"够大"判据（H2 降级链用）：face_w_native ≥ 40 px 才算能当锚。
#   出处：手掩膜守卫 [0.005, 0.70] 与旧链实测（脸太小则颊读数不可靠）。
FACE_MIN_W = 40.0
#
# ★ 光位判据（H4）：设计 §3.1「逆光（脸/背景亮度比 < 1.0）· 顺光（比 > 1.3）」
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


def light_state(rec: dict) -> str:
    """光位（H4）：脸/背景亮度比 < 1.0 逆光，> 1.3 顺光。

    出处：设计 §3.1 的分类依据；比值来自测量层的 `light_ratio`。
    为什么要它：逆光与顺光要**相反**处理（§28 L3）——
    逆光该提的是脸（并且要预备收白）；顺光脸还低，那就不是光位的事。
    """
    lr = rec.get('light_ratio')
    if lr is None:
        return 'unknown'
    if lr < LIGHT_BACKLIT:
        return 'backlit'
    if lr > LIGHT_FRONTLIT:
        return 'frontlit'
    return 'unknown'


def pick_lookat(rec_engine: dict) -> LookAt:
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
    light = light_state(rec_engine)
    if skin.get('L') is not None and fw >= FACE_MIN_W:
        return LookAt('face', 'skin.L',
                      '有脸且够大（face_w %.0f ≥ %.0f）' % (fw, FACE_MIN_W), light)
    if skin.get('L') is not None or (rec_engine.get('face_mask_share') or 0) > 0:
        return LookAt('matrix', 'img.p50',
                      '有脸但太小（face_w %.0f < %.0f）：人的读数测量层还没接 ⇒ 降级整幅，'
                      '不假装有 person 档' % (fw, FACE_MIN_W), light)
    return LookAt('matrix', 'img.p50', '未检到脸 ⇒ 整幅；整幅落点没有不变量 ⇒ 曝光弃权', light)


# ---------------------------------------------------------------- ② 曝光

@dataclass
class Exposure:
    """曝光这一步的结果：值 + 两道预算 + 欠账。"""
    ev: float = 0.0
    want_ev: float = 0.0              # 为了把脸放进区想要的档数（含意图）
    cap_headroom: Optional[float] = None   # 引擎余量（"还能抬几档不剪"）
    cap_white: Optional[float] = None      # 把 p95 推到白平台还差几档
    cap_used: Optional[float] = None       # 实际取的那个上限（= min 两道）
    debt_ev: float = 0.0              # 差多少档没给（引擎欠账 / 卡欠账）
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
                   intent: Optional[Intent] = None) -> Exposure:
    # `target_L` 只为兼容旧调用保留，**已经不参与计算**：
    # 脸是约束不是目标（§28 L1），落点只由 T4 的区边界决定。
    """一次算到位。**脸是约束，不是目标**（设计 §28 L1）。

    2026-10-29 改（三处，都对着设计）：
      ① **只在脸出区时才动**。原来 `target_L=72` 硬编码 = 给脸设了一个点靶，
         与 §28 L1「脸要落进合理区，但不必落到某个具体值」冲突。
         现在：区内 ⇒ 0 档；出区 ⇒ 只推到"刚进区 + 1.5 L* 余量"。
      ② **白端保护（H3）用两道独立预算，取小**，不再笼统截到余量：
         引擎余量 vs 把 p95 推到白平台的档数。差多少记 `debt_ev`，不硬顶。
      ③ 意图叠在"想要的档数"上，**同样受两道预算约束**（S4：不许把脸推出人样）。
    """
    lo = T4['L_lo'] if L_lo is None else L_lo
    hi = T4['L_hi'] if L_hi is None else L_hi
    la = pick_lookat(rec_engine)

    # ★★★ 先验余量的**可不可信**，再谈用不用它（2026-10-29 实测结论）
    #   `headroom_ev` 在上端被 `scan_white_level` 砍过时是个恒等式：
    #   headroom_ev ≡ −log2(Y(天花))，砍多少报多少，等于零，代价是高光被砍平。
    #   ⇒ 这种底片的两道预算都不可算（_cap_white 也假设平台在 L*100）。
    #     此时**不许猜**：曝光不动，写明原因，让人去把白端还原。
    if rec_engine.get('headroom_valid') is False:
        why = rec_engine.get('headroom_why') or '天花被砍，余量等于砍掉的高度（恒等式）'
        return Exposure(why='拒算：余量不可信 —— %s' % why,
                        cap_headroom=rec_engine.get('headroom_ev'))

    ex = Exposure(cap_headroom=rec_engine.get('headroom_ev'),
                  cap_white=_cap_white(rec_engine))
    caps = [c for c in (ex.cap_headroom, ex.cap_white) if c is not None]
    ex.cap_used = min(caps) if caps else None

    if la.kind != 'face':
        ex.why = '弃权：%s（整幅没有落点不变量，曝光不动）' % la.why
        return ex

    cur = (rec_engine.get('skin') or {}).get('L')
    if cur is None:
        ex.why = '弃权：脸读数为空'
        return ex

    # ---- ① 约束式：只在出区时动，目标是"刚进区"（留测量容差）
    if cur < lo:
        want_dL, side = (lo + EDGE_MARGIN_L) - cur, '暗于下界'
    elif cur > hi:
        want_dL, side = (hi - EDGE_MARGIN_L) - cur, '亮于上界'
    else:
        want_dL, side = 0.0, '区内'
    ex.want_ev = ev_for_dL(want_dL, cur)

    # ---- 意图：只改方向与幅度，不许把脸推出人样（S4）
    if intent is not None:
        ex.want_ev += getattr(intent, 'brighter', 0.0) * INTENT_EV_PER_UNIT
        newL = cur + ex.want_ev * eval_slope(cur)
        if newL > hi - EDGE_MARGIN_L:
            ex.want_ev -= ev_for_dL(newL - (hi - EDGE_MARGIN_L), cur)
        elif newL < lo + EDGE_MARGIN_L:
            ex.want_ev += ev_for_dL((lo + EDGE_MARGIN_L) - newL, cur)

    # ---- 光位说明（H4）：**两个分支都要带**，它讲的是"该不该提"，不是"能不能提"
    light_hint = ''          # 详情在 plan.notes['light']，这里只留一个短标记
    if la.light == 'backlit':
        light_hint = '（逆光，提完需预备收白）'
    elif la.light == 'frontlit' and cur < lo:
        light_hint = '（顺光但脸低 ⇒ 先查测光/卡，别靠曝光硬提）'

    # ---- ② 两道预算取小（H3 / L2）
    if ex.cap_used is not None and ex.want_ev > ex.cap_used + 1e-9:
        ex.ev = max(0.0, float(ex.cap_used))
        ex.debt_ev = round(ex.want_ev - ex.ev, 3)
        which = []
        if ex.cap_headroom is not None and ex.cap_headroom <= ex.cap_used + 1e-9:
            which.append('引擎只留了 %.2f 档余量' % ex.cap_headroom)
        if ex.cap_white is not None and ex.cap_white <= ex.cap_used + 1e-9:
            which.append('p95 距白平台只剩 %.2f 档' % ex.cap_white)
        ex.why = ('%s（脸 %.1f，要 %+.2f 档，给了 %+.2f）⇒ 欠 %.2f 档：%s%s'
                  % (side, cur, ex.want_ev, ex.ev, ex.debt_ev,
                     '、'.join(which) or '预算缺', light_hint))
    else:
        ex.ev = ex.want_ev
        ex.why = '%s（脸 %.1f）%s%s' % (side, cur, '' if not want_dL else
                                       '，目标 %.1f' % (cur + want_dL), light_hint)
    ex.ev = round(float(ex.ev), 3)
    ex.want_ev = round(float(ex.want_ev), 3)
    return ex


# ---------------------------------------------------------------- ③ 收白

def solve_highlights(pred: dict, clip_max: Optional[float] = None):
    """高光收白：**在 ①'（提完曝光之后）上判**，不在 ① 上判。

    2026-10-29 修：原来传进来的是 ① 的记录 —— 等于"先收白、后算曝光"，顺序倒了。
    现在吃 `expose.after_ev()` 的结果：
      · ① 本身就爆（引擎欠账）        ⇒ 政策值 + 记欠账
      · 提了曝光才爆（超预算，本批 9/10）⇒ 政策值 + 标"待验收"，③ 必须验 clip
      · 都没爆                        ⇒ 不动

    **不算**具体压多少：`Highlights` 没有公开传递函数（见 lrmodel.py），
    编一个斜率比不给更糟。这里给的是政策档，且明确标成 policy。
    """
    cm = T1['clip_max'] if clip_max is None else clip_max
    lb = float(pred.get('clip_lb') or 0.0)
    over = float(pred.get('over_ev') or 0.0)
    if lb <= cm and over <= 1e-9:
        return 0.0, None
    if over > 1e-9:
        return float(HIGHLIGHT_POLICY), (
            '曝光超预算 %.2f 档 ⇒ p95 会被推高（下界 %.2f%%）；'
            'Highlights 给的是政策值 %+.0f（无公开传递函数，未标定）⇒ ③ 出片必须验 clip'
            % (over, lb, HIGHLIGHT_POLICY))
    return float(HIGHLIGHT_POLICY), (
        '① 已爆（下界 %.2f%% > %.2f%%）⇒ 引擎欠账（T0 没留够余量）；'
        'LR 这里给的是政策值 %+.0f，不是算出来的' % (lb, cm, HIGHLIGHT_POLICY))


# 政策档：不是公式。等实测标定后替换（当前只保证方向：压）
HIGHLIGHT_POLICY = -35.0

# ---------------------------------------------------------------- ④ 曲线

def solve_curve(rec_engine: dict, intent: Optional[Intent] = None,
                budget: Optional[float] = None) -> Tuple[Optional[List[Tuple[int, int]]], dict]:
    """**只修不变量 + 落地意图**，不对任何"别人的分位"造形。

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

    # (a) 不变量：爆白压低端、堵黑抬趾部
    clip = img.get('clip_pct') or 0.0
    fixed, unmet = [], {}
    if clip > T1['clip_max']:
        want = clip - T1['clip_max']            # 想压多少（L*）
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
                      C_min: float = 10.0) -> Optional[Dict[str, float]]:
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
    return dict(scale=max(-1.0, min(1.0, scale)), shift=0.0)


# ---------------------------------------------------------------- 主流程

def solve_one(rec_engine: dict, rec_after_ev: Optional[dict] = None,
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
    la = pick_lookat(rec_engine)
    plan.notes['lookat'] = '%s — %s' % (la, la.why)
    if la.light != 'unknown':
        plan.notes['light'] = ('逆光（脸/背景 %.2f < %.1f）⇒ 该提的是脸，提完必须预备收白'
                               % (rec_engine.get('light_ratio'), LIGHT_BACKLIT)
                               if la.light == 'backlit' else
                               '顺光（脸/背景 %.2f > %.1f）⇒ 只做微调'
                               % (rec_engine.get('light_ratio'), LIGHT_FRONTLIT))

    ex = solve_exposure(rec_engine, intent=intent)
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
        plan.notes['highlight_debt'] = hnote

    # 曲线看的是 ①'（等价于"提完曝光再修不变量"），否则曲线会去修一个已经不存在的问题
    rec_for_curve = dict(rec_engine)
    rec_for_curve['img'] = dict(pred['pred']['img'])
    if rec_for_curve['img'].get('p1') is None:
        rec_for_curve['img']['p1'] = (rec_engine.get('img') or {}).get('p1')
    pts, info = solve_curve(rec_for_curve, intent=intent)
    plan.curve = pts
    plan.notes['curve'] = ('无需' if pts is None else
                           '修正 %s ｜ 最大位移 %.2f' % (info.get('fixed') or '意图',
                                                       info.get('max_abs_delta') or 0))
    plan.saturation = round(solve_saturation(rec_engine, intent), 1)

    # 点颜色吃 ①' 的肤色（② 的真值算不了，这是最接近的一层）
    rec_pc = rec_final or dict(rec_engine, skin=pred['pred']['skin'])
    pc = solve_point_color(rec_pc, intent=intent)
    if pc and (abs(pc['scale']) > 1e-6 or abs(pc['shift']) > 1e-6):
        plan.point_color = pc
        rgb = (rec_pc.get('skin') or {}).get('rgb')
        if rgb:
            from .curve import apply_curve_rgb
            plan.point_color_rgb = list(apply_curve_rgb(rgb, plan.curve or []))
            plan.notes['point_color_rgb_src'] = '②处（①的取样色过曲线 LUT）'
    return plan
