# -*- coding: utf-8 -*-
"""按**大师作品的中性轴色偏**给每张卡解一个印相滤片偏移（颜色交给卷纸/引擎）。

思路（`卷纸测量.md` §5.5 的延伸）：
  · 卡侧：大师成片的 `neutral_axis`（中性面在 L*≈10/30/50/70/90 的 a*/b*）；
  · 引擎侧：把同一条中性灰阶喂进这张卡的介质，量同样的 a*/b*；
  · 差值 (Δa, Δb) 就是这张卡的颜色偏差 —— 用印相滤片 Y/M 去抵（引擎自带旋钮）。

滤片响应：先测一次「ΔY/ΔM → Δa/Δb」的线性响应（同一灰阶、同一 PE），再逐卡解。

用法：
  python analysis/medium/cast_calib.py --dry-run
  python analysis/medium/cast_calib.py --apply
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import color_match as CM

LEVELS = [10.0, 30.0, 50.0, 70.0, 90.0]
RAMP = [0.005, 0.008, 0.012, 0.018, 0.028, 0.045, 0.07, 0.11, 0.18, 0.30, 0.50, 0.90]


def gray_img(width=720, height=240):
    img = np.zeros((height, width, 3), np.float64)
    w = width // len(RAMP)
    for i, v in enumerate(RAMP):
        x0 = i * w
        x1 = width if i == len(RAMP) - 1 else (i + 1) * w
        img[:, x0:x1] = v
    return img


def measure_cast(disp):
    """→ [(L, a, b)] 12 级的中位色度。"""
    lab = common.srgb_to_lab(disp)
    h, w = lab.shape[:2]
    n = len(RAMP)
    bw = w // n
    out = []
    for i in range(n):
        x0 = i * bw
        x1 = w if i == n - 1 else (i + 1) * bw
        px = lab[:, x0:x1].reshape(-1, 3)
        L, a, b = np.median(px, 0)
        out.append((float(L), float(a), float(b)))
    return out


def cast_at_levels(cast):
    """插值到 LEVELS：→ [(a, b)]。"""
    Ls = [c[0] for c in cast]
    As = [c[1] for c in cast]
    Bs = [c[2] for c in cast]
    return [(float(np.interp(L, Ls, As)), float(np.interp(L, Ls, Bs))) for L in LEVELS]


def render_medium(card, y_shift=0.0, m_shift=0.0, img=None, pe=1.0):
    from svFilm import presets
    md = json.loads(json.dumps(card['medium']))      # deep copy
    sim = md.setdefault('simulation', {})
    sim['print_y_filter_shift'] = float(y_shift)
    sim['print_m_filter_shift'] = float(m_shift)
    sim['auto_exposure'] = False
    sim['print_exposure'] = float(pe)
    sim['print_exposure_compensation'] = False
    sim['normalize_print_exposure'] = False
    disp = presets.render(img if img is not None else gray_img(), md,
                          print_exposure=float(pe))
    return np.asarray(disp, np.float64)


def load_card(path):
    return json.load(open(path, encoding='utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    cards = CM.card_features()                       # 卡侧中性轴 + 肤色等
    files = sorted(glob.glob(str(common.CARDS / '*.json')))
    if args.limit:
        files = files[:args.limit]

    img = gray_img()
    # ---- 1) 滤片响应（用第一张卡测；同一引擎、同一 PE，够用）
    c0 = load_card(files[0])
    base = cast_at_levels(measure_cast(render_medium(c0, 0.0, 0.0, img)))
    dy = cast_at_levels(measure_cast(render_medium(c0, 0.05, 0.0, img)))
    dm = cast_at_levels(measure_cast(render_medium(c0, 0.0, 0.05, img)))
    # 每级、每个通道的偏导
    resp = []
    for i in range(len(LEVELS)):
        resp.append(dict(
            da_dy=(dy[i][0] - base[i][0]) / 0.05, db_dy=(dy[i][1] - base[i][1]) / 0.05,
            da_dm=(dm[i][0] - base[i][0]) / 0.05, db_dm=(dm[i][1] - base[i][1]) / 0.05))
    print('滤片响应（每 0.05）：ΔY → Δa %+.2f Δb %+.2f ｜ ΔM → Δa %+.2f Δb %+.2f（中间调）'
          % (resp[2]['da_dy'] * 0.05, resp[2]['db_dy'] * 0.05,
             resp[2]['da_dm'] * 0.05, resp[2]['db_dm'] * 0.05))

    rows = []
    for f in files:
        card = load_card(f)
        name = card['name']
        feat = cards.get(name) or {}
        target = [feat['cast'].get(L) for L in LEVELS]
        if any(t is None for t in target):
            print('跳过（卡无中性轴）：', name)
            continue
        cur = cast_at_levels(measure_cast(render_medium(card, 0.0, 0.0, img)))
        # 逐级解 (Δy, Δm)：最小二乘 A·[y,m] = -(cur - target)
        A = []
        rhs = []
        for i in range(len(LEVELS)):
            ta, tb = target[i]
            ca, cb = cur[i]
            A.append([resp[i]['da_dy'], resp[i]['da_dm']])
            rhs.append(-(ca - ta))
            A.append([resp[i]['db_dy'], resp[i]['db_dm']])
            rhs.append(-(cb - tb))
        A = np.array(A, float)
        rhs = np.array(rhs, float)
        sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
        y, m = float(sol[0]), float(sol[1])
        y = float(np.clip(y, -0.08, 0.08))
        m = float(np.clip(m, -0.08, 0.08))
        # 解完复核
        chk = cast_at_levels(measure_cast(render_medium(card, y, m, img)))
        err0 = float(np.mean([abs(cur[i][0] - target[i][0]) + abs(cur[i][1] - target[i][1])
                              for i in range(len(LEVELS))]) / 2)
        err1 = float(np.mean([abs(chk[i][0] - target[i][0]) + abs(chk[i][1] - target[i][1])
                              for i in range(len(LEVELS))]) / 2)
        rows.append(dict(card=name, y=round(y, 4), m=round(m, 4),
                         err_before=round(err0, 2), err_after=round(err1, 2)))
        print('%-30s ΔY %+.4f ΔM %+.4f ｜ 色偏 %.2f → %.2f' % (name[:30], y, m, err0, err1))

    os.makedirs(args.out, exist_ok=True)
    rep = os.path.join(args.out, 'cast_calib.json')
    with open(rep, 'w', encoding='utf-8') as fh:
        json.dump(dict(response=resp, rows=rows), fh, ensure_ascii=False, indent=1)
    e0 = np.median([r['err_before'] for r in rows])
    e1 = np.median([r['err_after'] for r in rows])
    print('\n色偏误差中位：%.2f → %.2f（%d 卡）' % (e0, e1, len(rows)))
    print('写出', rep)

    if args.apply:
        stamp = time.strftime('%Y%m%d_%H%M')
        backup = os.path.join(args.out, 'backup_cards_filter_' + stamp)
        os.makedirs(backup, exist_ok=True)
        n = 0
        for r in rows:
            p = os.path.join(str(common.CARDS), r['card'] + '.json')
            shutil.copy2(p, os.path.join(backup, r['card'] + '.json'))
            doc = json.load(open(p, encoding='utf-8'))
            sim = doc['medium'].setdefault('simulation', {})
            sim['print_y_filter_shift'] = r['y']
            sim['print_m_filter_shift'] = r['m']
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            n += 1
        print('写卡 %d 张（备份 %s）' % (n, backup))


if __name__ == '__main__':
    main()
