import { Button, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';
import { cellCount, getLayout } from '../store/collageLayouts';

/**
 * 拼图台右栏：片位 + 参数 + 导出。
 *
 * ★ 不用 Radix 的 Slider（role="slider"）：调色台的自检钉死"右栏零滑杆"，
 *   这里用数字框 + 加减按钮，数值更精确，也不碰那条约束。
 * ★ 画质相关的三个开关都把**代价写在旁边**（裁切 / 重采样 / 有损），
 *   默认全关：contain + 原尺寸 + PNG = 逐像素无损。
 */

const numStyle: React.CSSProperties = {
  width: 62, padding: '3px 5px', textAlign: 'center',
  borderRadius: 'var(--r-sm)', border: '1px solid var(--line)',
  background: 'var(--bg-hover)', color: 'var(--text)', fontSize: 12
};
const miniBtn: React.CSSProperties = {
  width: 24, height: 24, border: '1px solid var(--line)',
  background: 'var(--bg-hover)', color: 'var(--text)',
  borderRadius: 'var(--r-sm)', cursor: 'pointer', fontSize: 13, lineHeight: 1
};
const inputCss = `input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}`;

function NumRow({ label, value, min, max, step = 1, onChange, hint }: {
  label: string; value: number; min: number; max: number; step?: number;
  onChange: (v: number) => void; hint?: string;
}) {
  const clamp = (v: number) => Math.max(min, Math.min(max, v));
  return (
    <div style={{ marginBottom: 10 }}>
      <Flex align="center" gap="2">
        <Text size="1" style={{ flex: 1, color: 'var(--text-dim)' }}>{label}</Text>
        <button style={miniBtn} onClick={() => onChange(clamp(value - step))}>−</button>
        <input type="number" value={value} min={min} max={max} step={step} style={numStyle}
          onChange={(e) => onChange(clamp(Number(e.target.value) || 0))} />
        <button style={miniBtn} onClick={() => onChange(clamp(value + step))}>+</button>
      </Flex>
      {hint && <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', marginTop: 2 }}>{hint}</Text>}
    </div>
  );
}

function Seg<T extends string | number>({ value, options, onChange }: {
  value: T; options: { v: T; label: string; hint?: string }[]; onChange: (v: T) => void;
}) {
  return (
    <Flex gap="1" style={{ background: 'var(--bg)', padding: 2, borderRadius: 'var(--r-sm)' }}>
      {options.map((o) => (
        <button key={String(o.v)} title={o.hint} onClick={() => onChange(o.v)}
          style={{
            flex: 1, border: 0, cursor: 'pointer', padding: '4px 6px', borderRadius: 4,
            fontSize: 11.5, whiteSpace: 'nowrap',
            color: value === o.v ? 'var(--text)' : 'var(--text-dim)',
            background: value === o.v ? 'var(--bg-hover)' : 'transparent'
          }}>{o.label}</button>
      ))}
    </Flex>
  );
}

const BG_PRESETS = [
  { c: '#ffffff', n: '白' },
  { c: '#000000', n: '黑' },
  { c: '#1c1c1e', n: '深灰' },
  { c: '#e5e5ea', n: '浅灰' }
];

export function CollagePanel() {
  const groups = useStore((s) => s.collageGroups);
  const activeIdx = useStore((s) => s.collageActive);
  const g = groups[activeIdx] || groups[0];
  const items = g?.items || [];
  const layout = g?.layout || 'v2';
  const opts = useStore((s) => s.collageOptions);
  const target = useStore((s) => s.collageTarget);
  const setTarget = useStore((s) => s.setCollageTarget);
  const setOptions = useStore((s) => s.setCollageOptions);
  const removeAt = useStore((s) => s.collageRemoveAt);
  const swap = useStore((s) => s.collageSwap);
  const clear = useStore((s) => s.collageClear);
  const requestExport = useStore((s) => s.requestCollageExport);
  const showToast = useStore((s) => s.showToast);

  const n = cellCount(layout);
  const def = getLayout(layout);
  const filled = items.slice(0, n).filter(Boolean).length;
  const nonEmpty = groups.filter((x) => x.items.some(Boolean)).length;

  return (
    <Flex direction="column" gap="3" p="3" style={{
      width: 300, flex: '0 0 auto',
      borderLeft: '1px solid var(--line)', background: 'var(--bg-panel)', overflow: 'auto'
    }}>
      <style>{inputCss}</style>

      {/* ---- ① 片位 ---- */}
      <div>
        <Text size="2" weight="bold">组 {activeIdx + 1} 的片位 · {def.name}</Text>
        <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', margin: '2px 0 8px' }}>
          {def.hint} ｜ 点底栏缩略图会填进本组的「当前选中」格（加到别组会自动从本组撤掉）
        </Text>
        <Flex direction="column" gap="1">
          {Array.from({ length: n }, (_, i) => {
            const it = items[i];
            const on = i === target;
            return (
              <Flex key={i} align="center" gap="2" onClick={() => setTarget(i)}
                style={{
                  padding: '5px 7px', borderRadius: 'var(--r-sm)', cursor: 'pointer',
                  border: '1px solid ' + (on ? 'var(--accent)' : 'var(--line)'),
                  background: on ? 'var(--bg-hover)' : 'transparent'
                }}>
                <Text size="1" style={{ color: 'var(--accent)', width: 16, flex: '0 0 auto' }}>
                  {String.fromCharCode(65 + i)}
                </Text>
                <Text size="1" style={{
                  flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  color: it ? 'var(--text)' : 'var(--text-faint)'
                }} title={it?.name || ''}>
                  {it ? it.name : '（空 · 点这里选中，再去底栏点图）'}
                </Text>
                {it && (
                  <button title="清空这一格" onClick={(e) => { e.stopPropagation(); removeAt(i); }}
                    style={{ ...miniBtn, width: 20, height: 20, fontSize: 11 }}>×</button>
                )}
              </Flex>
            );
          })}
        </Flex>
        <Flex gap="2" mt="2">
          <Button size="1" variant="soft" style={{ flex: 1 }} disabled={n < 2}
            onClick={() => { swap(0, 1); showToast('已交换 A / B'); }}>交换 A / B</Button>
          <Button size="1" variant="ghost" color="red" style={{ flex: 1 }}
            onClick={async () => {
              const ok = await API.confirmDialog({
                title: '清空本组', message: `确定清掉「组 ${activeIdx + 1}」里这 ${filled} 张图吗？`,
                detail: '只清本组的选片，其它组和磁盘上的照片都不动。'
              });
              if (ok) clear();
            }}>清空本组</Button>
        </Flex>
      </div>

      {/* ---- ② 间距与边框 ---- */}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
        <Text size="2" weight="bold" style={{ display: 'block', marginBottom: 8 }}>间距与边框</Text>
        <NumRow label="格子间距" value={opts.gap} min={0} max={200} step={2}
          onChange={(v) => setOptions({ gap: v })} hint="输出像素；预览会按比例同步缩小" />
        <NumRow label="四周留白" value={opts.pad} min={0} max={200} step={2}
          onChange={(v) => setOptions({ pad: v })} />
        <NumRow label="圆角" value={opts.radius} min={0} max={120} step={2}
          onChange={(v) => setOptions({ radius: v })} hint="0 = 直角，不裁内容" />
        <Text size="1" style={{ color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>背景 / 缝隙颜色</Text>
        <Flex gap="1" align="center">
          {BG_PRESETS.map((b) => (
            <button key={b.c} title={b.c} onClick={() => setOptions({ bg: b.c })}
              style={{
                width: 30, height: 24, cursor: 'pointer', borderRadius: 'var(--r-sm)',
                border: opts.bg.toLowerCase() === b.c ? '2px solid var(--accent)' : '1px solid var(--line)',
                background: b.c, color: b.n === '白' || b.n === '浅灰' ? '#000' : '#fff', fontSize: 10
              }}>{b.n}</button>
          ))}
          <input type="color" value={opts.bg} title="自定义颜色"
            onChange={(e) => setOptions({ bg: e.target.value })}
            style={{ width: 30, height: 24, padding: 0, border: '1px solid var(--line)',
              background: 'transparent', cursor: 'pointer' }} />
        </Flex>
      </div>

      {/* ---- ③ 画质 ---- */}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
        <Text size="2" weight="bold" style={{ display: 'block', marginBottom: 8 }}>画质</Text>
        <Text size="1" style={{ color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>图片怎么放进格子</Text>
        <Seg value={opts.fit} onChange={(v) => setOptions({ fit: v })} options={[
          { v: 'contain', label: '完整不裁', hint: '默认 · 原始像素 1:1，四周可能留白' },
          { v: 'cover', label: '裁切填满', hint: '铺满格子，会裁掉画面边缘' }
        ]} />
        <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', margin: '3px 0 10px' }}>
          {opts.fit === 'contain' ? '不缩放、不裁切 → 配合 PNG 逐像素无损' : '⚠ 会裁掉画面边缘'}
        </Text>

        <Text size="1" style={{ color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>导出长边</Text>
        <Seg value={opts.outputSide} onChange={(v) => setOptions({ outputSide: v })} options={[
          { v: 0, label: '原尺寸', hint: '默认 · 不重采样' },
          { v: 2048, label: '2048' },
          { v: 3000, label: '3000' },
          { v: 4096, label: '4096' }
        ]} />
        <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', margin: '3px 0 10px' }}>
          {opts.outputSide === 0 ? '成品尺寸 = 各图原始像素按布局相加' : '⚠ 会重采样，画质有损失（文件更小）'}
        </Text>

        <Text size="1" style={{ color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>输出格式</Text>
        <Seg value={opts.format} onChange={(v) => setOptions({ format: v })} options={[
          { v: 'png', label: 'PNG 无损', hint: '默认' },
          { v: 'jpeg', label: 'JPEG', hint: '体积小，但有损二次压缩' }
        ]} />
        <Flex align="center" gap="2" mt="2">
          <label style={{ flex: 1, fontSize: 11.5, color: 'var(--text-dim)', cursor: 'pointer' }}>
            <input type="checkbox" checked={opts.label}
              onChange={(e) => setOptions({ label: e.target.checked })} style={{ marginRight: 6 }} />
            标 A / B / C 角标
          </label>
        </Flex>
      </div>

      {/* ---- ④ 导出 ---- */}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12, marginTop: 'auto' }}>
        <Button size="2" style={{ width: '100%' }} disabled={!nonEmpty} onClick={requestExport}>
          导出全部 {nonEmpty} 个拼图组
        </Button>
        <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', marginTop: 6, textAlign: 'center' }}>
          {!nonEmpty ? '先给某个组放一张图' : '每组各出一张 · ' + (filled > 0 ? '本组 ' + filled + ' 张 · ' : '') + (
            opts.format === 'png' && opts.outputSide === 0 && opts.fit === 'contain'
              ? 'PNG 无损 · 原图像素 · 不裁切'
              : `${opts.format === 'png' ? 'PNG' : 'JPEG'} · ${opts.outputSide > 0 ? opts.outputSide + 'px 长边' : '原尺寸'} · ${opts.fit === 'cover' ? '裁切填满' : '完整不裁'}`)}
        </Text>
        <Text size="1" style={{ color: 'var(--text-faint)', display: 'block', marginTop: 2, textAlign: 'center' }}>
          直接写到源目录 \ Collage，不用选位置
        </Text>
      </div>
    </Flex>
  );
}
