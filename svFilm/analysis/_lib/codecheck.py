# -*- coding: utf-8 -*-
r"""**代码卫生三件套** —— 以后要"证明只动了注释"或"量注释体量"，一律用这里，**别再写新脚本**。

## 为什么有这个模块（10-15 的教训）
一轮"注释清理"里，四个子智能体各写了一份"只改注释"的验证脚本（`verify_comments_only.py` /
`check_comments_only.py` / `measure_comments.py` / `audit_comments.py`）—— **同一件事写了四遍**。
用户原话：「要复用一些写过的代码和方法，不要每次都写一堆临时代码。」
⇒ 收敛成这一个模块（逻辑与那四份**逐字一致**，只是参数化 + 合并）。

## 三个能力
| 函数 | 判什么 |
|---|---|
| `ast_equal(path, rev='HEAD')` | 除**文档字符串**外，AST **逐节点**相同（粗、快） |
| `token_equal(path, rev='HEAD')` | 除注释/文档外，**逐 token** 相同 **且**代码行骨架逐行相同（严） |
| `comment_stats(path)` | 注释/文档的**行数 + 字数 + 带日期处数 + 开发机绝对路径处数** |

## CLI
```
python -m _lib.codecheck check [文件...]   # 默认：自动找 HEAD 与工作树之间有改动的 .py
python -m _lib.codecheck stat  [文件...]   # 注释体量表
python -m _lib.codecheck eol   [文件...]   # 把裸 LF 归一成 CRLF（工作副本实况）
```
⚠ `token_equal` 的 `code_lines()` 用"行首到第一个 `#`"当骨架 ⇒ **只对"字符串字面量里不含 `#`"的文件安全**
（本项目所有 `.py` 已核过；新加文件若含 `#` 字面量，用 `ast_equal` 即可）。
"""
from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
import tokenize

REPO = r'E:\svStudio'          # git 根（`rev` 从它取）—— ★ 与 `_lib.REPO` 同源，别各写一份
DATE = re.compile(r'\b(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b')
ABSPATH = re.compile(r'[A-Za-z]:\\\\|[A-Za-z]:\\')
try:                            # 从 `_lib.REPO`（= <git 根>\svFilm）反推 git 根，避免两处写死
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import _lib as _L
    REPO = os.path.dirname(os.path.abspath(_L.REPO))
except Exception:                                                          # noqa: BLE001
    pass


# ---------------------------------------------------------------- ① AST
def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, 'body', None)
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return tree


def _canon(src):
    return ast.dump(_strip_docstrings(ast.parse(src)), annotate_fields=True)


def rev_text(path, rev='HEAD'):
    r"""取 `rev` 里那份文件（默认 `HEAD`）。@returns {str|None}（不在 rev 里 ⇒ None）"""
    rel = os.path.relpath(os.path.abspath(path), REPO).replace('\\', '/')
    r = subprocess.run(['git', '-C', REPO, 'show', '%s:%s' % (rev, rel)], capture_output=True)
    return r.stdout.decode('utf-8') if r.returncode == 0 else None


def ast_equal(path, rev='HEAD'):
    r"""除文档字符串外 AST 逐节点相同。@returns {(ok, detail)}"""
    cur = io.open(path, encoding='utf-8', newline='').read()
    old = rev_text(path, rev)
    if old is None:
        return True, '新增文件（%s 里没有）' % rev
    try:
        same = _canon(old) == _canon(cur)
    except SyntaxError as e:
        return False, '语法错：%s' % e
    if same:
        return True, '只动了注释/文档字符串'
    return False, _first_diff(old, cur)


def _first_diff(a, b):
    def code_lines(s):
        return [(i, ln.split('#')[0].rstrip())
                for i, ln in enumerate(s.split('\n'), 1) if ln.split('#')[0].strip()]
    ca, cb = code_lines(a), code_lines(b)
    for i in range(max(len(ca), len(cb))):
        x = ca[i][1] if i < len(ca) else '<无>'
        y = cb[i][1] if i < len(cb) else '<无>'
        if x != y:
            return '第 %d 处代码行不同：\n     %s: %s\n     现在: %s' % (i + 1, rev, x[:120], y[:120])
    return '（代码行看起来一样 ⇒ 差异在文档字符串，属允许范围）'


# ---------------------------------------------------------------- ② token / 代码行
def _char_col(lines, lineno, byte_col):
    r"""AST 的 `col_offset` 是 **UTF-8 字节**列，`tokenize` 是**字符**列 ⇒ 必须换算。"""
    return len(lines[lineno - 1].encode('utf-8')[:byte_col].decode('utf-8', 'replace'))


def _docstring_spans(src):
    lines = src.replace('\r\n', '\n').split('\n')
    out = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, 'body', None)
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            v = body[0].value
            out.add((v.lineno, _char_col(lines, v.lineno, v.col_offset),
                     v.end_lineno, _char_col(lines, v.end_lineno, v.end_col_offset)))
    return out


def tokens(src):
    r"""除 COMMENT 与"文档字符串 STRING"外的 token 流（类型 + 字面量）。"""
    out, dspans = [], _docstring_spans(src)
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT:
            continue
        if t.type == tokenize.STRING and (t.start[0], t.start[1], t.end[0], t.end[1]) in dspans:
            continue
        if t.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                      tokenize.ENDMARKER):
            continue
        out.append((t.type, t.string))
    return out


