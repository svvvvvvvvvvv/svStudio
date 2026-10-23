# -*- coding: utf-8 -*-
r"""第 1 层（主曲线）—— **整批执行版**，A 方案：光比这笔账由全局曲线付。

与 lr_curve_pivot.py（标定用）的区别：那个一次只测一张、只回答"这条路行不行"；
这个**对一批图逐张出动作**，判据全部来自已实测的口径：

① 位置：脸 p50 要落进他的区间 [55, 67]
   出处：他 52 张按光型分组的脸 p50（正面硬光 66.7–67.2 / 平光 56.4 / 阴影逆光 54.7）。
   ⇒ 高于 67 压、低于 55 抬、在带内不动。**当护栏用，不当靶。**
   EV = (目标 − 脸p50) / 16   （16 L*/EV 实测；⚠ 键名必须是 Exposure2012）
② 支点：**脸 p50 换算的码值**。不是整片中调、不是 127.5。
   实测：脸支点 光比 +8.0 且脸 p50 −0.1；中调支点只 +5.8 且脸 p50 +4.9（撞硬门）。
③ 曲线强度 g：**由暗部余量定，不由"光比到靶"定**。
   压黑上限 = max(2.34%, 底片自带压黑)  ← 他的 P90 = 2.34%，且不许比底片更黑
   贴白上限 = max(2.85%, 底片自带贴白)  ← 他的 P90 = 2.85%，且不许比底片更爆
   g 越小反差越强；从 0.93 起步，超限就往 1.0 退，余量还多就往 0.84 进。
④ 停止：光比落进他的 [51, 82] 就停（P10/P90）；脸落进 [55,67] 就停。
⑤ **零动作是常态**，且要报出理由。

护栏写法为什么是 max(他P90, 底片)：底片自己已经超了，就说明这张的两端问题在上游（引擎/拍摄），
不许拿曲线去覆盖它 —— 那只会把上游的错再压一遍。

用法：
  python opt/layer1.py                       # 默认批 _b2048_1022
  python opt/layer1.py --batch <dir> --only A,B
产物：<OUT>/<stem>/{base,final}/ + layer1.json + show/index.html
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402

from _probe_clip import measure, clip_stats         # noqa: E402
from bridge_client import run                        # noqa: E402
import lr_boot                                       # noqa: E402

BATCH = str(paths.BATCH_ROOT / '_b2048_1022')
OUT = str(paths.WORK / 'layer1')
XS = list(range(0, 256, 16))
TOE, SHOULDER = 0.0, 0.0          # 主曲线趾钩/肩滚（0 = 原来的纯 S；由 profile 覆盖）
G_MIN = 0.80                      # g 的强度下限（0.80 = 旧行为；试验可放到 0.75）
FORCE_G = None                    # 仅试验：跳过搜索，强制用这个 g

# 以下默认值 = 鹿井；main() 会按 --profile 覆盖
FACE_LO, FACE_HI = 55.0, 67.0      # 脸 p50 的护栏带（硬门是上沿 67）
SPAN_LO, SPAN_HI = 51.0, 82.0      # 光比 P10/P90
SPAN_TARGET = 58.0                 # 他的 P25 —— "不算低"的那条线，补到这里就停，不追中位 63
BLACK_P90 = 2.34                   # 他 52 张全图压黑 P90
WHITE_P90 = 2.85                   # 他 52 张全图贴白 P90
L_PER_EV = 16.0


def L_to_code(L):
    Y = ((L + 16.0) / 116.0) ** 3 if L > 8.0 else L / 903.3
    v = 12.92 * Y if Y <= 0.0031308 else 1.055 * Y ** (1 / 2.4) - 0.055
    return float(np.clip(v, 0.0, 1.0) * 255.0)


def curve(P, g, toe=None, shoulder=None):
    r"""端点钉死、支点不动的 S；可加**趾钩**（提暗）与**肩滚**（压高光）。

    趾钩/肩滚都是"支点为 0、两端为 0"的鼓包：
      toe     在暗侧把中间提起来（黑点不动，暗部不再一压就死）
      shoulder 在亮侧把中间压下去（白点不动，高光过渡更柔）
    这两个是教程里"暗部上提后再在黑色处加锚点下压 / 高光稍压暗"的连续版。
    """
    toe = TOE if toe is None else float(toe)
    shoulder = SHOULDER if shoulder is None else float(shoulder)
    out = []
    for x in XS:
        if x >= P:
            u = (x - P) / (255.0 - P) if 255.0 - P > 1e-6 else 0.0
            y = P + (255.0 - P) * (u ** g)
            y -= shoulder * 4.0 * u * (1.0 - u)
        else:
            t = x / P if P > 1e-6 else 0.0
            y = P * (t ** (1.0 / g))
            y += toe * 4.0 * t * (1.0 - t)
        out.append(int(round(min(255.0, max(0.0, y)))))
    # 单调护栏：鼓包太大会在局部造成负斜率（断层），减半重来
    for _ in range(4):
        if all(b >= a for a, b in zip(out, out[1:])):
            break
        toe *= 0.5
        shoulder *= 0.5
        out = curve(P, g, toe, shoulder)
    return out


def spec_of(ys):
    flat = []
    for x, y in zip(XS, ys):
        flat += [x, y]
    body = ','.join(str(v) for v in flat)
    return ('ToneCurvePV2012:%s;ToneCurvePV2012Red:%s;'
            'ToneCurvePV2012Green:%s;ToneCurvePV2012Blue:%s' % (body, body, body, body))


def expo(outdir, edge=2048):
    r"""导两次取第二次。**每次导出都写进一个全新的空目录** ——
    这样永远不需要'先删同名文件'（EXPORTTIF 遇到同名会弹模态框、把桥卡死），
    也避开了批量删除的安全阈值。"""
    last = None
    for _ in (1, 2):
        od = os.path.join(outdir, 'e%d' % time.time_ns())
        os.makedirs(od, exist_ok=True)
        r = run('EXPORTTIF %s %d' % (od, edge), timeout=180)
        time.sleep(0.4)
        fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith('.tif')]
        last = fs[0] if fs else None
    return last, r


def snap(p, mask_path=None):
    d = measure(p, mask_path=mask_path) or {}
    if os.path.exists(p):
        d.update(clip_stats(p))
    return d


def apply_state(src, od, ev, ys, hl=0, basic=None):
    """RESET → Basic（六旋钮或 EV/高光）→ 曲线 → 导两次。
    顺序不能反：GLOBAL 是整表写入，会抹掉之前写的曲线。"""
    run('SELECT ' + src, timeout=60)
    run('RESET ' + src, timeout=60)
    if basic:
        spec = ','.join('%s=%.3f' % (k, basic[k]) for k in
                        ('Exposure2012', 'Contrast2012', 'Highlights2012',
                         'Shadows2012', 'Whites2012', 'Blacks2012'))
        run('GLOBAL %s %s' % (src, spec), timeout=60)
    else:
        if abs(ev) > 0.01:
            run('GLOBAL %s Exposure2012=%.4f' % (src, ev), timeout=60)
        if abs(hl) > 0.5:
            run('GLOBAL %s Highlights2012=%d' % (src, hl), timeout=60)
    if ys is not None:
        run('CURVES %s %s' % (src, spec_of(ys)), timeout=120)
    f, r = expo(od)
    return f, r


# 高光 −100 能把脸压多少（实测，见 §10）：曝光只补它补不了的那部分，
# 因为高光是选择性的、几乎不碰暗部，而曝光的代价全在暗部。
HL_FACE_DROP = 8.4


def position_candidates(base0):
    r"""位置的两个候选：① 只用曝光 ② 少压 0.52EV + 高光 −100。
    压的方向：高光先吃掉 8.4 L*，曝光只需补剩下的 ⇒ 暗部少沉一大截。
    抬的方向：要靠曝光抬，高光负责把亮端按住不让它翻过 95。"""
    p = base0['p50']
    c = [('不动', 0.0, 0)]      # 基准态必须永远在候选里，否则"退回不动"会退回一个不存在的地方
    if p > FACE_HI:
        tgt = FACE_HI - 0.5
        c += [('压', (tgt - p) / L_PER_EV, 0),
              ('压', (tgt + HL_FACE_DROP - p) / L_PER_EV, -100)]
    elif p < FACE_LO:
        tgt = FACE_LO + 2.0
        c += [('抬', (tgt - p) / L_PER_EV, 0),
              ('抬', (tgt - p) / L_PER_EV, -100)]
    return c


def solve_one(src, stem, base0, basic_row=None):
    """返回 (记录 dict, 底片导出路径, 定稿导出路径)"""
    rec = dict(stem=stem, src=src, before=base0)
    if not base0.get('face'):
        rec['action'] = '不动'
        rec['why'] = '量不到脸（%s）' % base0.get('why', '没检到脸')
        return rec, None, None

    p50 = base0['p50']
    rec['p50_0'] = p50
    # 两端上限取自**原底片（EV=0）**：拿做完曝光之后的两端当基准，等于让过头的曝光自己给自己发许可。
    blk_lim = max(BLACK_P90, base0.get('black', 0.0))
    wht_lim = max(WHITE_P90, base0.get('white', 0.0))
    rec['black_lim'], rec['white_lim'] = blk_lim, wht_lim

    if p50 > FACE_HI:
        direction, tgt = '压', FACE_HI - 0.5
    elif p50 < FACE_LO:
        direction, tgt = '抬', FACE_LO + 2.0
    else:
        direction, tgt = '不动', p50
    rec['dir'], rec['target'] = direction, tgt

    # ② 位置：有 Basic 结果就直接用它；否则走曝光/高光候选
    if basic_row:
        b = dict(basic_row.get('settings') or {})
        od = os.path.join(OUT, stem, 'basic')
        ft, r = apply_state(src, od, 0.0, None, 0, basic=b)
        m = snap(ft) if ft else {}
        if not ft or not m.get('face'):
            rec['action'] = '失败'; rec['why'] = 'Basic 态量不到脸'
            return rec, None, None
        f, base, ev, hl = ft, m, float(b.get('Exposure2012', 0.0)), 0
        rec['basic'] = b
        rec['tried'] = []
        rec['_idn_tif'] = ft
    else:
        tried = []
        idn = None
        for d, evc, hl in position_candidates(base0):
            od = os.path.join(OUT, stem, 'pos%+.2f_h%d' % (evc, abs(hl)))
            ft, r = apply_state(src, od, evc, None, hl)
            if not ft:
                continue
            m = snap(ft)
            if not m.get('face'):
                continue
            t = dict(dir=d, ev=evc, hl=hl, m=m, path=ft)
            tried.append(t)
            if abs(evc) < 0.01 and abs(hl) < 0.5:
                idn = t
        rec['tried'] = [{k: v for k, v in t.items() if k != 'path'} for t in tried]
        rec['_idn_tif'] = idn['path'] if idn else None   # 原样底片（EV=0、高光 0）——目检的"修前"
        if not tried:
            rec['action'] = '失败'; rec['why'] = '所有候选都量不到脸'
            return rec, None, None

        def feas(t):
            return t['m']['white'] <= wht_lim + 0.05 and t['m']['black'] <= blk_lim + 0.05

        if direction == '不动':
            pick = tried[0]
        elif direction == '压':
            # 硬门优先：脸必须先过 67。两端只记账，不否决。
            pool = [t for t in tried if t['m']['p50'] <= FACE_HI] or tried
            pick = min(pool, key=lambda t: (abs(t['m']['p50'] - tgt), t['m']['black']))
        else:
            # 抬的方向：只有"脸进了带 + 亮端没翻"才算成功；否则退回不动。
            good = [t for t in tried if feas(t) and t['m']['p50'] >= FACE_LO - 1.0]
            if good:
                pick = min(good, key=lambda t: (t['m']['white'], abs(t['m']['p50'] - tgt)))
            else:
                lift = [t for t in tried if t['dir'] == '抬']
                if lift:
                    bad = max(lift, key=lambda t: t['m']['white'])
                    rec['refused'] = ('抬 %+.2fEV%s 会把贴白从 %.2f%% 打到 %.2f%%（上限 %.2f%%），'
                                      '而脸只到 %.1f ⇒ 退回不动；亮端是平顶，归上游'
                                      % (bad['ev'],
                                         '（含高光 %d）' % bad['hl'] if abs(bad['hl']) > 0.5 else '',
                                         base0.get('white', 0.0), bad['m']['white'], wht_lim,
                                         bad['m']['p50']))
                pick = idn or tried[0]
        f, base, ev, hl = pick['path'], pick['m'], pick['ev'], pick['hl']
    if abs(ev) < 0.01 and abs(hl) < 0.5:
        direction = '不动'
    rec['base'] = base
    rec['ev'], rec['hl'], rec['dir'] = ev, hl, direction
    if abs(hl) > 0.5:
        rec['using_hl'] = '高光 %d 参与了位置（曝光只补它补不了的部分）' % hl
    if direction == '压' and base['black'] > blk_lim + 0.05:
        rec['paid'] = '压黑 %.2f%%（上限 %.2f%%，他 P90）—— 硬门优先，这笔记在暗部账上' % (
            base['black'], blk_lim)

    P = L_to_code(base['p50'])
    rec['P'] = P

    def ok(d):
        # 两端都是硬护栏：曲线/曝光不许把贴白或压黑推得比底片更糟
        return (d['black'] <= blk_lim + 0.05
                and d['white'] <= wht_lim + 0.05)

    # ④ 曲线强度：由暗部余量定，不由"光比到靶"定
    #    停在"他的 P25 = 58"（不算低的那条线），不追他的中位 63。
    def run_curve(toe, shoulder, basic=None):
        """从 g=0.93 起逐档收紧；返回 (g, final, ysf, fpath, note)。"""
        g, final, ysf, fpath, note = 1.0, base, None, f, ''
        prev = None
        cand = 0.93
        for _ in range(5):
            ys = curve(P, cand, toe, shoulder)
            od = os.path.join(OUT, stem, 'g%.2f' % cand)
            ff, rr = apply_state(src, od, ev, ys, hl, basic=basic)
            if not ff:
                note = '导出失败 %s' % rr
                break
            d = snap(ff, mask_path=f)
            d['g'] = cand
            if not ok(d):
                if prev is None:
                    note = ('曲线买不起：g=%.2f 就把端点到 %.2f/%.2f%%（上限 %.2f/%.2f%%）⇒ 零动作'
                            % (cand, d['black'], d['white'], blk_lim, wht_lim))
                else:
                    g, final, ysf, fpath = prev
                    note = 'g=%.2f（再紧一档 g=%.2f 就把端点到 %.2f/%.2f%%，超上限）' % (
                        g, cand, d['black'], d['white'])
                break
            prev = (cand, d, ys, ff)
            g, final, ysf, fpath = cand, d, ys, ff
            if d['span'] >= SPAN_TARGET:
                note = 'g=%.2f（光比 %.1f 到目标 %.1f，停）' % (g, d['span'], SPAN_TARGET)
                break
            cand = round(cand - 0.03, 2)
            if cand < G_MIN:
                note = 'g=%.2f（已到强度下限 %.2f，光比 %.1f）' % (g, G_MIN, d['span'])
                break
        return g, final, ysf, fpath, note

    fpath = f
    if base['span'] >= SPAN_TARGET:
        g, final, ysf = 1.0, base, None
        note = '光比 %.1f 已到他的 P25(%.0f)，零动作' % (base['span'], SPAN_TARGET)
    else:
        if FORCE_G is not None:
            ys = curve(P, FORCE_G, TOE, SHOULDER)
            od = os.path.join(OUT, stem, 'force_g%.2f' % FORCE_G)
            ff, rr = apply_state(src, od, ev, ys, hl,
                                 basic=(basic_row.get('settings') if basic_row else None))
            if ff:
                g, final, ysf, fpath = FORCE_G, snap(ff, mask_path=f), ys, ff
                note = '强制 g=%.2f（试验，不看护栏）' % FORCE_G
            else:
                g, final, ysf, fpath, note = 1.0, base, None, f, '强制导出失败 %s' % rr
        else:
            bset = dict(basic_row.get('settings')) if basic_row else None
            g, final, ysf, fpath, note = run_curve(0.0, 0.0, basic=bset)
            # ★ 纯 S 被暗部上限卡死时，才用趾钩/肩滚去"买"对比（教程：提暗后再压黑 / 高光稍压）
            if g == 1.0 and (TOE or SHOULDER):
                g2, f2, y2, p2, n2 = run_curve(TOE, SHOULDER, basic=bset)
                if g2 != 1.0:
                    g, final, ysf, fpath = g2, f2, y2, p2
                    note = n2 + '（纯 S 买不起，用趾钩/肩滚买到 g=%.2f）' % g2

    # ⑤ 定稿态确认（若最后一次写的就是它，就不重导）
    rec['g'] = g
    rec['curve'] = ysf
    rec['final'] = final
    rec['note'] = note
    rec['pivot'] = P
    rec['action'] = '零动作' if g == 1.0 and abs(ev) < 0.01 else '已执行'
    return rec, f, fpath


def to_jpg(tif, dst, width=1200):
    im = cv2.imdecode(np.frombuffer(open(tif, 'rb').read(), np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        return False
    if im.dtype == np.uint16:
        im = (im / 257.0).astype(np.uint8)
    if im.ndim == 3 and im.shape[2] == 3:
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    h, w = im.shape[:2]
    im = cv2.resize(im, (width, int(round(h * width / max(1, w)))), interpolation=cv2.INTER_AREA)
    return cv2.imwrite(dst, cv2.cvtColor(im, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 94])


def write_page(recs, od):
    rows = []
    for r in recs:
        s, f = r.get('_jpg_base'), r.get('_jpg_final')
        if not s:
            continue
        b, fi = r.get('base') or {}, r.get('final') or {}
        rows.append((r['stem'], s, f, b, fi, r))
    if not rows:
        return
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>第 1 层整批</title><style>',
         ':root{color-scheme:dark}*{box-sizing:border-box}',
         'body{margin:0;background:#0d0d0f;color:#e9e9ec;font:15px/1.7 -apple-system,"PingFang SC",sans-serif}',
         '.w{max-width:900px;margin:0 auto;padding:18px 12px 60px}',
         'h1{font-size:19px;margin:6px 0 4px}.s{font-size:13px;color:#8d8d96;margin-bottom:22px}',
         'figure{margin:0 0 34px}figcaption{font-size:14px;color:#b9b9c2;margin-bottom:8px}',
         'figcaption b{color:#fff}.m{font-size:13px;color:#7e7e88;margin-top:8px;font-variant-numeric:tabular-nums}',
         'img{display:block;width:100%;height:auto;border-radius:6px}',
         '.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:4px;background:#232329;color:#a9a9b4;margin-left:6px}',
         '</style></head><body><div class="w">',
         '<h1>第 1 层（主曲线）· 整批</h1>',
         '<div class="s">每张两组：上=只做了曝光，下=加了脸中支点曲线。判据：脸 p50 落进 55–67、光比补到他的 P25（58）、压黑不许超他 P90（2.34%）。</div>']
    for stem, s, f, b, fi, r in rows:
        h.append('<figure><figcaption><b>%s</b> · 只做曝光<span class="tag">EV %+.2f</span></figcaption>'
                 '<img src="%s"><div class="m">脸 %.1f · 光比 %.1f · 贴白 %.2f%% · 压黑 %.2f%% · 纹理 %.2f</div></figure>'
                 % (stem, r.get('ev', 0.0), s, b.get('p50', 0), b.get('span', 0),
                    b.get('white', 0), b.get('black', 0), b.get('tex', 0)))
        if f and f != s:
            h.append('<figure><figcaption><b>%s</b> · 加曲线<span class="tag">g=%.2f</span></figcaption>'
                     '<img src="%s"><div class="m">脸 %.1f · 光比 %.1f · 贴白 %.2f%% · 压黑 %.2f%% · 纹理 %.2f</div></figure>'
                     % (stem, r.get('g', 1.0), f, fi.get('p50', 0), fi.get('span', 0),
                        fi.get('white', 0), fi.get('black', 0), fi.get('tex', 0)))
    h.append('</div></body></html>')
    open(os.path.join(od, 'index.html'), 'w', encoding='utf-8').write('\n'.join(h))


def main():
    global FACE_LO, FACE_HI, SPAN_LO, SPAN_HI, SPAN_TARGET, BLACK_P90, WHITE_P90, L_PER_EV, OUT, TOE, SHOULDER, G_MIN, FORCE_G
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', default=BATCH)
    ap.add_argument('--only', default='')
    ap.add_argument('--profile', default='kurai', help='预设 id 或 profile.json 路径')
    ap.add_argument('--out-root', default='', help='产物根（默认 paths.WORK）')
    ap.add_argument('--toe', type=float, default=None, help='趾钩强度（提暗，0=关）')
    ap.add_argument('--shoulder', type=float, default=None, help='肩滚强度（压高光，0=关）')
    ap.add_argument('--span-target', type=float, default=None, help='光比目标（默认 profile；试验用）')
    ap.add_argument('--g-min', type=float, default=None, help='g 下限（默认 0.80；试验用）')
    ap.add_argument('--force-g', type=float, default=None, help='跳过搜索，强制用这个 g（只做目检实验）')
    ap.add_argument('--no-basic', action='store_true', help='不读 basic/basic.json，退回旧候选逻辑')
    a = ap.parse_args()

    prof = profile_io.load(a.profile)
    if prof.get('status') != 'calibrated':
        print('⚠ 预设 %s 状态=%s：tone 参数尚未标定，结果只作演示'
              % (prof.get('id'), prof.get('status')))
    t = profile_io.tone(prof)
    FACE_LO, FACE_HI = float(t['face_lo']), float(t['face_hi'])
    SPAN_LO, SPAN_HI = float(t['span_lo']), float(t['span_hi'])
    SPAN_TARGET = float(t['span_target'])
    BLACK_P90, WHITE_P90 = float(t['black_p90']), float(t['white_p90'])
    L_PER_EV = float(t['l_per_ev'])
    TOE = float(t.get('toe', 0.0))
    SHOULDER = float(t.get('shoulder', 0.0))
    G_MIN = float(t.get('g_min', 0.80))
    if a.toe is not None:
        TOE = a.toe
    if a.shoulder is not None:
        SHOULDER = a.shoulder
    if a.span_target is not None:
        SPAN_TARGET = a.span_target
    if a.g_min is not None:
        G_MIN = a.g_min
    FORCE_G = a.force_g
    if a.out_root:
        OUT = os.path.join(a.out_root, 'layer1')
    os.makedirs(OUT, exist_ok=True)
    print('预设 %s (%s)  产物 %s' % (prof.get('name', prof.get('id')), prof.get('id'), OUT))

    basic_rows = {}
    if not a.no_basic:
        bp = os.path.join(os.path.dirname(OUT), 'basic', 'basic.json')
        if os.path.isfile(bp):
            try:
                basic_rows = {r['stem']: r for r in
                              json.load(open(bp, encoding='utf-8'))['rows']}
                print('Basic 基线：%s（%d 张）' % (bp, len(basic_rows)))
            except Exception as e:                    # noqa: BLE001
                print('⚠ Basic 基线读失败：%s' % e)
        else:
            print('Basic 基线不存在，按旧候选逻辑跑：%s' % bp)

    srcs = sorted(p for p in glob.glob(os.path.join(a.batch, '*_svFilm_full.tif'))
                  if '_c0' not in p and '_c1' not in p)
    if a.only:
        want = set(x.strip() for x in a.only.split(','))
        srcs = [p for p in srcs if os.path.basename(p).split('_')[0] in want]
    if not srcs:
        print('没有底片：%s' % a.batch); return

    if lr_boot.lr_pids():
        lr_boot.kill_lr(); time.sleep(3)
    print('日志清理: %s ｜ 锁: %s' % (lr_boot.clear_console_log(), lr_boot.clear_stale_lock()))
    lr_boot.launch()
    ok, msg = lr_boot.wait_bridge(150)
    print('桥：%s %s\n' % ('就绪' if ok else '未就绪', msg))
    if not ok:
        return

    print('%-10s %2s %7s %11s %11s %8s %7s %7s  %s'
          % ('张号', '向', 'EV', '脸前→后', '光比前→后', 'g', '压黑', '贴白', '处置'))
    recs = []
    for src in srcs:
        stem = os.path.basename(src).replace('_svFilm_full.tif', '')
        run('SELECT ' + src, timeout=60)
        run('RESET ' + src, timeout=60)
        b0 = snap(src)
        rec, fb, ff = solve_one(src, stem, b0, basic_rows.get(stem))
        rec['_base_tif'], rec['_final_tif'] = rec.get('_idn_tif') or fb, ff
        recs.append(rec)
        if not fb:
            print('%-10s %2s %7s %11s %11s %8s %7s %7s  %s'
                  % (stem, '-', '-', '-', '-', '-', '-', '-', rec.get('why', '')))
            continue
        bb, fi = rec.get('base') or {}, rec.get('final') or {}
        msg = ' ｜ '.join(x for x in (rec.get('note'), rec.get('using_hl'),
                                     rec.get('refused'), rec.get('paid')) if x)
        evtag = '%+.2f%s' % (rec.get('ev', 0.0),
                             ('/H%d' % rec['hl']) if abs(rec.get('hl', 0)) > 0.5 else '')
        print('%-10s %s %7s %5.1f→%5.1f %5.1f→%5.1f %8.2f %7.2f %7.2f  %s'
              % (stem, rec.get('dir', '')[:1], evtag, bb.get('p50', 0),
                 fi.get('p50', 0), bb.get('span', 0), fi.get('span', 0), rec.get('g', 1.0),
                 fi.get('black', 0), fi.get('white', 0), msg))
        print('%-10s   底片 EV=0：脸 %.1f 光比 %.1f 贴白 %.2f%% 压黑 %.2f%%'
              % ('', b0.get('p50', 0), b0.get('span', 0), b0.get('white', 0), b0.get('black', 0)))

    # 目检页
    so = os.path.join(OUT, 'show')
    os.makedirs(so, exist_ok=True)
    for r in recs:
        stem = r['stem']
        for key, dst in (('_base_tif', '_jpg_base'), ('_final_tif', '_jpg_final')):
            t = r.get(key)
            if not t or not os.path.exists(t):
                r[dst] = None
                continue
            name = stem + ('_base.jpg' if key == '_base_tif' else '_final.jpg')
            r[dst] = name if to_jpg(t, os.path.join(so, name)) else None
        if r.get('_final_tif') == r.get('_base_tif'):
            r['_jpg_final'] = None   # 没加曲线就不重复贴同一张
    write_page(recs, so)

    json.dump(dict(profile=prof.get('id'), profile_version=prof.get('version'),
                   guard=dict(face=[FACE_LO, FACE_HI], span=[SPAN_LO, SPAN_HI],
                              black_p90=BLACK_P90, white_p90=WHITE_P90,
                              l_per_ev=L_PER_EV),
                   rows=recs),
              open(os.path.join(OUT, 'layer1.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'layer1.json'))
    print('-> %s' % os.path.join(so, 'index.html'))


if __name__ == '__main__':
    main()
