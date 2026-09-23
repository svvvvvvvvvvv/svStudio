/**
 * ★★ 苹果风目录（09-15 重写界面时一并落地）
 *
 * ⚠ 这些不是"我觉得好看"，是 **Apple HIG（人机界面指南）里的实际做法**：
 *  ① **底色不能纯黑** —— macOS 深色模式的窗口底是深灰（#1e1e1e 一带），
 *     纯黑（#000）会让窗口失去"层次"，苹果系统里几乎不用纯黑。
 *  ② **少用分隔线** —— 苹果靠"背景色差 + 留白"分区，
 *     老界面那种"每个区块画一条 border"是典型非苹果做法。
 *  ③ **圆角连续** —— 苹果的圆角是连续的（squircle），且一套 App 内统一档位。
 *  ④ **强调色** —— macOS 深色模式的系统蓝是 #0A84FF（不是亮的 #007AFF，那个是浅色模式用的）。
 *  ⑤ **字体** —— SF Pro / 苹方（PingFang SC），Win 上苹方常没有，回退微软雅黑。
 */

/** 深色模式灰阶（从最底层到文字，12 档，参考 Radix gray + 苹果实际取色） */
export const GRAY = {
  1: '#1c1c1e', // 最底：窗口背景（苹果深色窗口底）
  2: '#242426', // 卡片 / 侧栏
  3: '#2c2c2e', // 卡片悬浮
  4: '#3a3a3c', // 分隔 / 边框（很淡，替代老界面的实线）
  5: '#48484a', // 输入框边框
  6: '#636366', // 次级文字
  7: '#8e8e93', // 三级文字
  8: '#aeaeb2',
  9: '#d1d1d6', // 主文字
  10: '#e5e5ea',
  11: '#f2f2f7',
  12: '#ffffff',
} as const;

/** 强调色：macOS 深色模式系统蓝 */
export const ACCENT = '#0A84FF';

/** 语义色（苹果系统色，深色模式取值） */
export const SEMANTIC = {
  green: '#30D158',
  red: '#FF453A',
  orange: '#FF9F0A',
  yellow: '#FFD60A',
} as const;

/** 圆角档位（苹果内统一，不随便给值） */
export const RADIUS = {
  sm: 6,
  md: 10,
  lg: 14,
  pill: 999,
} as const;

/** 留白节奏（8pt 栅格，苹果的标准） */
export const SPACE = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

/** 字体栈：SF Pro → 苹方 → 微软雅黑 */
export const FONT =
  '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", ' +
  '"Microsoft YaHei", system-ui, sans-serif';

/**
 * 把上面这套写进 CSS 变量（Radix Themes 用 <Theme> 组件，
 * 但**我们自定义的部分**（图墙、分屏、悬浮预览）要直接吃这些值）
 */
export function applyThemeVars(root: HTMLElement = document.documentElement) {
  const set = (k: string, v: string | number) => root.style.setProperty(k, String(v));
  for (const [k, v] of Object.entries(GRAY)) set(`--gray-${k}`, v);
  set('--accent', ACCENT);
  for (const [k, v] of Object.entries(SEMANTIC)) set(`--c-${k}`, v);
  for (const [k, v] of Object.entries(RADIUS)) set(`--r-${k}`, `${v}px`);
  for (const [k, v] of Object.entries(SPACE)) set(`--s-${k}`, `${v}px`);
  set('--font', FONT);
  /* 语义别名（业务代码用这些，别直接写 --gray-1） */
  set('--bg', GRAY[1]);
  set('--bg-panel', GRAY[2]);
  set('--bg-hover', GRAY[3]);
  set('--line', GRAY[4]);
  set('--text', GRAY[9]);
  set('--text-dim', GRAY[6]);
  set('--text-faint', GRAY[7]);
}
