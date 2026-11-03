# -*- coding: utf-8 -*-
"""批次：RAW 目录 → 一次 ① 渲染 + 交付域测量。**不驱动 LR**。

v0.3 流程（`设计真源.md` §3）：
    b = Batch.of(raf_dir, out_dir)
    res = contentmatch.match_jpgs(...)      # 匹配 A：机内 JPG，不渲 ①′
    # 人拍板 card / hero
    recs  = run_engine(b, card)             # 只渲一次 ①（交付尺寸）
    peers = peer.build(recs, card)          # 同构图 + 连续光向量
    hero_target = peer.target_from_rec(measure_one(b.hero)) if b.hero else None
    plans = lr_solve.plan_batch(recs, card, peers, hero_target=hero_target)
    jobs  = lrsink.write_jobs(plans, out_dir)   # XMP 作业单
"""
from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .flow import render_card

RAW_EXT = ('*.RAF', '*.raf', '*.NEF', '*.CR2', '*.CR3', '*.ARW', '*.DNG')


@dataclass
class Batch:
    name: str
    raf_dir: str
    out_dir: str
    stems: List[str] = field(default_factory=list)
    # ★ 人拍板的两件事：选哪张卡 + 哪张样片当 hero。
    #   hero = 目检参考 + peer 不足时的关系兜底（不拿绝对 p50 当靶）。
    card: str = ''
    hero: str = ''

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

    def write_meta(self, **kv) -> str:
        """批次元数据（card / hero …）落到 `<out>/batch.json`。

        原子写：先写临时文件再替换（纪律：改 JSON 要原子写，别写半个文件）。
        """
        import json
        import time
        os.makedirs(self.out_dir, exist_ok=True)
        d = dict(batch=self.name, raf=self.raf_dir, n=len(self.stems),
                 card=self.card, hero=self.hero,
                 created=time.strftime('%Y-%m-%d %H:%M:%S'))
        d.update({k: v for k, v in kv.items() if v not in (None, '')})
        p = os.path.join(self.out_dir, 'batch.json')
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return p

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

# 交付域（`测量设计.md` §1.1）：sRGB / 长边 1080。
# 与卡比对的量必须在这个域里量；16bit TIF 只用于物理底线（死白/p1）。
DELIVERY_LONG = 1080


def _delivery_copy(path: str) -> str:
    """TIF/大图 → sRGB / 长边 1080 的交付域副本（测量用）。

    ★ 存 PNG 而不是 JPEG：交付域的关键是 **sRGB + 1080**；JPEG 压缩噪声会让
      YuNet 在小脸上误检（实测 DSCF2500：1080 JPEG 把右上角空调外机当成脸，
      box 185×234；同一像素的 1080 PNG 检出正确的人脸 60×82）。
      测量要的是"同一工件、同一分辨率"，不是"再加一层压缩噪声"。
    """
    import hashlib
    import tempfile
    from PIL import Image
    st = os.stat(path)
    key = hashlib.sha1(('%s|%d|%d' % (os.path.abspath(path), st.st_mtime_ns,
                                      st.st_size)).encode('utf-8')).hexdigest()[:16]
    root = os.path.join(tempfile.gettempdir(), 'svfilm_delivery')
    os.makedirs(root, exist_ok=True)
    dst = os.path.join(root, key + '.png')
    if os.path.isfile(dst):
        return dst
    im = Image.open(path).convert('RGB')
    k = DELIVERY_LONG / float(max(im.size))
    if k < 1.0:
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))),
                       Image.LANCZOS)
    tmp = dst + '.tmp'
    im.save(tmp, 'PNG')
    os.replace(tmp, dst)
    return dst


def measure_tif(path: str) -> dict:
    """量一张出片 —— **统一用 design.measure_all 一把尺子 + 交付域副本**。

    ★ 2026-11-01 v0.3：与卡比对的量必须在交付域（sRGB/1080）量；
      直接量 2048 的 16bit TIF 与大师的 1080 JPEG 不是同一工件。
    """
    import json
    from .measure_all import measure_one as _measure
    rec = _measure(_delivery_copy(path))
    rec['file'] = os.path.basename(path)
    if rec.get('err'):
        return rec
    sk = rec.get('skin') or {}
    ps = rec.get('person') or {}
    bg = rec.get('bg') or {}
    face = rec.get('face') or {}
    # 扁平别名（给 plan/peer 的接口）
    rec['face_L'] = sk.get('L')
    rec['face_C'] = sk.get('C')
    rec['face_h'] = sk.get('h')
    rec['face_b'] = sk.get('b')
    rec['person_L'] = ps.get('L')
    rec['bg_L'] = bg.get('L')
    rec['bg_L_p90'] = bg.get('L_p90')
    rec['bg_L_p95'] = bg.get('L_p95')
    rec['face_px'] = face.get('n')
    rec['person_px'] = int(ps.get('share', 0) * (rec.get('meta', {}).get('orig', [1, 1])[0]
                                                  * rec.get('meta', {}).get('orig', [1, 1])[1]))
    rec['bg_px'] = bg.get('n')
    rec['n_px'] = (rec.get('meta', {}).get('orig', [1, 1])[0]
                   * rec.get('meta', {}).get('orig', [1, 1])[1])
    rec['face_p5'] = rec.get('face_p5')
    rec['face_p95'] = rec.get('face_p95')
    rec['face_p99'] = rec.get('face_p99')
    rec['person_p95'] = rec.get('person_p95')
    rec['person_p99'] = rec.get('person_p99')
    rec['person_clip_pct'] = ps.get('clip_pct')
    rec['face_over_bg'] = (round(float(sk['L']) / float(bg['L']), 3)
                           if sk.get('L') and bg.get('L') else None)
    rec['person_over_bg'] = (round(float(ps['L']) / float(bg['L']), 3)
                             if ps.get('L') and bg.get('L') else None)
    rec['skin'] = dict(sk, p5=rec.get('face_p5'), p95=rec.get('face_p95'),
                       p99=rec.get('face_p99'), px=face.get('n'))
    rec['person'] = dict(ps, p95=rec.get('person_p95'), p99=rec.get('person_p99'),
                         px=rec.get('person_px'),
                         clip_pct=rec.get('person_clip_pct'))
    rec['bg'] = dict(bg)
    # 引擎渲完会写一张 sidecar：这张底片还能抬几档
    side = path.replace('_engine.tif', '_headroom.json')
    if os.path.isfile(side):
        try:
            rec['headroom_ev'] = float(json.load(open(side, encoding='utf-8'))['headroom_ev'])
        except Exception:
            pass
    return rec


