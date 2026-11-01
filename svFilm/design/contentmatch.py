# -*- coding: utf-8 -*-
"""机内 JPG 内容匹配（v0.3）：**不渲 ①′**，直接用 RAW 内嵌 JPG 做内容匹配。

流程：
    RAW → 内嵌 JPG（或同名 sidecar）→ 1080 副本 → design.measure_all
        → batch_content（人给或自动粗分）→ card_content.rank → 候选页

用法：
    from design.contentmatch import match_jpgs
    res = match_jpgs(r'D:\\照片库\\效果测试1027_2', r'E:\\ds\\photo\\_run_xxx',
                     content_path=r'...\\batch_content.json', topn=5)

纪律：
  · 不解 RAW、不渲 ①′；只读机内 JPG。
  · 自动 batch_content 只判 scene/palette/elements；服装必须人给（--content）。
  · **光位/光向量只诊断，不进内容匹配分数**（技能 §194/§195：光位不驱动影调）。
  · 候选页只排序/给理由/给样片，不代选。
"""
from __future__ import annotations

import glob
import json
import os
import time
from typing import Dict, Optional

RAW_EXT = ('*.RAF', '*.raf', '*.NEF', '*.nef', '*.CR2', '*.cr2',
           '*.CR3', '*.cr3', '*.ARW', '*.arw', '*.DNG', '*.dng')


def find_raws(raf_dir: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for e in RAW_EXT:
        for p in glob.glob(os.path.join(raf_dir, '**', e), recursive=True):
            out.setdefault(os.path.splitext(os.path.basename(p))[0], p)
    return dict(sorted(out.items()))


def _sidecar(raf: str) -> Optional[str]:
    stem = os.path.splitext(raf)[0]
    for ext in ('.JPG', '.jpg', '.JPEG', '.jpeg'):
        p = stem + ext
        if os.path.isfile(p):
            return p
    return None


def _extract_one(raf: str, stem: str, out_dir: str) -> str:
    """RAW → 机内 JPG（全尺寸）。返回路径。已有同名 sidecar 就直接用。"""
    side = _sidecar(raf)
    if side:
        return side
    import rawpy
    from PIL import Image
    import io
    with rawpy.imread(raf) as r:
        t = r.extract_thumb()
    dst = os.path.join(out_dir, stem + '.jpg')
    if t.format == rawpy.ThumbFormat.JPEG:
        with open(dst, 'wb') as fh:
            fh.write(t.data)
    else:
        Image.fromarray(t.data).convert('RGB').save(dst, quality=95)
    return dst


def _to1080(src: str, dst: str) -> str:
    from PIL import Image
    im = Image.open(src).convert('RGB')
    k = 1080.0 / max(im.size)
    if k < 1.0:
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))),
                       Image.LANCZOS)
    im.save(dst, quality=95, subsampling=0)
    return dst


def extract_jpgs(raws: Dict[str, str], out_dir: str, log=print) -> Dict[str, dict]:
    """{stem: {'src': 原JPG, 'jpg1080': 1080副本}}。"""
    jpg_dir = os.path.join(out_dir, 'jpg')
    small_dir = os.path.join(out_dir, 'jpg1080')
    os.makedirs(jpg_dir, exist_ok=True)
    os.makedirs(small_dir, exist_ok=True)
    out = {}
    for stem, raf in raws.items():
        src = _extract_one(raf, stem, jpg_dir)
        small = _to1080(src, os.path.join(small_dir, stem + '.jpg'))
        out[stem] = dict(src=src, jpg1080=small)
        log('  JPG %s ← %s' % (stem, os.path.basename(src)))
    return out


def measure_jpgs(jpgs: Dict[str, dict], log=print) -> Dict[str, dict]:
    """用 design.measure_all 量 1080 副本（与大师成片同一把尺子）。"""
    from .measure_all import measure_one
    out = {}
    for i, (stem, d) in enumerate(sorted(jpgs.items()), 1):
        rec = measure_one(d['jpg1080'])
        rec['stem'] = stem
        rec['file'] = d['jpg1080']
        out[stem] = rec
        if i % 10 == 0:
            log('  量 %d/%d' % (i, len(jpgs)))
    return out


def match_jpgs(raf_dir: str, out_dir: str, content_path: str = '',
               topn: int = 10, limit: int = 0, log=print) -> dict:
    """跑完整的内容匹配；不渲一张图。"""
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    raws = find_raws(raf_dir)
    if limit:
        raws = dict(list(raws.items())[:limit])
    if not raws:
        raise RuntimeError('没有 RAW：%s' % raf_dir)
    log('批次 %s：%d 张 RAW（机内 JPG 内容匹配，不跑 ①′）'
        % (os.path.basename(os.path.normpath(raf_dir)), len(raws)))
    jpgs = extract_jpgs(raws, out_dir, log=log)
    measures = measure_jpgs(jpgs, log=log)
    from . import card_content
    if content_path:
        batch = card_content.load_batch(content_path)
        log('  batch_content ← %s' % content_path)
    else:
        batch = card_content.batch_from_measures(measures)
        log('  batch_content ← 自动粗分（服装/光位 unknown；建议 --content 覆盖）')
    hits = card_content.rank(batch, topn=topn)
    if not hits:
        n_manual = sum(1 for c in card_content.load().values()
                       if c.get('source') == 'manual')
        raise RuntimeError(
            '内容卡覆盖不足：人标卡 %d 张。先标 5 张（card_content_manual.json）'
            '或加 include_auto 做诊断；不允许退回基线渲（设计：不单独跑一次）。'
            % n_manual)
    doc = dict(batch=os.path.basename(os.path.normpath(raf_dir)),
               generated=time.strftime('%Y-%m-%d %H:%M:%S'),
               raf_dir=raf_dir, n=len(raws), batch_content=batch,
               hits=[dict(card=h['card'], score=h['score'], dims=h['dims'],
                          reason=h['reason'], cost=h['cost'],
                          confidence=h.get('confidence'), source=h.get('source'),
                          variant=h.get('variant'), mixed=h.get('mixed'))
                     for h in hits])
    mp = os.path.join(out_dir, 'match')
    os.makedirs(mp, exist_ok=True)
    # 测量另存：content_match.json 只留匹配结论，别把 19 张的整包塞进去
    mpj = os.path.join(mp, 'jpg_measure.json')
    tmpj = mpj + '.tmp'
    with open(tmpj, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(dict(per_image=measures), fh, ensure_ascii=False, indent=1)
    os.replace(tmpj, mpj)
    tmp = os.path.join(mp, 'content_match.json') + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(mp, 'content_match.json'))
    from .matchpage import make_content_page
    page = make_content_page(batch, hits, {k: v['src'] for k, v in jpgs.items()},
                             out_dir, title='%s · 内容匹配（机内 JPG）'
                             % os.path.basename(os.path.normpath(raf_dir)),
                             all_cards=sorted(card_content.load().keys()), log=log)
    log('内容匹配完成：%d 张 JPG，%.0f 秒' % (len(jpgs), time.time() - t0))
    return dict(batch=batch, hits=hits, page=page, measures=measures,
                jpgs=jpgs, n=len(raws))
