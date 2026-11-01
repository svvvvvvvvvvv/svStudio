# -*- coding: utf-8 -*-
"""按**作者的 grain.toml** 给每张卡的介质写 `grain.rms_granularity`。

为什么要这一步
--------------
· `particle_area_um2` 是**已删除字段**（`引擎手册.md` §9.1；`model/grain.py`
  从不读它）——之前 medium 里写它等于没写。
· 颗粒粗细的唯一物理旋钮是 `rms_granularity`：ISO 6328，48 µm 孔径、
  净密度 1.0，量程 4–44；`a₁ ∝ rms²`（`grain.py:44-59` / `:171-199`）。
· 之前 64 份介质几乎全是 `(21,21,21)`（模板值），不是卷的规格；
  作者 `data/presets/grain.toml` 里每个卷都有公开 rms（Ektar 4 / Portra 800 6 /
  Vision3 500T 等）。按卷写，才是真正的"逐卡颗粒粗细"。

不测大师成片上的颗粒：1080 上 48µm 颗粒约 1–1.5 px，且交付物的功率谱陡降是
`grain.blur`（扫描/压缩 PSF），不是乳剂颗粒（`引擎手册.md` §四）；从成片反解
会把扫描仪糊进胶片里。大师只做**幅度诊断**，不做参数目标。

用法：
    & $py -X utf8 analysis/corpus/kurai/sync_grain_from_stock.py [--dry]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib

_CORPUS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_CORPUS)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

MEDIA = os.path.join(_CORPUS, 'media')
GRAIN_TOML = os.path.join(_REPO, '_tools', 'spektrafilm', 'src',
                          'spektrafilm', 'data', 'presets', 'grain.toml')
BACKUP = os.path.join(_CORPUS, 'media_grain_backup.json')


def _three(v):
    vals = [float(x) for x in (v if isinstance(v, (list, tuple)) else [v])]
    if len(vals) == 1:
        return vals * 3
    return vals[:3]


def catalog() -> dict:
    with open(GRAIN_TOML, 'rb') as fh:
        return tomllib.load(fh)


def rms_for(g: dict, stock: str, kind: str = 'negative') -> tuple:
    sec = g.get(stock)
    if isinstance(sec, dict) and sec.get('rms_granularity') is not None:
        return tuple(_three(sec['rms_granularity']))
    d = g.get('defaults') or {}
    color = (d.get('color') or {})
    cand = color.get(kind) or color.get('negative') or {}
    return tuple(_three(cand.get('rms_granularity', [7.0, 7.0, 12.0])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只打印，不写')
    a = ap.parse_args(argv)
    g = catalog()
    from design.stocks import registry
    reg = registry()
    stocks = (reg.stocks.values() if isinstance(reg.stocks, dict) else reg.stocks)
    kinds = {s.name: s.kind for s in stocks}

    files = sorted(f for f in os.listdir(MEDIA) if f.endswith('.json'))
    backup, changed = {}, 0
    for f in files:
        p = os.path.join(MEDIA, f)
        m = json.load(open(p, encoding='utf-8'))
        sim = m.get('simulation') or {}
        stock = str(sim.get('film_stock') or '')
        kind = kinds.get(stock, 'negative')
        rms = rms_for(g, stock, kind)
        gr = dict(m.get('grain') or {})
        old = gr.get('rms_granularity')
        backup[os.path.splitext(f)[0]] = old
        if list(old or []) == list(rms):
            continue
        gr['rms_granularity'] = list(rms)
        m['grain'] = gr
        changed += 1
        print('%-32s %-28s %s → %s' % (os.path.splitext(f)[0], stock, old, list(rms)))
        if not a.dry:
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(m, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
    if not a.dry:
        tmp = BACKUP + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(backup, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, BACKUP)
        print('\n改了 %d/%d 份介质；旧值备份 %s' % (changed, len(files), BACKUP))
    else:
        print('\n[dry] 会改 %d/%d 份' % (changed, len(files)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
