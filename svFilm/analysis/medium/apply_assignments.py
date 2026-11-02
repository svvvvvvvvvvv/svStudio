# -*- coding: utf-8 -*-
"""Write confirmed look-book stock/paper into card JSONs.

Only stock/paper and their stock-specific grain/halation/coupler fields change.
amount / PE / WB / scan endpoints are preserved.

Usage:
  python analysis/medium/apply_assignments.py --dry-run
  python analysis/medium/apply_assignments.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

RECIPE_PAIR = {
    'P160': ('kodak_portra_160', 'kodak_portra_endura'),
    'P400': ('kodak_portra_400', 'kodak_portra_endura'),
    'GOLD': ('kodak_gold_200', 'kodak_portra_endura'),
    'P800': ('kodak_portra_800', 'kodak_portra_endura'),
    'PUSH1': ('kodak_portra_800_push1', 'kodak_portra_endura'),
    'PUSH2': ('kodak_portra_800_push2', 'kodak_portra_endura'),
    'GREEN': ('kodak_portra_400', 'fujifilm_crystal_archive_typeii'),
    'XTRA': ('fujifilm_xtra_400', 'fujifilm_crystal_archive_typeii'),
    'EKTAR': ('kodak_ektar_100', 'kodak_portra_endura'),
}


def card_key(name: str) -> str:
    if name.startswith('xhs'):
        return 'xhs'
    if name.startswith('滨田'):
        return '滨田'
    return name[:3]


def grain_table():
    p = common.ENGINE / 'src' / 'spektrafilm' / 'data' / 'presets' / 'grain.toml'
    if not p.is_file():
        p = common.SVFILM / '_tools' / 'spektrafilm' / 'src' / 'spektrafilm' / 'data' / 'presets' / 'grain.toml'
    data = tomllib.load(open(p, 'rb'))
    out = {}
    for k, v in data.items():
        if k == 'defaults' or not isinstance(v, dict):
            continue
        r = v.get('rms_granularity')
        if r:
            out[k] = [float(x) for x in r]
    return out


def engine_stock_fields(stock, paper):
    from spektrafilm import init_params, digest_params
    p = digest_params(init_params(film_profile=stock, print_profile=paper))
    h = p.film_render.halation
    c = p.film_render.dir_couplers
    return {
        'halation_strength': [round(float(v) * 100.0, 2) for v in h.halation_strength],
        'halation_sigma': [float(v) for v in h.halation_first_sigma_um],
        'couplers': {
            'gamma_samelayer_rgb': [round(float(v), 4) for v in c.gamma_samelayer_rgb],
            'gamma_interlayer_r_to_gb': [round(float(v), 4) for v in c.gamma_interlayer_r_to_gb],
            'gamma_interlayer_g_to_rb': [round(float(v), 4) for v in c.gamma_interlayer_g_to_rb],
            'gamma_interlayer_b_to_rg': [round(float(v), 4) for v in c.gamma_interlayer_b_to_rg],
            'diffusion_size_um': float(c.diffusion_size_um),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--cards', default=str(common.CARDS))
    ap.add_argument('--assignments', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assignments.json'))
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.apply and args.dry_run:
        raise SystemExit('choose --apply or --dry-run')
    if not args.apply:
        args.dry_run = True

    assign = json.load(open(args.assignments, encoding='utf-8'))
    gtable = grain_table()
    fields_cache = {}
    files = sorted(Path(args.cards).glob('*.json'))
    backup = None
    if args.apply:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        backup = out_dir / ('backup_cards_before_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copytree(args.cards, backup / 'cards')
        print('backup before write:', backup)
    report = []
    for f in files:
        c = json.load(open(f, encoding='utf-8'))
        key = card_key(c['name'])
        recipe = assign.get(key)
        if recipe not in RECIPE_PAIR:
            print('skip (no recipe)', c['name'], key, recipe)
            continue
        stock, paper = RECIPE_PAIR[recipe]
        if (stock, paper) not in fields_cache:
            fields_cache[(stock, paper)] = engine_stock_fields(stock, paper)
        ef = fields_cache[(stock, paper)]
        m = c.get('medium') or {}
        sim = dict(m.get('simulation') or {})
        old = (c.get('stock'), c.get('paper'), sim.get('film_stock'), sim.get('print_paper'))

        c['stock'] = stock
        c['paper'] = paper
        sim['film_stock'] = stock
        sim['print_paper'] = paper
        m['simulation'] = sim

        if 'halation' in m:
            m['halation']['halation_strength'] = list(ef['halation_strength'])
            m['halation']['halation_first_sigma_um'] = list(ef['halation_sigma'])
        if 'grain' in m:
            m['grain']['rms_granularity'] = list(gtable.get(stock, m['grain'].get('rms_granularity', [])))
        if 'couplers' in m:
            # keep amount/inhibition/langmuir; replace stock-specific gamma/diffusion
            for k, v in ef['couplers'].items():
                m['couplers'][k] = v
        m['_lookbook'] = {
            'recipe': recipe, 'stock': stock, 'paper': paper,
            'source': 'visual_lookbook_20261103',
        }
        m['_derived'] = None
        c['medium'] = m

        report.append({'name': c['name'], 'old': old, 'new': (stock, paper),
                       'recipe': recipe, 'path': str(f)})
        if args.apply:
            tmp = str(f) + '.tmp'
            json.dump(c, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            os.replace(tmp, f)

    from collections import Counter
    print('cards', len(report), 'recipes', dict(Counter(r['recipe'] for r in report)))
    for r in report[:8]:
        print(r['name'], r['old'], '->', r['new'])
    print('mode', 'APPLY' if args.apply else 'DRY-RUN')

    json.dump(report, open(os.path.join(args.out, 'apply_report.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('wrote', os.path.join(args.out, 'apply_report.json'))


if __name__ == '__main__':
    main()