# ---------------------------------------------------------------- 整批

def run_engine(b: Batch, card, measure_fn=None, limit: int = 0,
               skip: Optional[List[str]] = None, log=print,
               targets: Optional[Dict[str, float]] = None,
               table: Optional[dict] = None,
               stock: str = '', paper: str = '') -> Dict[str, dict]:
    """整批渲 + 立刻量（交付域），返回 ① 的记录。

    `stock`/`paper`：只跑介质对比时临时覆盖卡的卷/纸。
    """
    stems = b.stems[:limit] if limit else b.stems
    if skip:
        stems = [s for s in stems if s not in set(skip)]
    paths = b.raf_paths(stems)
    if not paths:
        raise RuntimeError('没有匹配到 RAW：%s' % b.raf_dir)
    t0 = time.time()
    if table is None:
        from . import chart_calib
        cname = card.name if hasattr(card, 'name') else str(card.get('name'))
        table = chart_calib.load_card_table(cname)
    tifs = render_card(card, paths, b.sub('engine'), log=log,
                       targets=targets, table=table, stock=stock, paper=paper)
    mf = measure_fn or measure_tif
    recs = {}
    for st, p in tifs.items():
        r = mf(p)
        r['file'] = os.path.basename(p)
        # ★ 没脸的图也要留着（照样出片；只在 LR 计划里记账"缺脸"）
        recs[st] = r
    dt = time.time() - t0
    log('整批：%d 张渲完，%d 张有效，%.0f 秒（%.0f 秒/张）'
        % (len(tifs), len(recs), dt, dt / max(len(tifs), 1)))
    return recs


def measure_engine_dir(b: Batch, log=print, expect: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
    """复用已经渲好的 ①（只重测，不重渲）。

    用途：只改 LR 段（颜色/白端/曲线）时，引擎那一步不用重跑 —— ① 只取决于卡的介质，
    介质没变就不该重渲（一次几分钟，还白白多一遍噪声）。

    ★ `expect`（如 `{'card': ..., 'wb': 'as_shot', 'wb_calibrated': True}`）：和 `engine/_render.json` 比对，
      不一致就抛。没有这道校验，换卡/改 WB/改 amount 之后复用旧底片会**静默**算错整批。
    """
    import glob
    import json
    eng = b.sub('engine')
    meta_p = os.path.join(eng, '_render.json')
    meta = None
    if os.path.isfile(meta_p):
        try:
            meta = json.load(open(meta_p, encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            meta = None
    if expect:
        if meta is None:
            raise RuntimeError('复用被拒：找不到 %s（不知道这批 ① 是用什么渲的）' % meta_p)
        for k, v in expect.items():
            if v and meta.get(k) != v:
                raise RuntimeError(
                    '复用被拒：这批 ① 是 %s=%r 渲的，现在要 %r。'
                    '换卡/改 WB/改 amount 之后必须重渲。' % (k, meta.get(k), v))
    recs = {}
    for p in sorted(glob.glob(os.path.join(eng, '*_engine.tif'))):
        st = os.path.basename(p)[:-len('_engine.tif')]
        if st not in b.stems:
            continue
        r = measure_tif(p)
        r['file'] = os.path.basename(p)
        recs[st] = r
        log('  复用 ① %s' % st)
    return recs


def build_review(b: Batch, recs: Dict[str, dict], plans: Dict[str, object],
                 recs3: Dict[str, dict], log=print) -> Dict[str, object]:
    """出目检页（**不判过/不过**）。逻辑在 `review.make_review`（不依赖 Batch）。"""
    from .review import make_review
    return make_review(recs, recs3, plans, b.out_dir,
                       tif_1_dir=b.sub('engine'), tif_3_dir=b.sub('final'),
                       title='%s · 成片目检' % b.name, hero=b.hero, log=log)

def estimate(n: int, per_image_s: float = 8.0) -> str:
    """给个时间预估（1080 交付尺寸，约 5~10 秒/张）。"""
    t = n * per_image_s
    return '%d 张 ≈ %.0f 秒（%.1f 分）' % (n, t, t / 60.0)
