# -*- coding: utf-8 -*-
r"""统一入口：按预设跑 LR 收尾的层。

    python svFilm/lrfinish/run.py --profile kurai --batch <底片目录> --out-root <产物根>
    python svFilm/lrfinish/run.py --profile hamada --batch ... --out-root ... --stages layer1

预设 = `svFilm/svFilm/data/presets/<名字>/`：
  preset.json（胶片，引擎读）+ finish.json（本工具读）+ bands.json + calibration/。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import paths
import profile_io


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', '--preset', dest='profile', default='kurai',
                    help='预设 id（kurai/hamada）或 finish.json 路径')
    ap.add_argument('--batch', default='', help='底片目录（layer1 需要）')
    ap.add_argument('--out-root', default=str(paths.WORK), help='产物根')
    ap.add_argument('--stages', default='layer1,layer2,layer5')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    prof = profile_io.load(a.profile)
    print('预设 %s（%s）状态=%s' % (prof.get('name', prof.get('id')),
                                  prof.get('_preset'), prof.get('status')))
    if prof.get('status') != 'calibrated':
        print('⚠ 该预设未标定：结果只作演示，别当交付。')

    env = os.environ.copy()
    env['LR_ROOT'] = str(a.out_root)
    env['LRFINISH_PROFILE'] = str(prof.get('id'))
    stages = [s.strip() for s in a.stages.split(',') if s.strip()]
    for st in stages:
        script = paths.OPT / ('%s.py' % st)
        if not script.is_file():
            raise SystemExit('没有这一层：%s' % script)
        cmd = [a.python, '-u', str(script), '--profile', str(a.profile),
               '--out-root', str(a.out_root)]
        if st in ('layer1', 'basic'):
            if not a.batch:
                raise SystemExit('%s 需要 --batch <底片目录>' % st)
            cmd += ['--batch', str(a.batch)]
        print('▶ ' + ' '.join(cmd))
        if a.dry_run:
            continue
        subprocess.run(cmd, env=env, check=True)


if __name__ == '__main__':
    main()
