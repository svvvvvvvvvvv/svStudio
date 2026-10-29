# -*- coding: utf-8 -*-
"""批次：从 RAW 目录一路跑到 XMP 作业单。**不驱动 LR**。

这一段存在的理由（预检发现的三个拦路石）：
  1. **鸡生蛋**：选卡要 ① 的测量，可批次开始时只有 RAW
     ⇒ 先拿现有卡渲 2 张"试点"，用它们的**场景侧量**（天空/爆白/光比）算需求。
       试点卡的风格不会污染这个判断 —— 因为需求只用场景量，不用颜色偏差。
  2. 渲完不会自动量 ⇒ 这里渲完立刻量。
  3. 没有批次清单 ⇒ 这里从目录生成。

流程：
    b = Batch.of(raf_dir, out_dir)
    recs0 = pilot(b, n=2)                 # 试点渲 2 张 -> ①
    offer = choose(recs0, intent)         # 选卡
    recs  = run_engine(b, offer.best())   # 整批渲 -> ①
    plans = plan_lr(recs, intent)         # LR 计划
    jobs  = write_jobs(plans, out_dir)    # XMP 作业单

用法：
    from design.batch import Batch, pilot, run_engine, plan_lr
"""
from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import measure as M
from .cards import cards as all_cards
from .flow import Choice, choose, render_card
from .intent import Intent
from .lr import LRPlan, solve_one
from .lrsink import write_jobs

RAW_EXT = ('*.RAF', '*.raf', '*.NEF', '*.CR2', '*.CR3', '*.ARW', '*.DNG')


@dataclass
class Batch:
    name: str
    raf_dir: str
    out_dir: str
    stems: List[str] = field(default_factory=list)

    @classmethod
    def of(cls, raf_dir: str, out_dir: str, limit: int = 0) -> 'Batch':
        rafs = {}
        for e in RAW_EXT:
            for p in glob.glob(os.path.join(raf_dir, '**', e), recursive=True):
                rafs.setdefault(os.path.splitext(os.path.basename(p))[0], p)
        stems = sorted(rafs)
        if limit:
            stems = stems[:limit]
        name = os.path.basename(os.path.normpath(raf_dir))
        return cls(name=name, raf_dir=raf_dir, out_dir=out_dir, stems=stems)

    def raf_paths(self, stems: Optional[List[str]] = None) -> Dict[str, str]:
        want = set(stems or self.stems)
        out = {}
        for e in RAW_EXT:
            for p in glob.glob(os.path.join(self.raf_dir, '**', e), recursive=True):
                st = os.path.splitext(os.path.basename(p))[0]
                if st in want:
                    out[st] = p
        return out

    def sub(self, key: str) -> str:
        d = os.path.join(self.out_dir, key)
        os.makedirs(d, exist_ok=True)
        return d


# ---------------------------------------------------------------- 测量

def measure_tif(path: str, with_bg: bool = True, fixed_xy=None) -> dict:
    """渲出来的 TIF 立刻量。

    `with_bg=True` 是**必须的**（2026-10-29 真跑发现）：没有背景就**没有 `light_ratio`**，
    `light_state()` 永远返回 unknown ⇒ 逆光补偿、光位提示全是死的。
    代价很小（9 张量背景约 1 秒/张）。
    """
    import sys
    P = r'E:\ds\photo\_corpus\kurai\full'
    if P not in sys.path:
        sys.path.insert(0, P)
    import measure_all as MA
    rec = MA.measure_one(path, with_bg=with_bg, fixed_xy=fixed_xy)
    # 引擎渲完会写一张 sidecar：这张底片还能抬几档
    import json
    side = path.replace('_engine.tif', '_headroom.json')
    if os.path.isfile(side):
        try:
            rec['headroom_ev'] = float(json.load(open(side, encoding='utf-8'))['headroom_ev'])
        except Exception:
            pass
    return rec


# ---------------------------------------------------------------- 试点（解鸡生蛋）

def pilot(b: Batch, n: int = 2, card: Optional[str] = None,
          measure_fn=None, log=print) -> Dict[str, dict]:
    """拿现有卡渲 n 张，得到 ① 的测量，供选卡用。

    为什么试点卡的风格不影响选卡：`need_from_batch` 只读**场景侧量**
    （天空占比 / 爆白 / 光比），不读我们自己的颜色偏差。
    """
    tb = all_cards()
    name = card or sorted(tb)[0]
    if name not in tb:
        raise KeyError('没有这张卡：%s（现有：%s）' % (name, ', '.join(tb)))
    stems = b.stems[:max(1, n)]
    if not stems:
        raise RuntimeError('RAF 目录里没有可用的 RAW：%s' % b.raf_dir)
    log('试点：卡 %s ｜ %d 张（%s）' % (name, len(stems), ', '.join(stems)))
    ch = Choice(hit=_fake_hit(tb[name]), texture=_fake_texture(tb[name]))
    tifs = render_card(ch, b.raf_paths(stems), b.sub('engine'), log=log)
    mf = measure_fn or measure_tif
    recs = {}
    for st, p in tifs.items():
        r = mf(p)
        r['file'] = os.path.basename(p)
        # ★ **没脸的图也要留着**（10-29 真跑发现 DSCF1726 整张被漏掉）：
        #   `pick_lookat` 会给它"整幅 + 弃权 0 档"，照样出片；
        #   在这里按皮肤过滤 = 这张图干脆没有 LR 计划、也就不出 ③。
        recs[st] = r
    log('试点完成：%d/%d 张有效' % (len(recs), len(tifs)))
    log('! 试点卡的爆白率会影响 shoulder 需求（试点只渲 %d 张，代表性有限）' % len(stems))
    return recs