def code_lines(src):
    r"""代码行骨架（行首到第一个 `#` 之前，rstrip）；注释行与文档字符串覆盖的行跳过。"""
    ds, out = set(), []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant) \
                and isinstance(node.body[0].value.value, str):
            n = node.body[0].value
            ds.update(range(n.lineno, n.end_lineno + 1))
    for i, ln in enumerate(src.replace('\r\n', '\n').split('\n'), 1):
        t = ln.strip()
        if not t or t.startswith('#') or i in ds:
            continue
        out.append(ln.split('#')[0].rstrip())
    return out


def token_equal(path, rev='HEAD'):
    r"""除注释/文档外逐 token 相同 **且** 代码行骨架逐行相同。@returns {(ok, detail)}"""
    cur = io.open(path, encoding='utf-8', newline='').read()
    old = rev_text(path, rev)
    if old is None:
        return True, '新增文件'
    if tokens(old) != tokens(cur):
        a, b = tokens(old), tokens(cur)
        n = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        pos = n[0] if n else min(len(a), len(b))
        ctx = ['    %s[%d] %r | 现在 %r' % (rev, j, a[j] if j < len(a) else None,
                                            b[j] if j < len(b) else None)
               for j in range(max(0, pos - 2), pos + 3)]
        return False, 'token 不同（第 %s 处）\n%s' % (n[:3] or '长度不同', '\n'.join(ctx))
    if code_lines(old) != code_lines(cur):
        a, b = code_lines(old), code_lines(cur)
        d = [i for i in range(max(len(a), len(b)))
             if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)]
        return False, '代码行不同（第 %s 处）' % d[:3]
    return True, '除注释/文档外逐 token 相同'


# ---------------------------------------------------------------- ③ 注释体量
def comment_stats(path):
    r"""注释/文档体量。@returns {dict} lines/code_lines/comment_lines/chars/doc_chars/dated/abspaths/eol/bom/trailing_nl"""
    raw = open(path, 'rb').read()
    src = io.open(path, encoding='utf-8', newline='').read()
    lines = src.split('\n')
    n_code = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith('#'))
    n_com = sum(1 for ln in lines if ln.strip().startswith('#'))
    com_chars = sum(len(ln.strip()) for ln in lines if ln.strip().startswith('#'))
    tree = ast.parse(src)
    doc_chars = 0
    for node in ast.walk(tree):
        # ⚠ `ast.get_docstring` 对**不能带文档字符串**的节点（如 `Expr`）会抛 TypeError ⇒ 必须先筛类型
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        d = ast.get_docstring(node, clean=False)
        if d:
            doc_chars += len(d)
    return dict(path=path, lines=len(lines), code_lines=n_code, comment_lines=n_com,
                chars=com_chars + doc_chars, doc_chars=doc_chars,
                dated=len(DATE.findall(src)), abspaths=len(ABSPATH.findall(src)),
                eol=('CRLF' if b'\r\n' in raw else 'LF'), bom=raw[:3] == b'\xef\xbb\xbf',
                trailing_nl=raw.endswith(b'\n'))


# ---------------------------------------------------------------- ④ EOL（与 jsonsafe 同口径）
def normalize_eol(path, crlf=True):
    r"""把裸 LF 归一成 CRLF（工作副本实况；`core.autocrlf=true`）。@returns {int} 改了几处"""
    s = io.open(path, encoding='utf-8', newline='').read()
    n_lf = s.count('\n') - s.count('\r\n')
    if not n_lf:
        return 0
    s2 = s.replace('\r\n', '\n')
    s2 = s2.replace('\n', '\r\n') if crlf else s2
    io.open(path, 'w', encoding='utf-8', newline='').write(s2)
    return n_lf


# ---------------------------------------------------------------- CLI
def changed_py(root=r'E:\svStudio\svFilm\svFilm'):
    r = subprocess.run(['git', '-C', REPO, 'diff', '--name-only', 'HEAD', '--',
                        os.path.relpath(root, REPO).replace('\\', '/')], capture_output=True)
    return [os.path.join(REPO, p.replace('/', os.sep))
            for p in r.stdout.decode('utf-8').split('\n') if p.strip().endswith('.py')]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv and argv[0] in ('check', 'stat', 'eol') else 'check'
    files = argv[1:] if cmd == argv[0] else argv
    files = files or changed_py()
    if not files:
        print('没有改动过的 .py（拿 HEAD 当基线）')
        return 0
    if cmd == 'eol':
        for p in files:
            n = normalize_eol(p)
            print('  %-16s 裸 LF %d 处 → CRLF' % (os.path.basename(p), n))
        return 0
    if cmd == 'stat':
        print('%-18s %6s %6s %6s %9s %9s %6s %8s' %
              ('文件', '总行', '代码行', '注释行', '注释+文档字', '其中文档字', '带日期', '绝对路径'))
        tot = 0
        for p in files:
            d = comment_stats(p)
            tot += d['chars']
            print('%-18s %6d %6d %6d %9d %9d %6d %8d' %
                  (os.path.basename(p), d['lines'], d['code_lines'], d['comment_lines'],
                   d['chars'], d['doc_chars'], d['dated'], d['abspaths']))
        print('合计注释+文档 %d 字' % tot)
        return 0
    bad = 0
    for p in files:
        ok1, d1 = ast_equal(p)
        ok2, d2 = token_equal(p)
        d = comment_stats(p)
        ok = ok1 and ok2
        bad += 0 if ok else 1
        print('  %-16s %s ｜ AST:%s token:%s ｜ %s ｜ %d 字' %
              (os.path.basename(p), 'OK' if ok else '**红了**',
               'ok' if ok1 else 'X', 'ok' if ok2 else 'X', d['eol'], d['chars']))
        if not ok:
            print('     %s' % (d1 if not ok1 else d2))
    print('\n%d 个文件，红 %d 个' % (len(files), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
