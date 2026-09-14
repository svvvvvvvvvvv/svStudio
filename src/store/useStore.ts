import { create } from 'zustand';
import { API, Photo, Session, Stock, Base, ParamDef, GradeState } from '../api';

/**
 * ★★ 全局状态（09-15 React 重写的核心收益）
 *
 * 老代码最大的痛点：**一个星级要三处手写同步**
 *   `updateDockBadge()` + `renderStars()` + `renderDock()`
 *   —— 漏一处就"界面和实际对不上"。这类 bug 在 React + 单一状态源下**从根上不存在**：
 *   界面是状态的函数，改一处状态，所有用到它的地方自动更新。
 *
 * 这里按"变化频率"分片（避免一次 setState 把整棵树重渲染）：
 *   慢的（切主题/切模式）  → 放 store
 *   快的（鼠标位置、hover）→ **不要放这里**，用 useRef（见 components/Dock 的注释）
 */

export type Mode = 'pick' | 'grade';
/** 选片台筛选：all=全部 / unrated=未评 / n=几星 */
export type Filter = 'all' | 'unrated' | '1' | '2' | '3' | '4' | '5';

interface AppState {
  /* ---- 配置与库 ---- */
  libRoot: string;
  ready: boolean;

  /* ---- 会话与照片 ---- */
  sessions: Session[];
  sessionPath: string;
  sessionName: string;
  photos: Photo[];
  /** 当前看的下标（在 photos 里的，不是筛选后的） */
  cur: number;

  /* ---- 星级（★ 单一数据源：以前要三处同步） ---- */
  ratings: Record<string, number>;

  /* ---- 界面 ---- */
  mode: Mode;
  filter: Filter;
  /** 悬浮预览开关（SV 09-14 要求默认关） */
  hoverEnabled: boolean;
  busy: boolean;
  busyText: string;
  toast: string;

  /* ---- 调色台 ---- */
  stocks: Stock[];
  bases: Base[];
  paramDefs: ParamDef[];
  engineOk: boolean;
  grade: GradeState;

  /* ---- actions ---- */
  setReady: (v: boolean) => void;
  setLibRoot: (v: string) => void;
  loadSessions: () => Promise<void>;
  enterSession: (name: string, opts?: { silent?: boolean }) => Promise<void>;
  goHome: () => void;
  setCur: (i: number) => void;
  rate: (v: number) => void;
  setFilter: (f: Filter) => void;
  setMode: (m: Mode) => void;
  setHover: (v: boolean) => void;
  setBusy: (v: boolean, text?: string) => void;
  showToast: (msg: string) => void;
  loadEngine: () => Promise<void>;
  setGrade: (patch: Partial<GradeState>) => void;
}

/** 星级键：老代码 `keyForExif` 是 `sessionName + '||' + photo.name`，保持一致 */
export const ratingKey = (sessionName: string, photoName: string) =>
  sessionName + '||' + photoName;

/** 保存「上次状态」到配置（主题/照片/台）；失败静默 */
function saveLast(patch: { session?: string; cur?: number; mode?: string }) {
  API.setConfig({ last: patch }).catch(() => {});
}

