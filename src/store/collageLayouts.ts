/**
 * 拼图台的布局模板（纯数据，store 和组件共用，避免两份格子定义）。
 *
 * 参照 Lightroom「打印模块」/ Fotor / PhotoScape 的做法：**预定义网格**而不是自由拖放 ——
 * 网格能消除决策疲劳，选一个布局立刻出结果；自由画布留给以后需要时再加。
 *
 * 每个格子是归一化矩形 [x, y, w, h]（0~1），与输出像素无关，
 * 预览（长边 1600）和导出（原尺寸）共用同一套比例 ⇒ 所见即所得。
 */
import type { CollageLayout } from '../store/useStore';

export interface LayoutDef {
  id: CollageLayout;
  name: string;
  /** 一句话说明，鼠标悬停时给 */
  hint: string;
  cells: [number, number, number, number][];
  /** 简笔图标用的格子（同 cells，画成小方块） */
  icon: [number, number, number, number][];
}

const C = (x: number, y: number, w: number, h: number): [number, number, number, number] =>
  [x, y, w, h];

export const LAYOUTS: LayoutDef[] = [
  {
    id: 'v2', name: '上下双拼', hint: '默认 · 前后对比最常用',
    cells: [C(0, 0, 1, 0.5), C(0, 0.5, 1, 0.5)],
    icon: [C(0.08, 0.06, 0.84, 0.42), C(0.08, 0.52, 0.84, 0.42)]
  },
  {
    id: 'h2', name: '左右双拼', hint: '两张横图 / 竖图并排',
    cells: [C(0, 0, 0.5, 1), C(0.5, 0, 0.5, 1)],
    icon: [C(0.06, 0.08, 0.42, 0.84), C(0.52, 0.08, 0.42, 0.84)]
  },
  {
    id: 'v3', name: '三连竖', hint: '三张按顺序上下排',
    cells: [C(0, 0, 1, 1 / 3), C(0, 1 / 3, 1, 1 / 3), C(0, 2 / 3, 1, 1 / 3)],
    icon: [C(0.08, 0.04, 0.84, 0.29), C(0.08, 0.355, 0.84, 0.29), C(0.08, 0.67, 0.84, 0.29)]
  },
  {
    id: 'h3', name: '三连横', hint: '三张并排',
    cells: [C(0, 0, 1 / 3, 1), C(1 / 3, 0, 1 / 3, 1), C(2 / 3, 0, 1 / 3, 1)],
    icon: [C(0.04, 0.08, 0.29, 0.84), C(0.355, 0.08, 0.29, 0.84), C(0.67, 0.08, 0.29, 0.84)]
  },
  {
    id: 'grid4', name: '2×2 四宫格', hint: '四张平均',
    cells: [C(0, 0, 0.5, 0.5), C(0.5, 0, 0.5, 0.5), C(0, 0.5, 0.5, 0.5), C(0.5, 0.5, 0.5, 0.5)],
    icon: [C(0.06, 0.06, 0.42, 0.42), C(0.52, 0.06, 0.42, 0.42),
           C(0.06, 0.52, 0.42, 0.42), C(0.52, 0.52, 0.42, 0.42)]
  },
  {
    id: 'bigTop', name: '上大下两小', hint: '一张主图 + 两张细节',
    cells: [C(0, 0, 1, 0.62), C(0, 0.62, 0.5, 0.38), C(0.5, 0.62, 0.5, 0.38)],
    icon: [C(0.06, 0.05, 0.88, 0.54), C(0.06, 0.62, 0.42, 0.33), C(0.52, 0.62, 0.42, 0.33)]
  },
  {
    id: 'bigLeft', name: '左大右两小', hint: '竖构图主图 + 两张',
    cells: [C(0, 0, 0.62, 1), C(0.62, 0, 0.38, 0.5), C(0.62, 0.5, 0.38, 0.5)],
    icon: [C(0.05, 0.06, 0.54, 0.88), C(0.62, 0.06, 0.33, 0.42), C(0.62, 0.52, 0.33, 0.42)]
  },
  {
    id: 'grid9', name: '九宫格', hint: '九张平均',
    cells: Array.from({ length: 9 }, (_, i) =>
      C((i % 3) / 3, Math.floor(i / 3) / 3, 1 / 3, 1 / 3)),
    icon: Array.from({ length: 9 }, (_, i) =>
      C(0.05 + (i % 3) * 0.31, 0.05 + Math.floor(i / 3) * 0.31, 0.27, 0.27))
  },
  {
    id: 'compare', name: '滑动对比', hint: '两张叠一起，拖动分割线看前后（Lightroom 式）',
    cells: [C(0, 0, 1, 1), C(0, 0, 1, 1)],   // 两张都占满，靠 split 裁
    icon: [C(0.06, 0.08, 0.42, 0.84), C(0.52, 0.08, 0.42, 0.84)]
  }
];

export function getLayout(id: CollageLayout): LayoutDef {
  return LAYOUTS.find((l) => l.id === id) || LAYOUTS[0];
}

export function cellCount(id: CollageLayout): number {
  return getLayout(id).cells.length;
}
