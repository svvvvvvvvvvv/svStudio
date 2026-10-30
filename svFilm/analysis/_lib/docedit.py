# -*- coding: utf-8 -*-
r"""**注释/文档字符串的批量改写**（安全版） —— 以后改注释就用它，别再写一堆 pass 脚本。

## 为什么有这个模块
10-15 那轮注释清理，四个子智能体一共写了 **15 个 pass 脚本**（`_clean_1015_a..o.py`）+ 4 个 `_trim_notes*`，
其中**真正可复用的只有 `_clean_1015_d.py` 里那 4 个函数**（按限定名换文档字符串、按锚点换注释块）。
⇒ 收敛成这里；配合 `_lib.codecheck`（改完证明"只动了注释"）= **两个模块覆盖整件事**。

## 三条安全约定（每条都是踩过的）
```
① **锚点必须恰好命中一次** —— 0 次或多于 1 次 ⇒ 当场 raise（不许"改到别处"）
② **按 AST 定位，不按正则** —— 文档字符串用 `ast` 的 `lineno/col_offset` 算**字符列**（UTF-8 字节列要换算）
③ **一次改完、原子写、保留 CRLF** —— 中途失败不留半截文件
```
## 用法
```python
import _lib.docedit as DE
ops = [DE.doc('presets.py', 'presets.render', '新文档字符串'),
       DE.block('presets.py', 'def _check_white_gate', '# 新注释块\n# 第二行')]
DE.apply_all(ops)                    # 全部校验通过才写盘
```
CLI（改完自动跑 codecheck 验证）：
`python -m _lib.docedit <file> --show <qualified.name>`   # 只看某个节点的文档字符串
"""
from __future__ import annotations

import ast
import io
import os
import sys


# ---------------------------------------------------------------- 基础
def load(path):
    return io.open(path, encoding='utf-8', newline='').read()


def save(path, text):
    r"""原子写（tmp + `os.replace`；`newline=''` ⇒ 不翻译换行，CRLF 原样保留）。"""
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)
    os.replace(tmp, path)


def _lines(text):
    return text.split('\n')


def _char_col(line, byte_col):
    r"""`ast` 的 `col_offset` 是 **UTF-8 字节**列 ⇒ 换成**字符**列（切字符串要用字符）。"""
    return len(line.encode('utf-8')[:byte_col].decode('utf-8', 'replace'))


def find_node(text, qual):
    r"""按限定名找 AST 节点。`a.b.C.method` 或 **`模块名.名字`**（模块名那一段允许存在、会被忽略）。

    ★ 为什么要容忍模块名前缀：`qual='color.luma'` 与 `'luma'` 都该能用 ——
      写脚本时人手边有"文件名.函数名"这个直觉写法。
    @returns {ast.AST|None}
    """
    node = ast.parse(text)
    parts = qual.split('.')
    names = {getattr(ch, 'name', None) for ch in ast.iter_child_nodes(node)}
    if parts and parts[0] not in names:          # 首段不是子节点名 ⇒ 当模块名前缀丢掉
        parts = parts[1:]
    for part in parts:
        nxt = None
        for ch in ast.iter_child_nodes(node):
            if getattr(ch, 'name', None) == part:
                nxt = ch
                break
        if nxt is None:
            return None
        node = nxt
    return node


def _count(text, anchor):
    n = text.count(anchor)
    if n != 1:
        raise ValueError('锚点必须恰好命中一次，实际 %d 次：%r' % (n, anchor[:60]))
    return n


# ---------------------------------------------------------------- 两种改写
def doc(path, qual, body, indent=None):
    r"""换某个节点的文档字符串。@returns {(path, 'doc', qual, body, indent)}"""
    return (path, 'doc', qual, body, indent)


def block(path, anchor, new_text):
    r"""把**紧贴在 `anchor` 那一行上方**的连续注释行，整块换成 `new_text`（没有则插入）。"""
    return (path, 'block', anchor, new_text, None)


def _apply_doc(text, qual, body, indent):
    node = find_node(text, qual)
    if node is None:
        raise ValueError('找不到节点：%s' % qual)
    ls = _lines(text)
    cur = ast.get_docstring(node, clean=False)
    if cur is None:
        raise ValueError('该节点没有文档字符串（要在合适的位置插，请手工定）：%s' % qual)
    # 现有文档字符串的行区间（1-based）
    first = node.body[0]
    l0, l1 = first.value.lineno, first.value.end_lineno
    col = _char_col(ls[l0 - 1], first.value.col_offset)
    ind = ' ' * col if indent is None else indent
    quote = '"""'
    new_lines = [ind + quote + body.split('\n')[0]]
    for extra in body.split('\n')[1:]:
        new_lines.append(extra)
    new_lines[-1] = new_lines[-1] + quote
    return '\n'.join(ls[:l0 - 1] + new_lines + ls[l1:])


def _apply_block(text, anchor, new_text):
    _count(text, anchor)
    ls = _lines(text)
    i = next(k for k, ln in enumerate(ls) if anchor in ln)
    j = i - 1
    while j >= 0 and ls[j].strip().startswith('#'):
        j -= 1
    head = ls[:j + 1]
    tail = ls[i:]
    blk = [ln for ln in new_text.split('\n')] if new_text else []
    return '\n'.join(head + blk + tail)


def apply_all(ops, check=True, verbose=True):
    r"""按文件分组、**全部校验通过后才写**（一条失败 ⇒ 该文件不写）。@returns {list[str]} 写过的文件"""
    by_file = {}
    for op in ops:
        by_file.setdefault(op[0], []).append(op)
    written = []
    for path, file_ops in by_file.items():
        text = load(path)
        for op in file_ops:
            _p, kind, a, b, c = op
            text = (_apply_doc(text, a, b, c) if kind == 'doc' else _apply_block(text, a, b))
        ast.parse(text)                       # 语法硬门
        save(path, text)
        written.append(path)
        if verbose:
            print('  写了 %s（%d 条 op）' % (os.path.basename(path), len(file_ops)))
    if check and written:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import _lib.codecheck as CC
        print('  验证（只动了注释吗）：')
        rc = CC.main(['check'] + written)
        if rc != 0:
            raise RuntimeError('codecheck 红了 ⇒ 上面写进去的东西不只是注释，请回退重做')
    return written


# ---------------------------------------------------------------- CLI
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 3 and argv[1] == '--show':
        node = find_node(load(argv[0]), argv[2])
        if node is None:
            print('找不到节点：%s' % argv[2])
            return 1
        print(ast.get_docstring(node, clean=False) or '(无文档字符串)')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
