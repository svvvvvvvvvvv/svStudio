import { useMemo } from 'react';
import { Button, Flex, Slider, Text, Tooltip } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 每卷代表色（沿用 09-14 已提交的那套，SV 已目测确认「A」）。
 * 形状用 jiaopian.svg 的三条路径，灌各卷颜色；原图是单色灰，不灌色 5 卷一模一样。
 */
const STOCK_COLORS: Record<string, { a: string; b: string; t: string }> = {
  neutral: { a: '#4b5563', b: '#374151', t: '中性' },
  portra400: { a: '#e8a87c', b: '#c9764a', t: 'Portra 400' },
  fuji_c200: { a: '#8fc9a8', b: '#4f9c74', t: 'C200' },
  pro400h: { a: '#a8c8b8', b: '#5f8f7a', t: 'Pro 400H' },
  ektar100: { a: '#d98878', b: '#b04a42', t: 'Ektar 100' },
  cinestill800t: { a: '#d9a066', b: '#a86a2c', t: '500T' },
};

/* 胶卷图形的三条路径（SV 提供的 jiaopian.svg） */
const P_FRAME =
  'M895.8 98.2c0.2 0.9 0.3 1.8 0.3 2.8v78.8c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12V101c0-1 0.1-1.9 0.3-2.8H191.7c0.2 0.9 0.3 1.8 0.3 2.8v78.8c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12V101c0-1 0.1-1.9 0.3-2.8H65v830h64.5c-0.9-1.7-1.5-3.6-1.5-5.7v-78.8c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v78.8c0 2.1-0.5 4-1.5 5.7h643c-0.9-1.7-1.5-3.6-1.5-5.7v-78.8c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v78.8c0 2.1-0.5 4-1.5 5.7h66.2v-830h-64.9z';
const P_WIN1 = 'M756.2 448.2H268.1c-6.6 0-12-5.4-12-12V203.8c0-6.6 5.4-12 12-12h488.1c6.6 0 12 5.4 12 12v232.4c0 6.6-5.4 12-12 12z';
const P_WIN2 = 'M756 831.7H267.9c-6.6 0-12-5.4-12-12V587.3c0-6.6 5.4-12 12-12H756c6.6 0 12 5.4 12 12v232.4c0 6.6-5.4 12-12 12z';

function FilmIcon({ name, size = 40 }: { name: string; size?: number }) {
  const c = STOCK_COLORS[name] || STOCK_COLORS.neutral;
  const perf = name === 'neutral' ? '#6E6E6E' : c.b;
  const w1 = name === 'neutral' ? '#8C8C8C' : c.a;
  return (
    <svg viewBox="0 0 1024 1024" width={size} height={size}>
      <path fill={perf} d={P_FRAME} />
      <path fill={w1} d={P_WIN1} />
      <path fill={c.a} d={P_WIN2} />
    </svg>
  );
}

/**
 * 调色台右栏。
 * ★ 这里是 React 化收益最明显的地方：老代码要 7 个 paintXxx 函数手搓 DOM，
 *   现在只是"把状态映射成 JSX"，参数变了界面自动更新，不用管重画时机。
 *
 * ★ 09-15 SV 定的渲染策略：**改任何东西都不自动出图**（换卷/换基准/拉滑杆/换图都不动画面），
 *   只有两个触发点 —— ① 这里的「渲染」按钮 ② 切进/进入调色台。
 *   所以按钮放在最显眼的「胶片卷」下面。
 */