export const useStore = create<AppState>((set, get) => ({
  libRoot: '',
  ready: false,

  sessions: [],
  sessionPath: '',
  sessionName: '',
  photos: [],
  cur: 0,

  ratings: {},

  mode: 'pick',
  filter: 'all',
  hoverEnabled: false,
  busy: false,
  busyText: '',
  toast: '',

  stocks: [],
  bases: [],
  paramDefs: [],
  engineOk: false,
  grade: { stock: 'portra400', base: 'all', params: {} },

  setReady: (v) => set({ ready: v }),
  setLibRoot: (v) => set({ libRoot: v }),

  loadSessions: async () => {
    const cfg = await API.getConfig();
    const root = cfg?.libRoot || '';
    set({ libRoot: root, ratings: cfg?.ratings || {} });
    let list: Session[] = [];
    if (root) {
      list = (await API.scanSessions(root)) || [];
      set({ sessions: list });
    }
    set({ ready: true });

    /* ★★ 恢复上次状态（SV 09-15：进来直接就是台，别停在主题列表）：
       上次的主题 + 选到哪张 + 在哪个台 */
    const last = cfg?.last;
    if (last?.session && list.some((s) => s.name === last.session)) {
      await get().enterSession(last.session, { silent: true });
      set({ mode: last.mode === 'grade' ? 'grade' : 'pick', cur: last.cur || 0 });
    }
  },

  enterSession: async (name, opts) => {
    const root = get().libRoot;
    if (!root) return;
    const sessionPath = root + '\\' + name;
    set({ sessionPath, sessionName: name, busy: true, busyText: '读取照片…' });
    try {
      const photos = await API.listPhotos(sessionPath);
      set({ photos: photos || [], cur: 0 });
    } finally {
      set({ busy: false });
    }
    if (!opts?.silent) {
      // ★ 每次进主题都记下来，下次启动直接回到这
      saveLast({ session: name });
    }
  },

  goHome: () => set({ sessionPath: '', sessionName: '', photos: [], cur: 0 }),

  setCur: (i) => {
    set({ cur: i });
    saveLast({ cur: i });
  },

  /**
   * ★ 打星：**只改这一处状态**。
   *   老代码这里要手动调 updateDockBadge + renderStars + renderDock 三个函数，
   *   漏一个就出现"底栏星标没更新"这类 bug。现在界面自动跟着 ratings 走。
   */
  rate: (v) => {
    const { sessionName, photos, cur, ratings } = get();
    const p = photos[cur];
    if (!p) return;
    const k = ratingKey(sessionName, p.name);
    const next = { ...ratings };
    if (v <= 0) delete next[k];
    else next[k] = v;
    set({ ratings: next });
    // 落盘（异步，不阻塞 UI）
    API.saveRatings(next).catch(() => {});
  },

  setFilter: (f) => set({ filter: f }),
  setMode: (m) => {
    set({ mode: m });
    saveLast({ mode: m });
  },
  setHover: (v) => set({ hoverEnabled: v }),
  setBusy: (v, text) => set({ busy: v, busyText: text || '' }),

  showToast: (msg) => {
    set({ toast: msg });
    setTimeout(() => {
      if (get().toast === msg) set({ toast: '' });
    }, 2000);
  },

  /** 拉引擎元数据（卷 / 基准 / 参数定义）+ 探活。服务没起时 ok=false，不白屏。 */
  loadEngine: async () => {
    try {
      const h = await API.engineHealth();
      if (!h || h.ok === false) {
        set({ engineOk: false });
        return;
      }
      const [s, b, p] = await Promise.all([
        API.engineStocks(),
        API.engineBases(),
        API.engineParams(),
      ]);
      set({
        engineOk: true,
        stocks: s?.stocks || [],
        bases: b?.bases || [],
        paramDefs: p?.params || [],
      });
    } catch {
      set({ engineOk: false });
    }
  },

  setGrade: (patch) => set({ grade: { ...get().grade, ...patch } }),
}));

/**
 * 筛选后的片单（派生，不进 store —— 避免和 photos 不同步）。
 * ⚠ 调色台语义：只收 ★≥1（SV 09-14 定死）。
 */
export function visiblePhotos(
  photos: Photo[],
  sessionName: string,
  ratings: Record<string, number>,
  filter: Filter,
  forGrade = false
): number[] {
  const out: number[] = [];
  for (let i = 0; i < photos.length; i++) {
    const p = photos[i];
    const v = ratings[ratingKey(sessionName, p.name)] || 0;
    if (forGrade) {
      if (v >= 1) out.push(i);
      continue;
    }
    if (filter === 'all') out.push(i);
    else if (filter === 'unrated') {
      if (v < 1) out.push(i);
    } else if (v === Number(filter)) out.push(i);
  }
  return out;
}
