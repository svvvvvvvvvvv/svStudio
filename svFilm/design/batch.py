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
    P = r'E:\svStudio\svFilm\analysis\corpus\kurai'
    if P not in sys.path:
        sys.path.insert(0, P)
    # ★ 2026-10-31：measure_all 依赖已删的 skin_v2/person_masks，生产改用自包含的
    #   measure_v7（design.skin + mediapipe）。这里把 v7 的扁平字段补成旧接口
    #   同时保留的嵌套形状，plan_batch 和 review 都能吃。
    import measure_v7 as MA
    rec = MA.measure_one(path)
    rec.setdefault('skin', dict(
        L=rec.get('face_L'), C=rec.get('face_C'), h=rec.get('face_h'),
        b=rec.get('face_b'), p5=rec.get('face_p5'), p95=rec.get('face_p95'),
        p99=rec.get('face_p99'), px=rec.get('face_px')))
    rec.setdefault('person', dict(
        L=rec.get('person_L'), p95=rec.get('person_p95'),
        p99=rec.get('person_p99'), px=rec.get('person_px'),
        clip_pct=None))
    rec.setdefault('bg', dict(
        L=rec.get('bg_L'), L_p90=rec.get('bg_L_p90'),
        L_p95=rec.get('bg_L_p95'), px=rec.get('bg_px')))
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
    from .matchcard import cards as _mc
    tb = {c.name: c for c in _mc()}
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
    from .flow import _Hit
    return _Hit(card, 0.0)


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



def _light_kind(p_engine, p_final):
    """光位：先 **light_probe**（成片口径），拿不到再退回 `light_state`。

    为什么换：`light_state` 只判得清极端情况（dark≥0.35 且 gap≥12，或 gap≥35），
    中间一律 unknown ⇒ 今天这批 10 张只判出 4 张，补光档因此几乎不触发。
    `light_probe` 用的是"脸上梯度 + 近邻背景 + 发丝"三个量，10-29 三百张目检过。
    """
    try:
        import light_probe as LP
        # ★ 只看 **①**（我们还没动手的那张）：光位必须**前馈**。
        #   拿 ③ 判就是"改完再看自己改得对不对"，会自己跟自己反馈（手册那条振荡）。
        for p in (p_engine, p_final):
            if p and os.path.isfile(p):
                k, _, _, _ = LP.classify(LP.probe(p))
                if k and k != 'unknown':
                    return k
    except Exception:                                      # noqa: BLE001
        pass
    return 'unknown'



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
