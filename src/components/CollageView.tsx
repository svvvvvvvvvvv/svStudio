import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';
import { LAYOUTS, getLayout, cellCount } from '../store/collageLayouts';
import type { CollageGroup, CollageItem, CollageLayout } from '../store/useStore';
import { paintCollage } from './collagePaint';
import type { PaintImage } from './collagePaint';
import { load as loadImage, peek, errorOf } from './collageImageCache';

/**
 * 拼图台主区：组条 + 布局模板条 + 实时预览画布。
 *
 * ★ 11-04 SV 定的「搭配形式」：片归**拼图组**，不是拼完一次就导出。
 *   · 组可增删切换，每组有自己的布局与格子；一张片只属于一个组（store 保证互斥）
 *   · 点「导出」= 把所有非空组各出一张，不是一个一个手动来
 *   · 底栏缩略图会打「组 N」角标，随时看得出这张片归哪组了
 *
 * ★ 预览和导出都用 `paintCollage` 这一份绘制实现：预览喂长边 1600 的副本、
 *   导出喂原图，gap/pad 按比例同步 ⇒ 所见即所得。
 */
const PREVIEW_SIDE = 1600;

/** 文件名清洗（与主进程 safeCollageName 同一套规则）：去扩展名、特殊符号→_、留中文/字母/数字/-/_ */
const cleanStem = (s: string) =>
  String(s || '')
    .replace(/\.[^./\\]+$/, '')
    .replace(/[^\w\u4e00-\u9fa5-]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 48) || 'Photo';

/** 某一组导出的目录与文件名（规则对所有组一致） */
function exportMeta(g: CollageGroup, sessionPath: string, format: 'png' | 'jpeg') {
  const used = g.items.filter((x): x is CollageItem => !!x);
  const stems = used.map((x) => cleanStem(x.name));
  const name = stems.length
    ? stems.slice(0, 4).join('_and_') + (stems.length > 4 ? '_andMore' : '')
    : 'Collage';
  const withPath = used.find((x) => !!x.path);
  const dir = withPath?.path
    ? withPath.path.replace(/[\\/][^\\/]+$/, '')
    : (sessionPath || '');
  return { name, dir, ext: format === 'png' ? 'png' : 'jpg' };
}

function LayoutIcon({ id, active }: { id: CollageLayout; active: boolean }) {
  const def = getLayout(id);
  return (
    <svg width="30" height="24" viewBox="0 0 30 24" aria-hidden="true">
      <rect x="0" y="0" width="30" height="24" rx="3"
        fill={active ? 'rgba(10,132,255,.18)' : 'transparent'} />
      {def.icon.map((c, i) => (
        <rect key={i}
          x={c[0] * 30} y={c[1] * 24} width={c[2] * 30} height={c[3] * 24}
          rx="1.5"
          fill={active ? 'var(--accent)' : 'var(--text-faint)'}
          opacity={active ? 0.95 : 0.55} />
      ))}
    </svg>
  );
}

