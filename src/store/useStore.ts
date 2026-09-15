import { create } from 'zustand';
import {
  API,
  Photo,
  Session,
  Stock,
  Base,
  ParamDef,
  GradeState,
  ImportCard,
  ImportPlan,
} from '../api';

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

/** 导入表单（源卡 / 主题 / 地点 / 日期）。日期留空 = 从 EXIF 推断 */
export interface ImportForm {
  src: string;
  topic: string;
  place: string;
  date: string;
}

/** 导入对话框最多留多少行日志（一次几百张也就几十行，留够看现场就行） */
const IMPORT_LOG_MAX = 400;

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
  engineMsg: string;
  grade: GradeState;
  /** 有没有一发渲染正在跑（分屏置位；按钮和状态栏据此显示「出图中…」） */
  renderBusy: boolean;
  /** 「请渲染」的次数。★ 09-15 SV 定的策略：**任何操作都不自动出图**，
   *  只有 ① 点「渲染」按钮 ② 切进/进入调色台 会让它 +1；分屏盯着它出图。
   *  （用「次数」而不是 boolean —— 连点两次也能各触发一次，不会被合并掉。） */
  renderTick: number;

  /* ---- 照片导入（SD 卡 / U 盘 → 照片库） ---- */
  importOpen: boolean;
  /** 找到的卡（照片最多的排前面） */
  importCards: ImportCard[];
  /** 导入脚本路径（**空串 = 还没配** ⇒ 对话框先引导"选一次"，之后写进 config） */
  importScript: string;
  importForm: ImportForm;
  /** 预演出来的计划 / 导入完的结果（没跑过是 null） */
  importPlan: ImportPlan | null;
  /** 进度与输出的尾巴（界面只显示最后几行） */
  importLog: string[];
  /** 有一发**真导入**在跑（按钮禁用 + 进度区常显 + 不许关对话框） */
  importRunning: boolean;
  /** 预演本身的忙碌（和 importRunning 分开：预演也转圈，但不算"在拷"） */
  importBusy: boolean;

  /* ---- actions ---- */
  setReady: (v: boolean) => void;
  setLibRoot: (v: string) => void;
  loadSessions: () => Promise<void>;
  /** 只重扫主题列表，**不碰**"恢复上次状态"（导入完要用它，见 runImport） */
  refreshSessions: () => Promise<void>;
  /** 换照片库（左栏「换图库」）—— 存进配置 + 重扫 + 回首页 */
  changeLibRoot: (root: string) => Promise<void>;
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
  ensureEngine: () => Promise<boolean>;
  setGrade: (patch: Partial<GradeState>) => void;
  /** 右栏「恢复默认」：滑杆清空（回引擎出厂）+ 基准回引擎默认；**不动卷**；不自动出图 */
  resetGrade: () => void;
  /** 右栏「存到主题」：把当前卷/基准/滑杆值写进 `config.grades[主题名]`（进主题时自动套回） */
  saveGradeToTheme: () => Promise<void>;
  setRenderBusy: (v: boolean) => void;
  /** 请分屏出一次图（右栏「渲染」按钮 / 切进调色台 都调它） */
  requestRender: () => void;
  /* ---- 导入相关 ---- */
  openImport: () => Promise<void>;
  closeImport: () => void;
  /** 重新找卡 + 问"脚本配了没" */
  detectImport: () => Promise<void>;
  setImportForm: (patch: Partial<ImportForm>) => void;
  /** 选导入脚本（只在还没配过时需要；选完写进 config，以后不用再选） */
  pickImportScript: () => Promise<void>;
  /** 预演（脚本的 --dry-run）：只看不复制 */
  previewImport: () => Promise<boolean>;
  /** 真跑。跑完刷新主题列表并**直接进新主题的选片台** */
  runImport: () => Promise<void>;
  /** 主进程推来的一行进度 */
  pushImportLog: (line: string) => void;
}