def _fake_hit(card):
    from .cards_match import CardHit
    return CardHit(card=card, score=0.0)


def _fake_texture(card):
    from .texture import plan_texture
    return plan_texture(card.stock, card.paper, long_side=1080)


# ---------------------------------------------------------------- 整批

def run_engine(b: Batch, choice: Choice, measure_fn=None, limit: int = 0,
               skip: Optional[List[str]] = None, log=print) -> Dict[str, dict]:
    """整批渲 + 立刻量，返回 ① 的记录。

    skip：试点已经渲过的 stem（同卡同参数，不必重渲；但要重新量）。
    """
    stems = b.stems[:limit] if limit else b.stems
    if skip:
        stems = [s for s in stems if s not in set(skip)]
    paths = b.raf_paths(stems)
    if not paths:
        raise RuntimeError('没有匹配到 RAW：%s' % b.raf_dir)
    t0 = time.time()
    tifs = render_card(choice, paths, b.sub('engine'), log=log)
    mf = measure_fn or measure_tif
    recs = {}
    for st, p in tifs.items():
        r = mf(p)
        r['file'] = os.path.basename(p)
        # ★ **没脸的图也要留着**（10-29 真跑发现 DSCF1726 整张被漏掉）：
        #   `pick_lookat` 会给它"整幅 + 弃权 0 档"，照样出片；
        #   在这里按皮肤过滤 = 这张图干脆没有 LR 计划、也就不出 ③。
        recs[st] = r
    dt = time.time() - t0
    log('整批：%d 张渲完，%d 张有效，%.0f 秒（%.0f 秒/张）'
        % (len(tifs), len(recs), dt, dt / max(len(tifs), 1)))
    return recs


def plan_lr(recs: Dict[str, dict], intent: Optional[Intent] = None,
            light: Optional[str] = None, log=print) -> Dict[str, LRPlan]:
    """① -> LR 计划（逐图）。"""
    plans = {}
    for st, rec in recs.items():
        plans[st] = solve_one(rec, intent=intent, light=light, log=lambda *_: None)
    log('LR 计划：%d 张' % len(plans))
    return plans


def run_lr(b: Batch, recs: Dict[str, dict], intent: Optional[Intent] = None,
           light: Optional[str] = None, log=print) -> Dict[str, object]:
    """① -> 计划 -> XMP 作业单（不驱动 LR）。"""
    plans = plan_lr(recs, intent=intent, light=light, log=log)
    mf = write_jobs(plans, b.sub('xmp'),
                    manifest_extra=dict(batch=b.name,
                                        card=(list(plans)[0] if plans else None),
                                        intent=(intent.as_dict() if intent else None)))
    return dict(plans=plans, manifest=mf)


# ---------------------------------------------------------------- ③ 出片 + 量 + 验收