export function CollageView() {
  const groups = useStore((s) => s.collageGroups);
  const activeIdx = useStore((s) => s.collageActive);
  const opts = useStore((s) => s.collageOptions);
  const target = useStore((s) => s.collageTarget);
  const exportTick = useStore((s) => s.collageExportTick);
  const sessionPath = useStore((s) => s.sessionPath);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const setLayout = useStore((s) => s.setCollageLayout);
  const setTarget = useStore((s) => s.setCollageTarget);
  const setOptions = useStore((s) => s.setCollageOptions);
  const fillFromDock = useStore((s) => s.collageFillFromDock);
  const addItems = useStore((s) => s.collageAdd);
  const setActive = useStore((s) => s.setCollageActive);
  const addGroup = useStore((s) => s.collageAddGroup);
  const removeGroup = useStore((s) => s.collageRemoveGroup);
  const showToast = useStore((s) => s.showToast);
  const setBusy = useStore((s) => s.setBusy);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const exporting = useRef(false);
  const [tick, setTick] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [info, setInfo] = useState({ w: 0, h: 0 });
  const [splitting, setSplitting] = useState(false);

  const g = groups[activeIdx] || groups[0];
  const layout: CollageLayout = g?.layout || 'v2';
  const n = cellCount(layout);
  const slots = Array.from({ length: n }, (_, i) => (g?.items[i] ?? null));
  const filled = slots.filter(Boolean).length;
  const assigned = groups.reduce((a, x) => a + x.items.filter(Boolean).length, 0);
  const nonEmpty = groups.filter((x) => x.items.some(Boolean));

  /* ---- 加载当前组预览图（每张只解码一次，完成后 tick 触发重绘） ---- */
  useEffect(() => {
    let alive = true;
    let changed = false;
    const jobs = slots.map(async (it) => {
      if (!it || peek(it, PREVIEW_SIDE)) return;
      try { await loadImage(it, PREVIEW_SIDE); changed = true; }
      catch (e) { changed = true; }
    });
    Promise.all(jobs).then(() => { if (alive && changed) setTick((t) => t + 1); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groups, activeIdx]);

  /* ---- 实时重绘：任何参数/图片/布局/切组变化都立刻反映 ---- */
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const imgs: (PaintImage | null)[] = slots.map((it) => {
      if (!it) return null;
      const hit = peek(it, PREVIEW_SIDE);
      return hit ? { img: hit.img, ow: hit.ow, oh: hit.oh } : null;
    });
    const out = paintCollage(cv, imgs, layout, opts, 0, true);
    setInfo({ w: out.baseW, h: out.baseH });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, groups, activeIdx, opts]);

  /* 当前组的导出目标（状态行显示，不用点了才知道会写到哪） */
  const exportInfo = useMemo(
    () => exportMeta(g, sessionPath, opts.format),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [groups, activeIdx, sessionPath, opts.format]
  );

  /* ---- 点击画布：落在哪一格就选中哪一格（compare 时拖动分割线） ---- */
  const hitCell = (clientX: number, clientY: number): number => {
    const cv = canvasRef.current;
    if (!cv) return 0;
    const r = cv.getBoundingClientRect();
    const nx = (clientX - r.left) / Math.max(1, r.width);
    const ny = (clientY - r.top) / Math.max(1, r.height);
    const cells = getLayout(layout).cells;
    for (let i = 0; i < cells.length; i++) {
      const [x, y, w, h] = cells[i];
      if (nx >= x && nx <= x + w && ny >= y && ny <= y + h) return i;
    }
    return 0;
  };
  const onCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) =>
    setTarget(hitCell(e.clientX, e.clientY));

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (layout !== 'compare') return;
    setSplitting(true);
    const cv = canvasRef.current;
    if (!cv) return;
    const r = cv.getBoundingClientRect();
    setOptions({ split: Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width))) });
  };
  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!splitting || layout !== 'compare') return;
    const cv = canvasRef.current;
    if (!cv) return;
    const r = cv.getBoundingClientRect();
    setOptions({ split: Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width))) });
  };
  const stopSplit = () => setSplitting(false);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const got: CollageItem[] = [];
    for (const f of Array.from(e.dataTransfer?.files || [])) {
      let full = '';
      try { full = API.getPathForFile(f) || ''; } catch (_) { /* 不是本地文件 */ }
      if (full) got.push({ key: full, name: f.name, path: full });
    }
    if (!got.length) { showToast('拖进来的不是本地图片（拿不到路径）'); return; }
    addItems(got);
    showToast(`加了 ${got.length} 张到组 ${activeIdx + 1}`);
  };

  const addCurrent = useCallback(() => {
    const p = photos[cur];
    if (!p) { showToast('当前目录没有照片'); return; }
    fillFromDock({
      key: (sessionPath || '') + '||' + p.rel,
      name: p.name,
      sessionPath,
      rel: p.rel
    });
  }, [photos, cur, sessionPath, fillFromDock, showToast]);

  /* ---- ★ 批量导出：把所有非空组各出一张（不是一个一个手动来） ---- */
  useEffect(() => {
    if (!exportTick || exporting.current) return;
    (async () => {
      const todo = groups
        .map((gg, i) => ({ gg, i }))
        .filter(({ gg }) => gg.items.some(Boolean));
      if (!todo.length) {
        showToast('还没有分组的片 —— 先从底栏点图，或把图片拖进画布');
        return;
      }
      exporting.current = true;
      setBusy(true, `正在导出 ${todo.length} 个拼图组…`);
      let done = 0;
      const failed: string[] = [];
      let firstDir = '';
      try {
        for (let k = 0; k < todo.length; k++) {
          const { gg, i } = todo[k];
          setBusy(true, `正在导出第 ${k + 1}/${todo.length} 组…`);
          const cnt = cellCount(gg.layout);
          const gs = Array.from({ length: cnt }, (_, j) => gg.items[j] ?? null);
          const full: (PaintImage | null)[] = [];
          for (const it of gs) {
            if (!it) { full.push(null); continue; }
            const hit = await loadImage(it, 0).catch(() => null);
            full.push(hit ? { img: hit.img, ow: hit.ow, oh: hit.oh } : null);
          }
          const cv = document.createElement('canvas');
          const out = paintCollage(cv, full, gg.layout, opts, opts.outputSide);
          const dataUrl = opts.format === 'png'
            ? cv.toDataURL('image/png')
            : cv.toDataURL('image/jpeg', 0.95);
          const meta = exportMeta(gg, sessionPath, opts.format);
          const r = await API.collageSave({
            dataUrl, name: meta.name, dir: meta.dir, w: out.w, h: out.h
          });
          if (r.ok) { done++; if (!firstDir) firstDir = r.dir || meta.dir; }
          else if (!r.canceled) failed.push(`组 ${i + 1}：${r.error || '原因没返回'}`);
        }
        if (done) showToast(`已导出 ${done} 个拼图组 → ${firstDir}${failed.length ? ' · 失败 ' + failed.length + ' 组' : ''}`);
        if (failed.length) showToast(failed[0]);
      } catch (e) {
        showToast('导出失败：' + String(e));
      } finally {
        exporting.current = false;
        setBusy(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exportTick]);

  const errs = slots
    .map((it) => (it ? errorOf(it, PREVIEW_SIDE) : null))
    .filter(Boolean) as string[];

  return (
    <Flex direction="column" style={{ flex: 1, minHeight: 0, minWidth: 0 }}>
      {/* ⓪ 拼图组条 —— 片归组，切换组时布局/格子整套跟着换 */}
      <Flex align="center" gap="1" px="3" py="2" wrap="wrap"
        style={{ borderBottom: '1px solid var(--line)', background: 'var(--bg-panel)', flex: '0 0 auto' }}>
        <Text size="1" style={{ color: 'var(--text-dim)', marginRight: 6 }}>拼图组</Text>
        {groups.map((gg, i) => {
          const c = gg.items.filter(Boolean).length;
          const cap = cellCount(gg.layout);
          return (
            <button key={gg.id} onClick={() => setActive(i)}
              title={`第 ${i + 1} 组 · ${getLayout(gg.layout).name} · ${c}/${cap} 张`}
              style={{
                border: '1px solid ' + (i === activeIdx ? 'var(--accent)' : 'var(--line)'),
                background: i === activeIdx ? 'var(--bg-hover)' : 'transparent',
                color: i === activeIdx ? 'var(--text)' : 'var(--text-dim)',
                borderRadius: 'var(--r-sm)', padding: '3px 9px', cursor: 'pointer', fontSize: 11.5
              }}>
              组 {i + 1} <span style={{ opacity: .7 }}>{c}/{cap}</span>
            </button>
          );
        })}
        <Button size="1" variant="soft" onClick={addGroup}>+ 新组</Button>
        <Button size="1" variant="ghost" color="red" disabled={groups.length <= 1}
          onClick={() => removeGroup(activeIdx)}>删除本组</Button>
        <div style={{ flex: 1 }} />
        <Text size="1" style={{ color: 'var(--text-faint)' }}>
          共 {groups.length} 组 · 已分配 {assigned} 张 · 点导出会一起出 {nonEmpty.length} 张
        </Text>
      </Flex>

      {/* ① 布局模板条（作用于当前组） */}
      <Flex align="center" gap="1" px="3" py="2" wrap="wrap"
        style={{ borderBottom: '1px solid var(--line)', background: 'var(--bg-panel)', flex: '0 0 auto' }}>
        <Text size="1" style={{ color: 'var(--text-dim)', marginRight: 6 }}>布局</Text>
        {LAYOUTS.map((l) => (
          <button key={l.id} title={l.hint} onClick={() => setLayout(l.id)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              border: '1px solid ' + (layout === l.id ? 'var(--accent)' : 'transparent'),
              background: layout === l.id ? 'var(--bg-hover)' : 'transparent',
              color: layout === l.id ? 'var(--text)' : 'var(--text-dim)',
              borderRadius: 'var(--r-sm)', padding: '3px 7px', cursor: 'pointer', fontSize: 11.5
            }}>
            <LayoutIcon id={l.id} active={layout === l.id} />
            {l.name}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <Button size="1" variant="soft" onClick={addCurrent}>+ 当前这张</Button>
        <Button size="1" variant="ghost" onClick={async () => {
          const paths = await API.collagePickFiles();
          if (!paths.length) return;
          addItems(paths.map((p) => ({
            key: p, name: p.split(/[\\/]/).pop() || p, path: p
          })));
          showToast(`加了 ${paths.length} 张到组 ${activeIdx + 1}`);
        }}>从文件添加…</Button>
      </Flex>

      {/* ② 画布 */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        style={{
          flex: 1, minHeight: 0, position: 'relative', overflow: 'hidden',
          background: 'var(--bg)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          outline: dragOver ? '2px dashed var(--accent)' : 'none',
          outlineOffset: -8
        }}>
        <canvas
          ref={canvasRef}
          onClick={onCanvasClick}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={stopSplit}
          onMouseLeave={stopSplit}
          style={{
            maxWidth: 'calc(100% - 32px)', maxHeight: 'calc(100% - 32px)',
            objectFit: 'contain', cursor: layout === 'compare' ? 'ew-resize' : 'pointer',
            boxShadow: '0 4px 24px rgba(0,0,0,.45)'
          }}
        />
        {dragOver && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
            justifyContent: 'center', background: 'rgba(10,132,255,.12)', pointerEvents: 'none',
            fontSize: 14, color: 'var(--text)' }}>松手把图片放进组 {activeIdx + 1}</div>
        )}
      </div>

      {/* ③ 状态行：空/错误/尺寸/导出位置都必须说清 */}
      <Flex align="center" gap="3" px="3" py="2"
        style={{ borderTop: '1px solid var(--line)', background: 'var(--bg-panel)', flex: '0 0 auto' }}>
        <Text size="1" style={{ color: 'var(--text-dim)' }}>
          {filled === 0
            ? `组 ${activeIdx + 1} 是空的 —— 点底栏缩略图、点「+ 当前这张」，或直接把图片拖进来`
            : `组 ${activeIdx + 1} 已放 ${filled}/${n} 格 · 当前选中第 ${Math.min(target + 1, n)} 格`}
        </Text>
        {errs.length > 0 && (
          <Text size="1" style={{ color: 'var(--c-red)' }}>读图失败：{errs[0]}</Text>
        )}
        <div style={{ flex: 1 }} />
        {filled > 0 && (
          <Text size="1" title={`导出到 ${exportInfo.dir}\\Collage\\${exportInfo.name}.${exportInfo.ext}`}
            style={{ color: 'var(--text-faint)', maxWidth: 260, overflow: 'hidden',
              textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            → Collage\{exportInfo.name}.{exportInfo.ext}
          </Text>
        )}
        {info.w > 0 && (
          <Badge color="gray">
            {opts.outputSide > 0 ? `导出 ${opts.outputSide}px 长边` : `原尺寸 ${Math.round(info.w)}×${Math.round(info.h)}`}
          </Badge>
        )}
        <Badge color={opts.format === 'png' ? 'green' : 'orange'}>
          {opts.format === 'png' ? 'PNG 无损' : 'JPEG 有损'}
        </Badge>
      </Flex>
    </Flex>
  );
}
