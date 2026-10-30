# -*- coding: utf-8 -*-
r"""_lib / jsonsafe.py —— JSON 安全改写（**踩过好几次坑，最该固化**）。

来源 = `系统重标_1013\s12_patch_note.py`（写对的那一版）+ `s9_final.py` / `s13_final2.py`（踩坑现场）
       + `AGENTS` §二.5 的工程纪律。

## 坑（为什么不能 `json.loads` + `json.dumps`）
```
⚠ `json.loads` + `json.dumps` 重排会**顺手改掉整份文件**（实测 415 行假 diff）。
★ 正确做法：**读原文 -> 探测排版 -> 只做纯文本插入/替换 -> `json.loads` 硬门 -> 原子写**。
★ 实测排版（**10-15 复核；别写死，一律用 `probe()`**）：
    · **工作副本一律 CRLF**（本机 `core.autocrlf=true`）；**仓库 blob 是 LF** ⇒ `git diff` 只算内容差
    · `targets.json`                 = indent=2 ｜ **无**尾换行
    · 预设（现在只有 `鹿井预设.json`） = indent=2 ｜ **有**尾换行
    · 全部**无 BOM**
    ⚠ 旧记载写的"targets.json = LF ｜ 10 条预设里 9 条 indent=1"**两处都错**（那是在旧工作副本上测的）
```

## 主函数（签名）
```
read_raw(path)                        -> str       newline='' 读（不翻译换行）
probe(path)                           -> dict      ★ 探测排版：indent / newline / bom / 尾换行 / sha256
gate(text)                            -> dict      ★ `json.loads` 硬门（不过就 raise）
bare_lf_check(text, crlf=None)        -> list      裸 LF 检查（CRLF 文件里混进裸 LF => 列行号）
snapshot(path, snapdir)               -> str      ★ 快照（**必须在写之前拍**）
write_atomic(path, text, newline='')  -> str      原子写（tmp + os.replace，不翻译换行）
insert_key(path, key, value, ...)     -> dict     纯文本插入一个顶层键（默认插在 `{` 之后）
replace_text(path, old, new, ...)     -> dict     纯文本替换（`old` 必须唯一命中）
patch(path, inserts=(), replaces=(), apply=False, snapdir=None) -> dict   ★ 组合补丁
roundtrip(path)                       -> dict     读->探测->不改的往返（逐字节相同才算过）
```

## 纪律（写进代码里，不靠记性）
```
★ 快照在**写前**拍 ｜ 补丁先过 `json.loads` 硬门 ｜ 原子写 ｜ 裸 LF 检查
★ 默认 **dry-run**：`apply=False` 只报告，不落盘
★ 改含中文的 JSON 用 Python `io.open`，别用 PowerShell 文本 cmdlet（GBK 往返会破坏 UTF-8）
```
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sys

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402


# ---------------------------------------------------------------- 读 / 探测

def read_raw(path):
    r"""`newline=''` 读原文（**不翻译换行**）。@returns {str}"""
    return io.open(path, 'r', encoding='utf-8', newline='').read()


def file_sha256(path):
    r"""文件全文 sha256（**唯一实现**；二进制安全，分块读）。@returns {str}"""
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def probe(path):
    r"""★ 探测排版。

    @returns {dict} {path, bytes, indent, newline, crlf, bom, trailing_nl, lines, sha256}
      `indent` = 第一个带引号的键那一行的缩进宽度（探测不到 => None）
    """
    b = open(path, 'rb').read()
    s = io.open(path, 'r', encoding='utf-8', newline='').read()
    # ★ 换行判定必须【按数量】而不是"含有"：一份以 LF 为主、只混进 1 个 CRLF 的文件
    #   （Windows 侧工具追加一行就会造成）用 `'\r\n' in s` 会误判成 CRLF ⇒ `split` 只切出 2 行
    #   ⇒ indent / lines 全错，下游"按行定位"的编辑会写坏文件。
    n_crlf = s.count('\r\n')
    crlf = n_crlf > (s.count('\n') - n_crlf)          # 少数服从多数
    nl = '\r\n' if crlf else '\n'
    lines = s.split(nl)
    ind = None
    for L in lines[:80]:
        m = re.match(r'^(\s+)"', L)
        if m:
            ind = len(m.group(1))
            break
    return dict(path=path, bytes=len(b), indent=ind, newline=nl, crlf=bool(crlf),
                bom=(b[:3] == b'\xef\xbb\xbf'), trailing_nl=s.endswith('\n'),
                lines=len(lines), sha256=file_sha256(path), text=s)


def gate(text):
    r"""★ `json.loads` 硬门（不通过就抛）。@returns {dict|list} 解析结果"""
    return json.loads(text)


def bare_lf_check(text, crlf=None):
    r"""裸 LF 检查：CRLF 文件里混进**裸 LF**（前面不是 `\r`）=> 列出 1-based 行号。

    @returns {list[int]} 行号（空 = 干净）
    """
    if crlf is None:
        _n = text.count('\r\n')
        crlf = _n > (text.count('\n') - _n)           # 同 probe：按数量判定，别用"含有"
    if not crlf:
        return []
    bad = []
    line = 1
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\n':
            if i == 0 or text[i - 1] != '\r':
                bad.append(line)
            line += 1
        i += 1
    return bad


# ---------------------------------------------------------------- 写

def snapshot(path, snapdir=None):
    r"""★ 快照（**必须在写之前拍**）。@returns {str} 快照路径"""
    snapdir = snapdir or os.path.join(os.path.dirname(os.path.abspath(path)), '_snap')
    os.makedirs(snapdir, exist_ok=True)
    dst = os.path.join(snapdir, os.path.basename(path) + '.before')
    shutil.copy2(path, dst)
    return dst


def write_atomic(path, text, newline=''):
    r"""原子写（`<path>.tmp` + `os.replace`；`newline=''` = 不做换行翻译）。@returns {str}"""
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline=newline) as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def write_json_atomic(obj, path, indent=1, newline='\n', **dump_kw):
    r"""★ **原子写 JSON 的唯一实现**（原来 `render` / `measure` / `jsonsafe` 各一份）。

    ★ 语义与合并前逐字节相同：`ensure_ascii=False` · `indent` · `default=float` ·
      父目录不存在就建 · `newline='\n'`（不做翻译，与两份旧实现一致）。
    @returns {str} 落盘路径
    """
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    kw = dict(ensure_ascii=False, indent=indent, default=float)
    kw.update(dump_kw)
    return write_atomic(path, json.dumps(obj, **kw), newline=newline)


# ---------------------------------------------------------------- 补丁

def _key_present(text, key, crlf):
    nl = '\r\n' if crlf else '\n'
    for L in text.split(nl):
        if re.match(r'^\s*"%s"\s*:' % re.escape(key), L):
            return True
    return False


def insert_key(path, key, value, apply=False, snapdir=None, position='head',
               after_brace=True, indent=None):
    r"""**纯文本插入**一个顶层键（不重排 JSON）。

    @param key {str} 新键名
    @param value {object} 值（用 `json.dumps` 渲染成**单行**文本，不换行）
    @param apply {bool} False = dry-run（只报告）
    @param position {str} `head` = 插在第一个顶层键之前；`end` = 插在最后一个键之后
    @returns {dict} {status, indent, crlf, delta_bytes, snapshot, sha_before, sha_after, bare_lf}
    """
    pr = probe(path)
    raw = pr['text']
    crlf, nl = pr['crlf'], pr['newline']
    if _key_present(raw, key, crlf):
        return dict(status='skip（键已存在）', key=key, sha_before=pr['sha256'])
    ind = pr['indent'] if indent is None else indent
    ind = 2 if ind is None else ind
    block = '%s"%s": %s,' % (' ' * ind, key, json.dumps(value, ensure_ascii=False))
    lines = raw.split(nl)
    out, done = [], False
    if position == 'head' and after_brace:
        for L in lines:
            if not done and L.strip() == '{':
                out.append(L)
                out.append(block)
                done = True
                continue
            out.append(L)
    if not done:
        # end（或找不到开头 `{`）：插在**最后一个** `}` 行之前
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == '}':
                lines.insert(i, block)
                done = True
                break
        out = lines
    if not done:
        return dict(status='FAIL（找不到 JSON 对象的边界）', key=key, sha_before=pr['sha256'])
    new = nl.join(out)
    gate(new)                                                     # ★ 硬门
    bad = bare_lf_check(new, crlf)
    res = dict(status='dry-ok', key=key, indent=ind, crlf=crlf,
               delta_bytes=len(new) - len(raw), sha_before=pr['sha256'],
               sha_after=hashlib.sha256(new.encode('utf-8')).hexdigest(),
               bare_lf=bad, json_ok=True)
    if bad:
        res['status'] = 'FAIL（裸 LF %d 处）' % len(bad)
        return res
    if not apply:
        res['new_text'] = new
        return res
    res['snapshot'] = snapshot(path, snapdir)
    _rewrite(path, new, res)
    return res


def replace_text(path, old, new, apply=False, snapdir=None, must_unique=True):
    r"""**纯文本替换**（不重排 JSON）。

    @param old {str} 必须**唯一**命中（除非 `must_unique=False`）
    @returns {dict} {status, hits, delta_bytes, snapshot, sha_before, sha_after, bare_lf}
    """
    pr = probe(path)
    raw = pr['text']
    crlf = pr['crlf']
    hits = raw.count(old)
    if hits == 0:
        return dict(status='skip（未命中）', hits=0, sha_before=pr['sha256'])
    if must_unique and hits > 1:
        return dict(status='FAIL（命中 %d 次，要求唯一）' % hits, hits=hits,
                    sha_before=pr['sha256'])
    new_text = raw.replace(old, new)
    gate(new_text)                                                # ★ 硬门
    bad = bare_lf_check(new_text, crlf)
    res = dict(status='dry-ok', hits=hits, delta_bytes=len(new_text) - len(raw),
               sha_before=pr['sha256'],
               sha_after=hashlib.sha256(new_text.encode('utf-8')).hexdigest(),
               bare_lf=bad, json_ok=True)
    if bad:
        res['status'] = 'FAIL（裸 LF %d 处）' % len(bad)
        return res
    if not apply:
        res['new_text'] = new_text
        return res
    res['snapshot'] = snapshot(path, snapdir)
    _rewrite(path, new_text, res)
    return res


def _rewrite(path, text, res):
    r"""落盘 + 复验（写后必须再过一次硬门 + 逐字节核对）。"""
    write_atomic(path, text)
    back = read_raw(path)
    gate(back)
    res['status'] = 'applied'
    res['roundtrip_identical'] = (back == text)
    res['sha_after'] = hashlib.sha256(back.encode('utf-8')).hexdigest()
    return res


def patch(path, inserts=(), replaces=(), apply=False, snapdir=None):
    r"""组合补丁：先在**内存里**把所有插入/替换做完，过门，最后**只写一次**。

    @param inserts {list} [(key, value)] 或 [{'key':…, 'value':…}]
    @param replaces {list} [(old, new)]
    @returns {dict} {status, steps, delta_bytes, sha_before, sha_after, snapshot}
    """
    pr = probe(path)
    raw = pr['text']
    nl, crlf = pr['newline'], pr['crlf']
    ind = 2 if pr['indent'] is None else pr['indent']
    cur = raw
    steps = []
    for it in inserts:
        k, v = (it['key'], it['value']) if isinstance(it, dict) else it
        if _key_present(cur, k, crlf):
            steps.append(dict(kind='insert', key=k, status='skip'))
            continue
        block = '%s"%s": %s,' % (' ' * ind, k, json.dumps(v, ensure_ascii=False))
        lines = cur.split(nl)
        for i, L in enumerate(lines):
            if L.strip() == '{':
                lines.insert(i + 1, block)
                break
        cur = nl.join(lines)
        steps.append(dict(kind='insert', key=k, status='ok'))
    for old, new in replaces:
        n = cur.count(old)
        if n != 1:
            steps.append(dict(kind='replace', status='FAIL（命中 %d 次）' % n))
            return dict(status='FAIL', steps=steps, sha_before=pr['sha256'])
        cur = cur.replace(old, new)
        steps.append(dict(kind='replace', status='ok'))
    gate(cur)                                                     # ★ 硬门
    bad = bare_lf_check(cur, crlf)
    res = dict(status='dry-ok', steps=steps, delta_bytes=len(cur) - len(raw),
               sha_before=pr['sha256'],
               sha_after=hashlib.sha256(cur.encode('utf-8')).hexdigest(), bare_lf=bad)
    if bad:
        res['status'] = 'FAIL（裸 LF %d 处）' % len(bad)
        return res
    if not apply:
        res['new_text'] = cur
        return res
    res['snapshot'] = snapshot(path, snapdir)
    _rewrite(path, cur, res)
    return res


def roundtrip(path):
    r"""★ 读 -> 探测 -> **不改**的往返：必须**逐字节相同**。

    @returns {dict} {path, identical, sha256, indent, newline, bytes, json_ok}
    """
    pr = probe(path)
    raw = pr['text']
    nl = pr['newline']
    s = raw[:-len(nl)] if raw.endswith(nl) else raw      # 尾换行单独记（nl 可能是 2 字符）
    lines = s.split(nl)
    back = nl.join(lines) + (nl if raw.endswith(nl) else '')
    try:
        gate(back)
        ok = True
    except Exception:                                                    # noqa: BLE001
        ok = False
    return dict(path=path, identical=bool(back == raw), json_ok=ok, bytes=pr['bytes'],
                indent=pr['indent'], newline=('CRLF' if pr['crlf'] else 'LF'),
                bom=pr['bom'], trailing_nl=pr['trailing_nl'],
                sha256=hashlib.sha256(back.encode('utf-8')).hexdigest(),
                sha256_file=pr['sha256'])


def main(argv=None):
    r"""命令行：`python -m _lib.jsonsafe probe <文件...>` / `roundtrip <文件...>`。@returns {int}"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('probe', 'roundtrip'))
    ap.add_argument('files', nargs='+')
    a = ap.parse_args(argv)
    bad = 0
    for p in a.files:
        if a.cmd == 'probe':
            d = probe(p)
            print('  %-46s ind=%-4s %s  bom=%-5s 尾换行=%-5s %8d B  %s'
                  % (os.path.basename(p), d['indent'], 'CRLF' if d['crlf'] else 'LF',
                     d['bom'], d['trailing_nl'], d['bytes'], d['sha256'][:16]))
        else:
            d = roundtrip(p)
            bad += (not d['identical'])
            print('  %-46s identical=%-5s json_ok=%-5s ind=%s %s'
                  % (os.path.basename(p), d['identical'], d['json_ok'], d['indent'], d['newline']))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
