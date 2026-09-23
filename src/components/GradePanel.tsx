import { Button, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 每卷代表色。形状用 jiaopian.svg 的三条路径，灌各卷颜色。
 * ⚠ 卷表 09-23 起是 public GUI 的 9 条预设（名字 =「胶卷 + 风格」，**不带作者名**）。
 *   卷名改了要同步这里，否则图标退回中性灰（不会报错，只是看不出是哪一卷）。
 */
const STOCK_COLORS: Record<string, { a: string; b: string; t: string }> = {
  Portra400薄荷: { a: '#e8a87c', b: '#c9764a', t: 'P400 薄荷' },
  Pro400H马卡龙: { a: '#a8c8b8', b: '#5f8f7a', t: '400H 马卡龙' },
  Portra400淡雅: { a: '#e6c39a', b: '#c09a6a', t: 'P400 淡雅' },
  Pro400H清风: { a: '#9ec9c0', b: '#5d9a90', t: '400H 清风' },
  C200过曝: { a: '#8fc9a8', b: '#4f9c74', t: 'C200 过曝' },
  Portra400空气感: { a: '#dfc3a8', b: '#b8946f', t: 'P400 空气感' },
  Ektar100浓彩: { a: '#d98878', b: '#b04a42', t: 'Ektar 浓彩' },
  C200青蓝: { a: '#8fb4c9', b: '#4f7a9c', t: 'C200 青蓝' },
  C200透明: { a: '#a8cfc9', b: '#6a9a94', t: 'C200 透明' },
};

/* 胶卷图形的三条路径（SV 提供的 jiaopian.svg） */
const P_FRAME =
  'M895.8 98.2c0.2 0.9 0.3 1.8 0.3 2.8v78.8c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12V101c0-1 0.1-1.9 0.3-2.8H191.7c0.2 0.9 0.3 1.8 0.3 2.8v78.8c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12V101c0-1 0.1-1.9 0.3-2.8H65v830h64.5c-0.9-1.7-1.5-3.6-1.5-5.7v-78.8c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v78.8c0 2.1-0.5 4-1.5 5.7h643c-0.9-1.7-1.5-3.6-1.5-5.7v-78.8c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v78.8c0 2.1-0.5 4-1.5 5.7h66.2v-830h-64.9z';
const P_WIN1 = 'M756.2 448.2H268.1c-6.6 0-12-5.4-12-12V203.8c0-6.6 5.4-12 12-12h488.1c6.6 0 12 5.4 12 12v232.4c0 6.6 5.4 12 12 12z';
const P_WIN2 = 'M756 831.7H267.9c-6.6 0-12-5.4-12-12V587.3c0-6.6 5.4-12 12-12H756c6.6 0 12 5.4 12 12v232.4c0 6.6-5.4 12-12 12z';

function FilmIcon({ name, size = 40 }: { name: string; size?: number }) {
  const c = STOCK_COLORS[name] || { a: '#9aa4b2', b: '#5b6472', t: name };
  return (
    <svg viewBox="0 0 1024 1024" width={size} height={size}>
      <path fill={c.b} d={P_FRAME} />
      <path fill={c.a} d={P_WIN1} />
      <path fill={c.a} d={P_WIN2} />
    </svg>
  );
}

/**
 * 调色台右栏 —— 只有两个选择器：
 *   · **胶片风格**（9 条预设）→ spektrafilm：负片 / 相纸 / 颗粒 / 柔光 / 光晕
 *   · **曝光风格**（高长调 / 中性调 / 暗调）→ svFilm：整张多亮、黑位到哪、白位到哪
 *
 * 渲染触发点只有两个：这里的「渲染」按钮、切进调色台。改风格不会自动出图。
 */
/** 批量出片那两个下拉的统一样式（原生 select：中文选项短，够用） */
const selStyle: React.CSSProperties = {
  flex: 1,
  padding: '5px 6px',
  borderRadius: 'var(--r-sm)',
  border: '1px solid var(--line)',
  background: 'var(--bg-hover)',
  color: 'var(--text)',
  fontSize: 11.5,
  cursor: 'pointer',
};

export function GradePanel() {
  const stocks = useStore((s) => s.stocks);
  const styles = useStore((s) => s.styles);
  const batchMinStar = useStore((s) => s.batchMinStar);
  const batchSide = useStore((s) => s.batchSide);
  const batchRunning = useStore((s) => s.batchRunning);
  const batchText = useStore((s) => s.batchText);
  const setBatchMinStar = useStore((s) => s.setBatchMinStar);
  const setBatchSide = useStore((s) => s.setBatchSide);
  const runBatchExport = useStore((s) => s.runBatchExport);
  const cancelBatchExport = useStore((s) => s.cancelBatchExport);
  const grade = useStore((s) => s.grade);
  const setGrade = useStore((s) => s.setGrade);
  const resetGrade = useStore((s) => s.resetGrade);
  const saveGradeToTheme = useStore((s) => s.saveGradeToTheme);
  const engineOk = useStore((s) => s.engineOk);
  const renderBusy = useStore((s) => s.renderBusy);
  const requestRender = useStore((s) => s.requestRender);

  const curStock = grade.stock || '';
  const curStyle = grade.style || '';

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
      gap="4"
      p="3"
      style={{
        width: 300,
        flex: '0 0 auto',
        borderLeft: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        overflow: 'auto',
      }}
    >
      {/* ---- ① 胶片风格（9 条预设，图标网格） ---- */}
      <div>
        <Text
          size="1"
          weight="bold"
          style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
        >
          胶片风格
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
              /* ★ 09-15 SV：**不要悬浮文字** —— 按钮底下本来就写着名字和说明，
                 悬停提示纯属重复，还老在鼠标划过时蹦出来挡视线。
                 ⚠ 别在注释里写出那个组件的 JSX 写法 —— 自检判断"还有没有它在用"
                 是搜源码的，自己的注释会把检查骗过去。 */
              <button
                key={s.name}
                data-stock={s.name}
                data-stock-on={on ? '1' : '0'}
                onClick={() => setGrade({ stock: s.name })}
                style={{
                  border: on ? '1px solid var(--accent)' : '1px solid var(--line)',
                  background: on ? 'rgba(10,132,255,.12)' : 'rgba(255,255,255,.03)',
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
            );
          })}
        </div>
        <Text size="1" style={{ color: 'var(--text-dim)', marginTop: 6 }}>
          {stocks.find((s) => s.name === curStock)?.desc || ''}
        </Text>
      </div>

      {/* ---- ② 曝光风格（三条档，一横排 chip） ---- */}
      <div>
        <Text
          size="1"
          weight="bold"
          style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
        >
          曝光风格
        </Text>
        <Flex gap="1" mt="2" wrap="wrap">
          {styles.map((b) => {
            const on = curStyle === b.name;
            return (
              <button
                key={b.name}
                data-style={b.name}
                data-style-on={on ? '1' : '0'}
                onClick={() => setGrade({ style: b.name })}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  gap: 2,
                  padding: '6px 10px',
                  borderRadius: 'var(--r-md)',
                  border: on ? '1px solid var(--accent)' : '1px solid var(--line)',
                  background: on ? 'rgba(10,132,255,.10)' : 'rgba(255,255,255,.03)',
                  color: on ? 'var(--accent)' : 'var(--text-dim)',
                  cursor: 'pointer',
                  fontSize: 12,
                  whiteSpace: 'nowrap',
                }}
              >
                <span>{b.name}</span>
                {typeof b.L50 === 'number' && (
                  <span style={{ fontSize: 10, opacity: 0.7 }}>
                    中位 {b.L50.toFixed(0)} · 暗 {b.L5?.toFixed(0)} · 亮{' '}
                    {b.L95?.toFixed(0)}
                  </span>
                )}
                {typeof b.evDown === 'number' && (
                  <span style={{ fontSize: 10, opacity: 0.7 }}>
                    压 {b.evDown.toFixed(2)} 档 · 高光 −{b.hiDown?.toFixed(0)} · 阴影 +
                    {b.shUp?.toFixed(0)}
                  </span>
                )}
              </button>
            );
          })}
        </Flex>
        <Text size="1" style={{ color: 'var(--text-dim)', marginTop: 6 }}>
          {styles.find((b) => b.name === curStyle)?.desc || ''}
        </Text>
      </div>

      {/* ---- ③ 渲染 / 导出 ---- */}
      <div>
        <Button
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
          改完风格按这里出图；进调色台也会自动出一次
        </Text>

        {/* ★★ 「导出成片」（09-15 SV 选「A」第 ② 项）：把**渲染结果**写成真照片文件。
            三件跟"屏幕上那张"不一样的事写在按钮下面：
              · 尺寸走**原图全尺寸**（颗粒是物理量、**越大颗粒越明显**）；
              · 要**重新解码 + 重新跑一遍**，原图尺寸一张 RAW 约 **6 分半**；
              · 相机信息（EXIF）保留，文件默认叫 `<原名>_svfilm.jpg`、存在原图旁边。 */}
        <Button
          mt="2"
          size="1"
          variant="soft"
          disabled={renderBusy || !engineOk}
          onClick={() => useStore.getState().exportImage()}
          style={{ width: '100%', cursor: renderBusy ? 'default' : 'pointer' }}
        >
          {renderBusy ? '引擎忙…' : '导出成片'}
        </Button>
        <Text
          size="1"
          style={{ color: 'var(--text-faint)', marginTop: 4, display: 'block' }}
        >
          按原图尺寸重出一张（RAW 约 6 分钟，等得久），相机信息保留
        </Text>
      </div>

      {/* ---- ④ 批量出片：整个目录用当前这套风格全出一遍 ---- */}
      <div>
        <Text
          size="1"
          weight="bold"
          style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
        >
          批量出片
        </Text>
        <Text
          size="1"
          style={{ color: 'var(--text-faint)', marginTop: 4, display: 'block' }}
        >
          用上面的胶片风格 + 曝光风格，把这个目录里的片全出一遍，写进
          「调色待验收」子目录。打星的「同步星级」会把成片搬进星级桶。
        </Text>
        <Flex gap="2" mt="2" align="center">
          <select
            data-batch-star="1"
            value={String(batchMinStar)}
            onChange={(e) => setBatchMinStar(Number(e.target.value))}
            disabled={batchRunning}
            style={selStyle}
          >
            <option value="0">全部</option>
            <option value="1">只出 ★≥1</option>
            <option value="2">只出 ★≥2</option>
            <option value="3">只出 ★≥3</option>
          </select>
          <select
            data-batch-side="1"
            value={String(batchSide)}
            onChange={(e) => setBatchSide(Number(e.target.value))}
            disabled={batchRunning}
            style={selStyle}
          >
            <option value="1600">长边 1600</option>
            <option value="2048">长边 2048</option>
            <option value="3000">长边 3000</option>
            <option value="0">原图全尺寸</option>
          </select>
        </Flex>
        <Button
          mt="2"
          size="1"
          variant="solid"
          disabled={batchRunning || !engineOk}
          onClick={runBatchExport}
          style={{ width: '100%', cursor: batchRunning ? 'default' : 'pointer' }}
        >
          {batchRunning ? '批量出片中…' : '批量出片'}
        </Button>
        {batchRunning && (
          <Flex justify="between" align="center" mt="1" gap="2">
            <Text size="1" style={{ color: 'var(--text-dim)' }}>
              {batchText || '…'}
            </Text>
            <Button size="1" variant="ghost" color="red" onClick={cancelBatchExport}>
              停
            </Button>
          </Flex>
        )}
        <Text
          size="1"
          style={{ color: 'var(--text-faint)', marginTop: 4, display: 'block' }}
        >
          ⚠ 尺寸越大越慢：2048 约 30 秒/张，原图全尺寸约 6 分半/张。
        </Text>
      </div>

      <Flex gap="2">
        <Button
          size="1"
          variant="ghost"
          onClick={resetGrade}
          title="曝光风格回引擎默认档（胶片风格不动）"
        >
          恢复默认
        </Button>
        <Button
          size="1"
          variant="ghost"
          onClick={saveGradeToTheme}
          title="把这个目录的风格组合记住，下次进来自动套回"
        >
          存到目录
        </Button>
      </Flex>
    </Flex>
  );
}
