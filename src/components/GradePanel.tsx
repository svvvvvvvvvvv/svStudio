import { useMemo } from 'react';
import { Button, Flex, Popover, Slider, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 每卷代表色。形状用 jiaopian.svg 的三条路径，灌各卷颜色。
 * ⚠ 卷表 09-23 起是 public GUI 的 9 条预设（名字 =「胶卷 + 风格」，**不带作者名**）。
 *   卷名改了要同步这里，否则图标退回中性灰（不会报错，只是看不出是哪一卷）。
 */
const STOCK_COLORS: Record<string, { a: string; b: string; t: string }> = {
  neutral: { a: '#4b5563', b: '#374151', t: '中性' },
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
 * ★ 09-15 SV 定的渲染策略：**改任何东西都不自动出图**（换卷/换相纸/换基准/拉滑杆/换图都不动画面），
 *   只有两个触发点 —— ① 这里的「渲染」按钮 ② 切进/进入调色台。
 *   所以按钮放在最显眼的「胶片卷」下面。
 *
 * ★ 09-15 SV 选「C」：**相纸做成第二个下拉**。一张真卷出图 = (负片, 相纸) 二元组，
 *   负片决定"什么胶卷"、相纸决定"冲印在什么纸上"（肤色/冷暖/饱和/暗部厚薄）。
 *   原来只开放了负片那一半 —— 这是人像成色的另一半。
 *
 * ★★ 09-15（B3）右栏顺序（SV 口头定的，从上到下）：
 *    交卷（胶片卷）→ 渲染 → 出片（导出成片）→ 影调 → 真卷 → 物理（质感）→ 脸 →
 *    相纸 → 成色基准。
 *    ⚠ 参数那四组的**组名与组序都不在这里维护** —— 它是 `svFilm/service.py` 的 `PARAMS`
 *      顺序 + `grp` 字段决定的（这里是按 `defs` 的先后分组），改顺序就改引擎那张表。
 *    ⚠ 「相纸」原来排在滑杆**上面**，把参数往下推了；现在挪到参数之后、基准之前。
 */
export function GradePanel() {
  const stocks = useStore((s) => s.stocks);
  const bases = useStore((s) => s.bases);
  /* ★ 相纸表**只对当前这一卷有效**（默认相纸跟着卷走，换卷由 store 重拉）。
     中性卷下引擎返回空表 ⇒ 这一栏整个不显示（中性卷没有"相纸"这回事）。 */
  const papers = useStore((s) => s.papers);
  const paramDefs = useStore((s) => s.paramDefs);
  const grade = useStore((s) => s.grade);
  const setGrade = useStore((s) => s.setGrade);
  /* ★ 09-15 补：右下角那两个按钮原来是**死的**（没有 onClick），
     而主进程的 `get-grade` / `set-grade` 接口早就写好了、前端从来没调过 ——
     不是"缺功能"，是"接了半截"。
     「恢复默认」= 23 根滑杆回引擎出厂 + 基准回引擎默认 + 相纸回这一卷的配套纸
     （**不动卷**：卷是"拍什么"，不是"调出来的"）；「存到主题」= 把当前
     卷/相纸/基准/滑杆值写进 `config.grades[主题名]`，下次进这个主题自动套回。
     ⚠ 两个都不自动出图（沿用"只有两个触发点"的规矩）。 */
  const resetGrade = useStore((s) => s.resetGrade);
  const saveGradeToTheme = useStore((s) => s.saveGradeToTheme);
  const engineOk = useStore((s) => s.engineOk);
  const renderBusy = useStore((s) => s.renderBusy);
  const requestRender = useStore((s) => s.requestRender);

  const curStock = grade.stock || 'Portra400薄荷';
  const isEngine = useMemo(
    () => !!stocks.find((s) => s.name === curStock)?.engine,
    [stocks, curStock]
  );

  /** ★ 不生效的滑杆不列：引擎物理链（预设/真卷）下影调/质感那几根是拧不动的
   *  （引擎自带 H&D + 颗粒 + halation + 柔光）。 */
  const defs = useMemo(
    () =>
      paramDefs.filter((p) => {
        if (p.spek === true && !isEngine) return false;
        if (p.spek === false && isEngine) return false;
        return true;
      }),
    [paramDefs, isEngine]
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

  /* ★ 下拉里"现在这张" = 状态里那张；状态里那张不在这张表里（还没拉到 / 旧配方带过来的
     不认得的名字）就落到配套纸。**只用于显示**，不回写状态 —— 回写会造成 set 循环。
     ⚠ 这条同样不许写死纸名。 */
  const curPaper =
    papers.length && papers.some((p) => p.name === grade.paper)
      ? String(grade.paper)
      : (papers.find((p) => p.isDefault) || papers[0])?.name ?? '';

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
              /* ★ 09-15 SV：**不要悬浮文字** —— 这里原来把一个悬停提示（Tooltip）套在
                 图标按钮外面，而按钮底下本来就写着卷名和那行长描述 ⇒ 悬停提示纯属重复，
                 还老在鼠标划过时蹦出来挡视线。卷的名字与说明现在**只在下面那两行**里出现。
                 ⚠ 别在注释里写出那个组件的 JSX 写法 —— 自检判断"还有没有它在用"是搜源码的，
                   自己的注释会把检查骗过去（本项目踩过，见技能 §10「剥掉注释再查源码」）。 */
              <button
                key={s.name}
                data-stock={s.name}
                data-stock-on={on ? '1' : '0'}
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
            );
          })}
        </div>
        <Text size="1" style={{ color: 'var(--text-dim)', marginTop: 6 }}>
          {stocks.find((s) => s.name === curStock)?.desc || ''}
        </Text>

        {/* ★★ 「渲染」按钮（SV 09-15：放到胶片卷下面）。
            改卷 / 改相纸 / 改基准 / 换图**不会自动出图** —— 按下它才出，切进调色台时也会出一次。
            ⚠ 09-15 晚 SV 选「A」之后**多了一条**：**拖右栏滑杆会实时出图**
              （带 60ms 防抖 + "忙时补发最新一发"，见 `Viewer.tsx`）。 */}
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
          改完卷 / 相纸 / 基准按这里出图；滑杆是拖到哪出到哪
        </Text>

        {/* ★★ 「导出成片」（09-15 SV 选「A」第 ② 项）：把**渲染结果**写成真照片文件。
            三件跟"屏幕上那张"不一样的事写在按钮下面，别让人以为"导出=把屏幕存下来"：
              · 尺寸走**原图全尺寸**（09-15 SV 选「A」定的默认），不是预览那 700 ——
                颗粒是物理量、**越大颗粒越明显**，这一点要如实说；
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
              /* ★★ 初值必须用引擎给的 `dv`（= 引擎此刻实际在用的值），**不许退回区间中点**。
                 过去这里写的是 `(d.lo + d.hi) / 2`，而引擎用的是 config 出厂值
                 ⇒ 13 根滑杆里有 12 根显示的数字和实际生效的对不上
                 （「整张浓淡」显示 0.50 / 实际 0.00，彩度差一档半；「颗粒」显示 0.050 / 实际 0.024）。
                 画面本身没错（没拧过的键不参与覆盖），**错的是那行字**。
                 最后那个中点只作为"引擎漏给 dv"的兜底，正常永远走不到。 */
              /* ★★ 初值必须用引擎给的 `dv`（= 引擎此刻实际在用的值），**不许退回区间中点**。
                 过去这里写的是 `(d.lo + d.hi) / 2`，而引擎用的是 config 出厂值
                 ⇒ 13 根滑杆里有 12 根显示的数字和实际生效的对不上
                 （「整张浓淡」显示 0.50 / 实际 0.00，彩度差一档半；「颗粒」显示 0.050 / 实际 0.024）。
                 画面本身没错（没拧过的键不参与覆盖），**错的是那行字**。
                 最后那个中点只作为"引擎漏给 dv"的兜底，正常永远走不到。 */
              const v = params[d.k] ?? d.dv ?? (d.lo + d.hi) / 2;
              /* ★★ 09-15（B3）：这一行画什么**由引擎的 `kind` 决定，前端不许自己猜** ——
                 ① `num`（默认）= 滑杆；② `bool` = 勾选框（整层开关）；
                 ③ `enum` = 下拉（柔光型号那一类）。 */
              const kind = d.kind || 'num';
              const isEnum = kind === 'enum';
              const isBool = kind === 'bool';
              /* 只有"本来就是数"的那几根才有滑杆/数字；开关和下拉不算数 */
              const num = typeof v === 'number' ? v : 0;
              /* 小数位跟着 step 走 —— 原来一律 toFixed(2)，「颗粒」的实际值 0.024 会显示成
                 "0.02"（step 是 0.002，白丢了精度）。 */
              const dp = d.step >= 1 ? 0 : d.step >= 0.1 ? 1 : d.step >= 0.01 ? 2 : 3;
              /* ★★ 「整层开关」（09-15 B3）：挂在名字**前面**的那个小勾。
                  它管的是这一根/这一道**要不要跑**（`d.gate` = config 里那个 `*_ENABLE`）。
                  · 单独成行的开关（如「降噪」）：勾就是这根参数自己的值（`d.k` 本身）；
                  · 挂在滑杆上的（颗粒 / 黑柔 / 红橙晕圈 / 脸的层次）：勾的是它管的那一层。
                  ⚠ 关掉一层 **≠** 把强度拧到 0：关掉是"这一层不跑"，连淡出/守恒都不做。 */
              const swKey = isBool ? d.k : d.gate;
              const swOn = isBool
                ? !!v
                : d.gate
                  ? params[d.gate] !== undefined ? !!params[d.gate] : !!d.gate_dv
                  : false;
              /* ★ 「这根动过没有」= 参数串里**有没有这个键**（键在 = 有覆盖 = 动过）。
                 用它决定重置按钮亮不亮 —— 一眼看出哪几根动过（30 多根里找"我改了哪几根"很费眼）。
                 ⚠ 带开关的那几根**开关也算**：只关层不拧滑杆也是"动过"，
                   否则关着的时候 ↺ 是灰的、想开回来点不了。 */
              const touched =
                params[d.k] !== undefined || (!!d.gate && params[d.gate] !== undefined);
              return (
                <div key={d.k}>
                  <Flex justify="between" align="center" gap="2">
                    <Flex align="center" gap="1" style={{ minWidth: 0 }}>
                      {/* ★★ 参数名（09-15 SV）：
                          ① **字号**：原来 Radix size="1" = 12px ⇒ 先放大 1.5 倍到 18px，
                             当晚再定「缩到当前的 0.9 左右」⇒ **16px**；
                          ② **不要悬停提示** —— 那段说明挂在悬停上根本看不清（得悬着不动、还老
                             在鼠标划过时蹦出来），改成后面这个「?」点开看。
                          ⚠ 名字和数字原来都吃 size="1"（12px）；那行**不能带 size** ——
                             Radix 的 size="1" 会盖住 inline fontSize（踩过）。 */}
                      <Text style={{ fontSize: 16, lineHeight: 1.35 }}>{d.name}</Text>
                      {/* ★★ 「?」= 详细说明（09-15 SV 选的做法）。内容就是引擎 `PARAMS` 里那段
                          `d`（`svFilm/service.py` 写的，带数字和 ⚠ 提醒），**前端不许自己编文案**。
                          用 Popover 不用 Dialog：贴着这一根弹出来、点别处就关，不打断手感。
                          ⚠ 说明**不许常驻 DOM**（常驻的话右栏文字里到处都是说明，自检也读不准）
                          ⇒ 只有点开那一刻才渲染。 */}
                      <Popover.Root>
                        <Popover.Trigger>
                          <button
                            data-param-help={d.k}
                            aria-label={`${d.name} 的说明`}
                            style={{
                              flex: '0 0 auto',
                              width: 16,
                              height: 16,
                              padding: 0,
                              borderRadius: 999,
                              border: '1px solid var(--line)',
                              background: 'transparent',
                              color: 'var(--text-dim)',
                              fontSize: 11,
                              lineHeight: '14px',
                              cursor: 'pointer',
                            }}
                          >
                            ?
                          </button>
                        </Popover.Trigger>
                        <Popover.Content width="330px" data-param-help-pop={d.k}>
                          <Flex direction="column" gap="2">
                            <Text size="2" weight="bold">
                              {d.name}
                            </Text>
                            <Text
                              size="1"
                              style={{
                                color: 'var(--text-dim)',
                                whiteSpace: 'pre-wrap',
                                lineHeight: 1.7,
                              }}
                            >
                              {d.d || '这一根引擎侧没有写说明'}
                            </Text>
                            {/* 顺带把"这个数怎么读"给出来：范围 / 每格 / 出厂值。
                                ⚠ 出厂值用引擎给的 `dv`，不是前端算的区间中点（老 bug 见 §9）。 */}
                            {/* ⚠ 只有"数字"那几行才有范围 / 每格 / 出厂 —— 整层开关和下拉
                                没有这些（硬画会变成"范围 undefined ~ undefined"）。 */}
                            {kind === 'num' && (
                              <Text size="1" style={{ color: 'var(--text-faint)' }}>
                                范围 {d.lo} ~ {d.hi} · 每格 {d.step} · 出厂{' '}
                                {Number(d.dv ?? (d.lo + d.hi) / 2).toFixed(dp)}
                              </Text>
                            )}
                          </Flex>
                        </Popover.Content>
                      </Popover.Root>
                    </Flex>
                    <Flex align="center" gap="2" style={{ flex: '0 0 auto' }}>
                      {/* 数字只给"本来就是数"的那几根；开关说 开/关，下拉不给数字 */}
                      <Text size="1" style={{ color: 'var(--text-dim)' }}>
                        {isBool ? (v ? '开' : '关') : isEnum ? '' : num.toFixed(dp)}
                      </Text>
                      {/* ★★ 每根滑杆一个重置（09-15 SV）。
                          做法：把这个键**从参数串里删掉** ⇒ 这根回到引擎给的值（`dv`）。
                          ⚠ 必须是"删键"，**不能**写回 `dv` —— 「没碰过的键不进参数串」是一条
                             契约（自检 `[7]` 钉着它：未触碰 = 用引擎出厂值）。写回去等于把
                             出厂值也塞进请求，和引擎的出厂打架。
                          ⚠ 没拧过的这根灰着、点不动：既说明"这根还没动过"，也避免手一滑把
                             别的根改了。 */}
                      <button
                        data-param-reset={d.k}
                        data-param-reset-on={touched ? '1' : '0'}
                        disabled={!touched}
                        onClick={() => {
                          const np = { ...params };
                          delete np[d.k];
                          // 开关也一起复原（不然 ↺ 之后那一层还关着，看着"没回到默认"）
                          if (d.gate) delete np[d.gate];
                          setGrade({ params: np });
                          /* 和「拖滑杆」同一条规矩：松开就出图。不同步出图的话数字回到默认、
                             画面还停在拧过的样子 —— 那正是本项目最烦的"看着对、其实对不上"。 */
                          requestRender();
                        }}
                        aria-label={`${d.name} 回到默认`}
                        style={{
                          flex: '0 0 auto',
                          width: 18,
                          height: 18,
                          padding: 0,
                          borderRadius: 999,
                          border: touched
                            ? '1px solid var(--accent)'
                            : '1px solid var(--line)',
                          background: 'transparent',
                          color: touched ? 'var(--accent)' : 'var(--text-faint)',
                          fontSize: 11,
                          lineHeight: '15px',
                          cursor: touched ? 'pointer' : 'default',
                          opacity: touched ? 1 : 0.45,
                        }}
                      >
                        ↺
                      </button>
                    </Flex>
                  </Flex>
                  {/* ★★ 三种控件在这里分叉（09-15 B3）。都用同一个 `setGrade`，
                      都**不自动出图** —— 沿用"只有两个触发点：右栏「渲染」/ 进调色台"，
                      唯一的例外是滑杆（拖到哪出到哪）。 */}
                  {swKey && (
                    <Flex align="center" gap="1" mt="1">
                      <input
                        type="checkbox"
                        data-param-sw={swKey}
                        data-param-sw-on={swOn ? '1' : '0'}
                        checked={swOn}
                        onChange={(e) =>
                          setGrade({
                            params: { ...params, [swKey]: e.target.checked },
                          })
                        }
                        aria-label={`${d.name} 这一层开不开`}
                        style={{ flex: '0 0 auto', cursor: 'pointer', margin: 0 }}
                      />
                      <Text size="1" style={{ color: 'var(--text-faint)' }}>
                        {isBool ? '这一层要不要跑' : '这一层开不开'}
                      </Text>
                    </Flex>
                  )}
                  {isEnum ? (
                    /* 下拉：选项**照引擎的 `opts` 抄**（前端不许自己编型号名 —— 编错了
                       引擎会当成"名字不认得"静默丢掉，画面一点不变）。
                       ⚠★★ `opts` 的形状是**对象数组** `[{v: 值, t: 人话}, …]`（在引擎
                       `_param_defs()` 里把紧凑元组翻好的）—— 就按 `o.v` / `o.t` 读。
                       09-15 踩过：引擎当时发的是**元组**（过 HTTP 变数组对），这里按对象读
                       ⇒ 四个 `<option>` 的 value 和文字全是 undefined ⇒ **下拉框看着是空的**
                       （而"个数是 4"照样成立 ⇒ 自检绿着）。别再退回元组下标。 */
                    <select
                      data-param-enum={d.k}
                      data-param-enum-on={String(v)}
                      value={String(v)}
                      onChange={(e) =>
                        setGrade({ params: { ...params, [d.k]: e.target.value } })
                      }
                      style={{
                        width: '100%',
                        marginTop: 6,
                        padding: '6px 7px',
                        borderRadius: 'var(--r-sm)',
                        border: '1px solid var(--line)',
                        background: 'var(--bg-hover)',
                        color: 'var(--text)',
                        fontSize: 11.5,
                        cursor: 'pointer',
                      }}
                    >
                      {(d.opts || []).map((o) => (
                        <option
                          key={o.v}
                          value={o.v}
                          style={{ color: '#111', background: '#fff' }}
                        >
                          {o.t}
                        </option>
                      ))}
                    </select>
                  ) : isBool ? null : (
                  <Slider
                    size="1"
                    min={d.lo}
                    max={d.hi}
                    step={d.step}
                    value={[num]}
                    /* ★★ 09-15 SV 选「A」：**拖着滑杆就出图**（"滑动每个参数都能实时预览"）。
                       两条护栏都在 Viewer 那边，这里不许自己加节流：
                         ① 合并 —— 同时只跑一发，跑完发现"参数又变了"就补发**最新那一发**；
                         ② 防抖 —— 60ms 内的连续变化只发起一次。
                       ⚠ 这里要**每一格都发**：真正"松手后停在旧画面"的那个 bug，
                         根因是上游把请求丢了（Viewer 忙着时直接 return），不是发得太多。
                         在这儿省一发 = 把"最后一发"也省掉 ⇒ 松手后画面停在中间某一格。 */
                    onValueChange={([nv]) => {
                      setGrade({ params: { ...params, [d.k]: nv } });
                      requestRender();
                    }}
                  />
                  )}
                </div>
              );
            })}
          </Flex>
        </div>
      ))}

      {/* ---- 相纸（09-15 SV 选「C」）----
          ★★ 只在真卷下出现：中性卷走的是 Lab 引擎，根本没有「负片 + 相纸」这个二元组，
             引擎对中性卷返回的是**空表**（见 `spektra.papers()`），这里自然就不显示。
          ⚠ 这一栏是"人像成色的另一半"：同一卷负片印在不同纸上，是两套不同的脸色。 */}
      {isEngine && papers.length > 0 && (
        <div>
          <Flex justify="between" align="baseline">
            <Text
              size="1"
              weight="bold"
              style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
            >
              相纸
            </Text>
            {/* 「现在用的就是这一卷的配套纸」—— 纯展示的小标记，一眼看出有没有换过纸 */}
            {papers.some((p) => p.isDefault && p.name === curPaper) && (
              <Text size="1" style={{ color: 'var(--text-faint)' }}>
                本卷配套
              </Text>
            )}
          </Flex>
          {/* ★ 用原生 <select>：8 张纸的中文名很长，竖排列表会把右栏撑得没法用。
              `data-paper` / `data-paper-on` / `data-paper-n` 是给布局自检断言用的 ——
              自检读的是**实际在用的那张纸的名字**，不是"下拉在不在"。 */}
          <select
            data-paper="1"
            data-paper-on={curPaper}
            data-paper-n={papers.length}
            value={curPaper}
            onChange={(e) => setGrade({ paper: e.target.value })}
            style={{
              width: '100%',
              marginTop: 6,
              padding: '6px 7px',
              borderRadius: 'var(--r-sm)',
              border: '1px solid var(--line)',
              background: 'var(--bg-hover)',
              color: 'var(--text)',
              fontSize: 11.5,
              cursor: 'pointer',
            }}
          >
            {papers.map((p) => (
              <option
                key={p.name}
                value={p.name}
                style={{ color: '#111', background: '#fff' }}
              >
                {(p.label || p.name) + (p.isDefault ? '（本卷配套）' : '')}
              </option>
            ))}
          </select>
          <Text
            size="1"
            style={{ color: 'var(--text-dim)', marginTop: 4, display: 'block' }}
          >
            {papers.find((p) => p.name === curPaper)?.desc || ''}
          </Text>
        </div>
      )}

      {/* ---- 成色基准（★ 09-15 SV：从上面挪到**这里**）----
          为什么挪：它对画面成色的影响很大（不套基准 / 只加雾 / 退黄+加雾 / 全对齐），
          但它原来竖排四行摆在「卷 / 相纸」下面 ⇒ 把滑杆往下推掉四行的位置，
          而他天天拧的是滑杆。挪到底部 + 改成**一横排紧凑 chip**（占一行、会换行）。
          ⚠ `data-base` / `data-base-on` 一个都不许少：布局自检靠它断言
            「基准**必须有且只有一支**选中」——不去认颜色/边框（那是皮肤，改样式就废了）。
            为什么值得钉死：默认值曾经是前端写死的 `'all'`，而引擎基准表里没有这一支
            ⇒ **四支一支都选不中**，同时引擎静默按"不套基准"出图（画面是错的、还看不出来）。 ---- */}
      <div>
        <Text
          size="1"
          weight="bold"
          style={{ color: 'var(--text-dim)', letterSpacing: 1 }}
        >
          成色基准
        </Text>
        <Flex gap="1" mt="1" wrap="wrap">
          {bases.map((b) => {
            const on = grade.base === b.name;
            return (
              <button
                key={b.name}
                data-base={b.name}
                data-base-on={on ? '1' : '0'}
                onClick={() => setGrade({ base: b.name })}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '4px 8px',
                  borderRadius: 999,
                  border: on ? '1px solid var(--accent)' : '1px solid var(--line)',
                  background: on
                    ? 'rgba(10,132,255,.10)'
                    : 'rgba(255,255,255,.03)',
                  color: on ? 'var(--accent)' : 'var(--text-dim)',
                  cursor: 'pointer',
                  fontSize: 11.5,
                  whiteSpace: 'nowrap',
                }}
              >
                <span
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: 999,
                    border: on ? '3px solid var(--accent)' : '1.5px solid var(--line)',
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

      <Flex gap="2" mt="1">
        {/* ★ 这两个按钮 09-15 之前是**死的**（没有 onClick，点了什么都不发生） */}
        <Button
          size="1"
          variant="ghost"
          onClick={resetGrade}
          title="滑杆回出厂、基准回默认、相纸回本卷配套纸（卷不动）"
        >
          恢复默认
        </Button>
        <Button size="1" variant="ghost" onClick={saveGradeToTheme} title="把这个主题的配方记住，下次进来自动套回">
          存到主题
        </Button>
      </Flex>
    </Flex>
  );
}