def run_final(b: Batch, recs: Dict[str, dict], plans: Dict[str, object],
              rounds: int = 2, measure_fn=None, light: Optional[str] = None,
              log=print) -> Dict[str, object]:
    """**导出 ③ → 量 ③ → 按"脸优先"决定要不要再导一轮。**

    这是流程里唯一驱动 LR 的地方（纪律：不许拿 LR 当标定台）。
    每一轮都从**同一张 ① 底片**重导，所以不会累积副作用。

    收白的进退沿用 `lr.next_highlights`：脸被压暗 ⇒ 回退；脸没掉还爆 ⇒ 加大；
    到尽头 ⇒ 接受背景过曝。最多 `rounds` 轮，避免来回打。
    """
    from .lr import (next_highlights, next_point_lum, zone_for, light_state,
                     LAND_TOL_L, PC_LUM_MAX)
    from .lrsink import apply_and_export

    mf = measure_fn or measure_tif
    engine_dir = b.sub('engine')
    final_dir = b.sub('final')
    recs3: Dict[str, dict] = {}
    used = 0

    for rnd in range(max(1, rounds)):
        pairs = [(st, os.path.join(engine_dir, st + '_engine.tif'), p)
                 for st, p in plans.items()
                 if os.path.isfile(os.path.join(engine_dir, st + '_engine.tif'))]
        if not pairs:
            raise RuntimeError('没有 ① 底片可导：%s' % engine_dir)
        log('③ 第 %d 轮：导出 %d 张（LR）' % (rnd + 1, len(pairs)))
        got = apply_and_export(pairs, final_dir, log=log)
        used = rnd + 1
        recs3 = {}
        for st, p in got.items():
            # ★ ③ 的取样点**固定用 ① 的那个像素**：否则"挑最平整的点"会在
            #   不同处理结果上跳到另一侧颊（真跑里 1307 一轮读 39.7、底片是 73.5）
            xy = ((recs.get(st) or {}).get('skin') or {}).get('xy')
            try:
                r = mf(p, fixed_xy=xy) if xy else mf(p)
            except TypeError:
                r = mf(p)
            r['file'] = os.path.basename(p)
            recs3[st] = r

        # ★ 轮廓光保护：逆光的"魂"是发丝边缘那圈亮边。收白如果把它压掉，
        #   光比是平了，氛围也死了（实操资料一致口径）。这里只**报警**，不自动回退
        #   —— 回退多少没有依据。
        for st, rec in recs3.items():
            r0 = ((recs.get(st) or {}).get('rim') or {}).get('bright_share')
            r1 = (rec.get('rim') or {}).get('bright_share')
            if r0 and r1 is not None and r1 < r0 * 0.5:
                log('   ! %s 轮廓光被吃掉一半以上（亮边占比 %.2f → %.2f）⇒ 收白可能压过头'
                    % (st, r0, r1))

        changed = 0
        for st, rec in recs3.items():
            plan = plans[st]
            s3 = rec.get('skin') or {}
            s1 = (recs.get(st) or {}).get('skin') or {}
            # ★ 仲裁：脸不够亮、且肤色亮度还没推满 ⇒ **这一轮不动收白**，
            #   先让 ⑥ 去补（它不动背景，不会反过来压脸）。加满才轮到回退收白。
            _lt = light_state(dict(recs.get(st) or {}), given=light)
            _tgt = zone_for(_lt)['L_lo'] + LAND_TOL_L
            _pc = getattr(plan, 'point_color', None) or {}
            _lum = float(_pc.get('lum', 0.0) or 0.0)
            _face_short = s3.get('L') is not None and float(s3['L']) < _tgt
            _lum_can = _lum < PC_LUM_MAX - 1e-9
            if _face_short and _lum_can:
                nv, why = None, ''
            else:
                nv, why = next_highlights(
                    float(getattr(plan, 'highlights', 0.0) or 0.0),
                    float((rec.get('img') or {}).get('clip_pct') or 0.0),
                    face_after=s3.get('L'), face_before=s1.get('L'))
            if nv is not None and abs(float(nv) - float(getattr(plan, 'highlights', 0.0))) > 1e-9:
                log('   ↻ %s 收白 %+.0f → %+.0f ｜ %s' % (st, plan.highlights, nv, why))
                plan.highlights = float(nv)
                changed += 1
            # ⑥ 肤色亮度：全局曝光 + 收白都定了，脸还差多少就用它补（只动肤色）
            #   ★ 仲裁规矩（10-29 真跑吃过亏：收白和它互相抢，1665 打了两轮 −25→−12→−56）：
            #     脸不够亮 ⇒ **先加肤色亮度**（不动背景），加满才轮到回退收白。
            target = _tgt      # 分光位的下界 + 落点容差（见上）
            rgb = ((recs.get(st) or {}).get('skin') or {}).get('rgb')
            if s3.get('L') is not None and rgb:
                pc = getattr(plan, 'point_color', None) or dict(scale=0.0, shift=0.0, lum=0.0)
                cur_lum = float(pc.get('lum', 0.0) or 0.0)
                lv, lwhy = next_point_lum(cur_lum, s3.get('L'), target)
                if lv is not None and abs(lv - cur_lum) > 1e-9:
                    log('   ↻ %s 肤色亮度 %+.2f → %+.2f ｜ %s'
                        % (st, cur_lum, lv, lwhy))
                    pc['lum'] = float(lv)
                    plan.point_color = pc
                    plan.point_color_rgb = list(rgb)
                    changed += 1
        if not changed:
            log('   收白都定了，不再导')
            break
        write_jobs(plans, b.sub('xmp'),
                   manifest_extra=dict(batch=b.name, final_round=rnd + 2))
    return dict(recs3=recs3, rounds=used)


def build_review(b: Batch, recs: Dict[str, dict], plans: Dict[str, object],
                 recs3: Dict[str, dict], light: Optional[str] = None,
                 log=print) -> Dict[str, object]:
    """验收 + 目检页（G2/G3）。逻辑在 `review.make_review`（不依赖 Batch，能单独用）。"""
    from .review import make_review
    return make_review(recs, recs3, plans, b.out_dir,
                       tif_1_dir=b.sub('engine'), tif_3_dir=b.sub('final'),
                       title='%s · 成片目检' % b.name, light=light, log=log)

def estimate(n: int, per_image_s: float = 25.0) -> str:
    """给个时间预估（渲一张要跑两次 headroom 解，约 22~30 秒）。"""
    t = n * per_image_s
    return '%d 张 ≈ %.0f 秒（%.1f 分）' % (n, t, t / 60.0)