export function GradePanel() {
  const stocks = useStore((s) => s.stocks);
  const bases = useStore((s) => s.bases);
  const paramDefs = useStore((s) => s.paramDefs);
  const grade = useStore((s) => s.grade);
  const setGrade = useStore((s) => s.setGrade);
  const engineOk = useStore((s) => s.engineOk);
  const renderBusy = useStore((s) => s.renderBusy);
  const requestRender = useStore((s) => s.requestRender);

  const curStock = grade.stock || 'portra400';
  const isSpek = useMemo(
    () => !!stocks.find((s) => s.name === curStock)?.spek,
    [stocks, curStock]
  );

  /** ★ 不生效的滑杆不列：真卷下影调/质感那几根是拧不动的（真卷自带 H&D+颗粒+halation） */
  const defs = useMemo(
    () =>
      paramDefs.filter((p) => {
        if (p.spek === true && !isSpek) return false;
        if (p.spek === false && isSpek) return false;
        return true;
      }),
    [paramDefs, isSpek]
  );

  /** 按 grp 分组（像 LR 那样平铺，不用下拉框） */
  const groups = useMemo(() => {
    const m = new Map<string, typeof defs>();
    for (const d of defs) {
      const k = d.grp || '其它';
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(d);
    }
    return [...m.entries()];
  }, [defs]);

  const params = grade.params || {};

  if (!engineOk) {
    return (
      <Flex direction="column" gap="2" p="3">
        <Text size="1" style={{ color: 'var(--text-dim)' }}>
          调色引擎未启动
        </Text>
      </Flex>
    );
  }

  return (
    <Flex
      direction="column"
      gap="3"
      p="3"
      style={{
        width: 300,
        flex: '0 0 auto',
        borderLeft: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        overflow: 'auto',
      }}
    >
      {/* ---- 胶片卷（图标网格） ---- */}
      <div>
        <Text
          size="1"
          weight="bold"
          style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
        >
          胶片卷
        </Text>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3,1fr)',
            gap: 6,
            marginTop: 6,
          }}
        >
          {stocks.map((s) => {
            const on = s.name === curStock;
            return (
              <Tooltip key={s.name} content={s.label || s.name}>
                <button
                  onClick={() => setGrade({ stock: s.name })}
                  style={{
                    border: on
                      ? '1px solid var(--accent)'
                      : '1px solid var(--line)',
                    background: on
                      ? 'rgba(10,132,255,.12)'
                      : 'rgba(255,255,255,.03)',
                    borderRadius: 'var(--r-md)',
                    padding: '7px 3px 5px',
                    cursor: 'pointer',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 3,
                  }}
                >
                  <FilmIcon name={s.name} />
                  <span
                    style={{
                      fontSize: 9.5,
                      color: on ? 'var(--accent)' : 'var(--text-dim)',
                    }}
                  >
                    {STOCK_COLORS[s.name]?.t || s.name}
                  </span>
                </button>
              </Tooltip>
            );
          })}
        </div>
        <Text size="1" style={{ color: 'var(--text-dim)', marginTop: 6 }}>
          {stocks.find((s) => s.name === curStock)?.desc || ''}
        </Text>

        {/* ★★ 「渲染」按钮（SV 09-15：放到胶片卷下面）。
            改卷/改基准/拉滑杆/换图**都不会自动出图** —— 按下它才出，切进调色台时也会出一次。 */}
        <Button
          mt="3"
          size="2"
          variant="solid"
          disabled={renderBusy}
          onClick={requestRender}
          style={{ width: '100%', cursor: renderBusy ? 'default' : 'pointer' }}
        >
          {renderBusy ? '出图中…' : '渲染'}
        </Button>
        <Text
          size="1"
          style={{ color: 'var(--text-faint)', marginTop: 5, display: 'block' }}
        >
          改完卷 / 基准 / 参数，按这里出图（不会自动出）
        </Text>
      </div>

      {/* ---- 成色基准（竖排单选） ---- */}
      <div>
        <Text
          size="1"
          weight="bold"
          style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
        >
          成色基准
        </Text>
        <Flex direction="column" gap="1" mt="1">
          {bases.map((b) => {
            const on = grade.base === b.name;
            return (
              <button
                key={b.name}
                onClick={() => setGrade({ base: b.name })}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  padding: '6px 9px',
                  borderRadius: 'var(--r-sm)',
                  border: on ? '1px solid var(--accent)' : '1px solid var(--line)',
                  background: on
                    ? 'rgba(10,132,255,.10)'
                    : 'rgba(255,255,255,.03)',
                  color: on ? 'var(--accent)' : 'var(--text-dim)',
                  cursor: 'pointer',
                  fontSize: 11.5,
                  textAlign: 'left',
                }}
              >
                <span
                  style={{
                    width: 11,
                    height: 11,
                    borderRadius: 999,
                    border: on ? '4px solid var(--accent)' : '1.5px solid var(--line)',
                    boxSizing: 'border-box',
                    flex: '0 0 auto',
                  }}
                />
                {b.label || b.name}
              </button>
            );
          })}
        </Flex>
      </div>

      {/* ---- 参数（按组平铺） ---- */}
      {groups.map(([g, list]) => (
        <div key={g}>
          <Text
            size="1"
            weight="bold"
            style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
          >
            {g}
          </Text>
          <Flex direction="column" gap="3" mt="2">
            {list.map((d) => {
              const v = params[d.k] ?? (d.lo + d.hi) / 2;
              return (
                <div key={d.k}>
                  <Flex justify="between" align="baseline">
                    <Tooltip content={d.d || ''}>
                      <Text size="1">{d.name}</Text>
                    </Tooltip>
                    <Text size="1" style={{ color: 'var(--text-dim)' }}>
                      {v.toFixed(2)}
                    </Text>
                  </Flex>
                  <Slider
                    size="1"
                    min={d.lo}
                    max={d.hi}
                    step={d.step}
                    value={[v]}
                    onValueChange={([nv]) =>
                      setGrade({ params: { ...params, [d.k]: nv } })
                    }
                  />
                </div>
              );
            })}
          </Flex>
        </div>
      ))}

      <Flex gap="2" mt="1">
        <Button size="1" variant="ghost">
          恢复默认
        </Button>
        <Button size="1" variant="ghost">
          存到主题
        </Button>
      </Flex>
    </Flex>
  );
}