/** 星级键：老代码 `keyForExif` 是 `sessionName + '||' + photo.name`，保持一致 */
export const ratingKey = (sessionName: string, photoName: string) =>
  sessionName + '||' + photoName;

/** 保存「上次状态」到配置（主题/照片/台）；失败静默。
 *  ⚠ 必须用**平铺键**（lastSession/lastCur/lastMode），
 *    不能存 last:{...} 整个对象 —— main.js 的 set-config 是 Object.assign(cfg, patch)，
 *    存 {last:{cur}} 会把 last 整个换掉、丢掉 session（09-15「进来不是台」的根因）。 */
function saveLast(patch: { session?: string; cur?: number; mode?: string }) {
  const flat: Record<string, unknown> = {};
  if (patch.session !== undefined) flat.lastSession = patch.session;
  if (patch.cur !== undefined) flat.lastCur = patch.cur;
  if (patch.mode !== undefined) flat.lastMode = patch.mode;
  API.setConfig(flat).catch(() => {});
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
  hoverEnabled: true,
  busy: false,
  busyText: '',
  toast: '',

  stocks: [],
  bases: [],
  paramDefs: [],
  engineOk: false,
  engineMsg: '',
  /* ★★ 基准初值**故意留空串**：真正的默认由引擎给（`/bases` 里带 `isDefault` 的那条，
     见下面 loadEngine）。过去这里写死 'all'，而引擎的基准表（config.BASE_TABLE）里
     根本没有 'all' ⇒ `stocks.resolve_base` **静默**回落成 `BASE_NONE`（"不套基准"）
     ⇒ 默认出图等于"什么都没套"，界面上四支**一支都选不中**（还看不出哪里不对）。
     ⚠ 规矩同滑杆那条：**前端不许自己发明初值**。 */
  grade: { stock: 'portra400', base: '', params: {} },
  renderBusy: false,
  renderTick: 0,

  importOpen: false,
  importCards: [],
  importScript: '',
  importForm: { src: '', topic: '', place: '', date: '' },
  importPlan: null,
  importLog: [],
  importRunning: false,
  importBusy: false,

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
       上次的主题 + 选到哪张 + 在哪个台（平铺键，见 saveLast 注释） */
    const lastSession = cfg?.lastSession;
    if (lastSession && list.some((s) => s.name === lastSession)) {
      await get().enterSession(lastSession, { silent: true });
      /* ⚠★ 这里夹范围是**兜底**，不是主修。
         真正的根因是「`lastSession` 和 `lastCur` 会不同步」：过去 `enterSession` 只写
         `lastSession`、不写 `lastCur` ⇒ 落盘的 (主题, 下标) 是**两次不同操作**拼出来的。
         踩过的场景（真能走到）：在长主题里翻到第 30 张 → 切到一个只有 12 张的主题
         （`enterSession` 把 cur 归 0，但落盘的 lastCur 还是 30）→ 关掉再打开
         ⇒ 恢复成"主题 × 第 30 张" = 不存在 ⇒ 中间显示「没有照片」，
         在用户眼里就是"打开工作台白屏了"，而且看不出为什么。
         现在 `enterSession` 里两个键一起写（见那儿），夹范围留着挡"照片本身变少了"
         （删了片 / 换了盘 / 手动挪了文件）这一类。 */
      const n = get().photos.length;
      const want = Number(cfg?.lastCur) || 0;
      set({ cur: n > 0 ? Math.min(Math.max(0, want), n - 1) : 0 });
      set({ mode: cfg?.lastMode === 'grade' ? 'grade' : 'pick' });
    }
  },

  /** 只重扫主题列表（导入完要用它 —— 不能直接调 loadSessions：
   *  那个会顺带"恢复上次主题"，把刚导进来的新主题又换掉） */
  refreshSessions: async () => {
    const root = get().libRoot;
    if (!root) return;
    const list = (await API.scanSessions(root)) || [];
    set({ sessions: list });
  },

  /** 换照片库：存进配置 + 清空当前主题 + 重扫 */
  changeLibRoot: async (root) => {
    if (!root) return;
    await API.setConfig({ libRoot: root });
    set({ libRoot: root, sessionPath: '', sessionName: '', photos: [], cur: 0, sessions: [] });
    await get().refreshSessions();
    get().showToast('已切换照片库');
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
      /* ★ 每次进主题都记下来，下次启动直接回到这。
         ⚠★ `cur` 必须**一起**写：两个键是"一次操作的结果"，分开写就会不同步
           （过去只写 session，于是"换了主题但 lastCur 还是老主题的下标"，
            重启后恢复成不存在的第 N 张 ⇒ 打开就白屏）。 */
      saveLast({ session: name, cur: 0 });
    }
    /* ★ 按主题**套回配方**（「存到主题」存下的那份，`config.grades[主题名]`）。
       没存过就什么都不动 —— 保持当前状态。
       ⚠ 不加这一步「存到主题」就是**只写不读**（存了个寂寞），正是本项目最忌的
         "看着对、其实对不上"；也所以它没有单独一个按钮的必要 —— 存了就得用上。
       ⚠ 只改状态、**不出图**（沿用"只有两个触发点"的规矩）。 */
    const list = get().bases;
    const dfltName = (list.find((x) => x.isDefault) || list[0])?.name ?? '';
    try {
      const g = await API.getGrade(name);
      if (g && typeof g === 'object') {
        /* ★★ 存过的配方**也要校验再套**，不能原样信。
           `base` 是**引擎基准表里的名字**，而这份配置是"人能手改、旧版本也写过"的东西
           （旧版前端就写死过 `'all'`，那个名字引擎根本不认）。
           引擎对不认得的名字是**静默**回落成「不套基准」的 —— 见 `stocks.resolve_base`：
           `key = str(name or 'BASE_NONE').strip().upper()`，既不在表里就取 `'BASE_NONE'`。
           ⇒ 不校验的话，一进这个主题就是：界面上四支基准**一支都不亮**，
             出图悄悄变成"什么都没套"，而且**看不出哪里不对**。
           —— 跟修「默认值」那条是同一个坑，只是入口从"初值"换成了"存过的旧值"。 */
        const b = String((g as { base?: unknown }).base ?? '');
        const known = !!b && list.some((x) => x.name === b);
        set({ grade: { ...get().grade, ...g, base: known ? b : dfltName } });
        if (!known && list.length) {
          get().showToast(
            `「${name}」存的基准引擎不认${b ? '（' + b + '）' : ''}，已回默认`
          );
        }
      } else {
        /* 没存过 ⇒ **回出厂**（滑杆清空 + 基准回引擎默认）。
           为什么不"保持上一个主题的值"：那样主题之间会**互相串味**
           （在 A 里拧过的滑杆跟着你进 B），正是本项目最忌的那类"看着对、其实对不上"。
           代价照实说：在 A 里**没存**的临时改动，切走一趟回来就没了
           —— 这恰恰是「存到主题」这个按钮存在的意义。 */
        set({ grade: { stock: get().grade.stock, base: dfltName, params: {} } });
      }
    } catch {
      /* 读不到就当没存过，不吵 */
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
        set({ engineOk: false, engineMsg: '引擎未启动' });
        return;
      }
      const [s, b, p] = await Promise.all([
        API.engineStocks(),
        API.engineBases(),
        API.engineParams(),
      ]);
      // ★ main.js 给的键是 `items`（不是 `stocks`/`bases`/`params`）—— 09-15 名字对不上，
      //   三个列表永远是空的，卷/基准/滑杆全不显示。
      const baseList: Base[] = b?.items || [];
      set({
        engineOk: true,
        engineMsg: '',
        stocks: s?.items || [],
        bases: baseList,
        paramDefs: p?.items || [],
      });
      /* ★ 基准成色的初值**由引擎给**（同滑杆的 `dv` 规矩）：
         当前值不在引擎列表里（首次 = 空串；或引擎改了基准表）⇒ 取引擎标了
         `isDefault` 的那条；引擎万一没标，退到第一支。
         ⚠ **这里不许出现任何写死的基准名** —— 写死就会在引擎改配置后静默错位。 */
      const curBase = get().grade.base;
      if (baseList.length && !baseList.some((x) => x.name === curBase)) {
        const dflt = baseList.find((x) => x.isDefault) || baseList[0];
        set({ grade: { ...get().grade, base: dflt.name } });
      }
    } catch {
      set({ engineOk: false, engineMsg: '引擎未启动' });
    }
  },

  /** ★ 引擎没起就自己拉起来（老版：点渲染时自动 spawn），拉完再探活 */
  ensureEngine: async () => {
    if (get().engineOk) return true;
    try {
      const r = await API.engineStart();
      if (r && r.ok === false) {
        set({ engineMsg: r.error || '引擎启动失败' });
        return false;
      }
      for (let i = 0; i < 30; i++) {
        await new Promise((res) => setTimeout(res, 1000));
        const h = await API.engineHealth();
        if (h && h.ok !== false) {
          await get().loadEngine();
          return true;
        }
      }
      set({ engineMsg: '引擎启动超时' });
      return false;
    } catch (e) {
      set({ engineMsg: String(e) });
      return false;
    }
  },

  setGrade: (patch) => set({ grade: { ...get().grade, ...patch } }),

  /* ★ 右栏「恢复默认」（09-15 接上 —— 之前这个按钮**没有 onClick**，点了什么都不发生）：
     只清「调出来的东西」= 23 根滑杆全清（引擎自动回到它自己 `config` 里的出厂值）+
     基准回引擎默认那支。
     ⚠ **不动卷**：卷（portra400 / cinestill800t…）是"这张要弄成什么"，不是调出来的，
     被「恢复默认」顺手抹掉会很意外。
     ⚠ 也不自动出图 —— 沿用 SV 定的"只有两个触发点"（右栏「渲染」/ 切进调色台）。 */
  resetGrade: () => {
    const list = get().bases;
    const dflt = list.find((x) => x.isDefault) || list[0];
    set({ grade: { ...get().grade, base: dflt?.name ?? '', params: {} } });
    get().showToast('滑杆已回出厂（卷没动）—— 点「渲染」看效果');
  },

  /* ★ 右栏「存到主题」（09-15 接上）：一个主题一份配方，写进 `config.grades[主题名]`。
     ⚠ 主进程的 `get-grade` / `set-grade` 早就写好了，是前端一直没调
     —— 所以这不是"缺功能"，是"接了半截"。 */
  saveGradeToTheme: async () => {
    const name = get().sessionName;
    if (!name) {
      get().showToast('还没进主题，没地方存');
      return;
    }
    try {
      await API.setGrade(name, get().grade);
      get().showToast(`配方已存到「${name}」`);
    } catch (e) {
      get().showToast('存失败：' + String(e));
    }
  },

  setRenderBusy: (v) => set({ renderBusy: v }),
  requestRender: () => set((s) => ({ renderTick: s.renderTick + 1 })),

  /* ================= 照片导入 =================
     ★★ 复制这活**不在这里、也不在 main.js 里重写** —— 一路调现成的导入脚本
        （`photo-import` 的 `import_photos.py`）：只复制不动源卡 / 目标盘自动排除系统盘 /
        断点续传 / 拷完按「文件数 + 总字节」校验。这里只管表单、进度和"跑完去哪"。 */

  openImport: async () => {
    set({
      importOpen: true,
      importPlan: null,
      importLog: [],
      importForm: { src: '', topic: '', place: '', date: '' },
    });
    await get().detectImport();
  },

  closeImport: () => {
    /* ★ 正在拷就不许关：进度得看得见（看不到进度的"正在复制"= 用户眼里死机了，
       会去强杀程序 —— 而这正是最不该中断的一步）。 */
    if (get().importRunning) return;
    set({ importOpen: false });
  },

  detectImport: async () => {
    set({ importBusy: true });
    try {
      const r = await API.importDetect();
      const cards = r?.cards || [];
      /* ★ 默认选中"照片最多的那张卡" —— 导入脚本自己也是这么挑的
         （`find_source()`：所有可移动盘的 DCIM 子目录里取张数最多的那个）。
         两边规则一致，才不会出现"界面显示 A、实际拷的是 B"。 */
      const cur = get().importForm.src;
      set({
        importCards: cards,
        importScript: r?.script || '',
        importForm: { ...get().importForm, src: cur || cards[0]?.path || '' },
      });
    } catch (e) {
      get().showToast('找卡失败：' + String(e));
    } finally {
      set({ importBusy: false });
    }
  },

  setImportForm: (patch) => set({ importForm: { ...get().importForm, ...patch } }),

  pickImportScript: async () => {
    const p = await API.pickFile({
      title: '选择导入脚本（import_photos.py）',
      filters: [{ name: 'Python', extensions: ['py'] }],
    });
    if (!p) return;
    await API.setConfig({ importScript: p });
    set({ importScript: p });
    get().showToast('导入脚本已记住，以后不用再选');
  },

  /** 预演。★ 这一档的存在意义：**先看见"要拷多少 / 拷到哪"，再决定要不要动手** ——
   *  导入是真会写盘的（几百张、十几分钟），不能点一下就直接开跑。 */
  previewImport: async () => {
    const f = get().importForm;
    if (!f.src) {
      get().showToast('先选一张卡（或手动指定源目录）');
      return false;
    }
    if (!f.topic || !f.place) {
      get().showToast('主题和地点都要填 —— 它们组成文件夹名');
      return false;
    }
    set({ importBusy: true, importLog: [], importPlan: null });
    try {
      /* ⚠ 目标库**不从前端传**：让 main.js 从配置里取（唯一出处）。
         前端再传一份的话，两处一旦不一致，片就导进另一个库、工作台扫不到。 */
      const r = await API.importPreview({ src: f.src, topic: f.topic, place: f.place, date: f.date });
      set({ importPlan: r?.plan || null });
      if (!r?.ok) get().showToast('预演失败：' + (r?.error || '看下面的输出'));
      return !!r?.ok;
    } catch (e) {
      get().showToast('预演失败：' + String(e));
      return false;
    } finally {
      set({ importBusy: false });
    }
  },

  runImport: async () => {
    const f = get().importForm;
    if (!get().importPlan) {
      get().showToast('先点「只看不复制」看一眼计划');
      return;
    }
    set({ importRunning: true, importLog: [] });
    try {
      const r = await API.importRun({ src: f.src, topic: f.topic, place: f.place, date: f.date });
      const plan = r?.plan || null;
      set({ importPlan: plan });
      if (!r?.ok) {
        get().showToast('导入失败：' + (r?.error || '看下面的输出'));
        return;
      }
      /* ★ 跑完直接进新主题的选片台（沿用已定的规矩：「导入完直接开，不要再问」）。
         ⚠ 必须用 `refreshSessions()` 而不是 `loadSessions()` —— 后者会顺带
           "恢复到上次的主题"，把刚导进来的这个又换掉。 */
      await get().refreshSessions();
      const folder = plan?.folder || '';
      set({ importOpen: false });
      if (folder) {
        await get().enterSession(folder);
        get().setMode('pick');
        get().showToast(`导入完成：${folder}`);
      } else {
        get().showToast('导入完成（没解析出新主题名，看下面的输出）');
      }
    } catch (e) {
      get().showToast('导入失败：' + String(e));
    } finally {
      set({ importRunning: false });
    }
  },

  pushImportLog: (line) => {
    const log = get().importLog;
    const next = log.length >= IMPORT_LOG_MAX ? log.slice(-(IMPORT_LOG_MAX / 2)) : log.slice();
    next.push(line);
    set({ importLog: next });
  },
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
