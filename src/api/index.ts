/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * ★★ 这是 preload.js 里 35 个 IPC 通道的 **唯一 TypeScript 封装修**.
 *
 * ⚠⚠ 铁律（09-15 改写）：**加一个通道必须四边同步**，少一边就是静默坏掉：
 *      ① `main.js` 里 `ipcMain.handle('xxx', …)`（返回形状 = 契约，见本文件末尾那段注释）
 *      ② `preload.js` 里 `contextBridge` 暴露同名方法
 *      ③ 本文件 `Window.api` 的类型 + `export const API` 里的封装（静态自检 [4] 组双向查这三边）
 *      ④ 用它的组件
 *    「`preload.js` 一个字节都不许改」是**老规矩、已经作废** —— 写那句话的时候改它的风险
 *    大于收益；但「导入照片」这种必须动主进程能力的功能绕不开它。规矩换成：
 *    **改它必须四边一起改**（静态自检 [4] 组会检查 preload ↔ API 两边方法名双向对得上）。
 *
 * 每个方法对应 preload 里同名的一项，返回结构以 `main.js` 实际返回 + 老 app.js 实际
 * 用法为准（不是猜的）。
 */

declare global {
  interface Window {
    api: {
      /** 渲染层日志（09-15 新增）：黑屏时助理读调试根下的 svstudio_render.log */
      logLine: (line: string) => Promise<boolean>;
      getConfig: () => Promise<any>;
      setConfig: (patch: any) => Promise<any>;
      pickDirectory: () => Promise<string | null>;
      scanSessions: (libRoot: string) => Promise<Session[]>;
      listPhotos: (sessionPath: string) => Promise<Photo[]>;
      readImage: (sessionPath: string, rel: string) => Promise<string | null>;
      getThumb: (
        sessionPath: string,
        rel: string,
        width: number
      ) => Promise<Thumb | null>;
      getThumbMeta: (
        sessionPath: string,
        rel: string
      ) => Promise<ThumbMeta | null>;
      getExif: (sessionPath: string, rel: string) => Promise<ExifInfo | null>;
      saveRatings: (ratings: Record<string, number>) => Promise<boolean>;
      importRatings: (libRoot: string) => Promise<any>;
      archivePhotos: (opts: ArchiveOpts) => Promise<ArchiveResult>;
      confirmDialog: (opts: ConfirmOpts) => Promise<boolean>;
      resetColorGrade: (themePath: string) => Promise<any>;
      /* ---- 调色台：svFilm 引擎（本机常驻 HTTP 服务） ---- */
      engineHealth: () => Promise<{ ok: boolean } & Record<string, any>>;
      engineStart: () => Promise<any>;
      engineStocks: () => Promise<{ ok?: boolean; items: Stock[]; error?: string }>;
      engineBases: () => Promise<{ ok?: boolean; items: Base[]; error?: string }>;
      engineParams: () => Promise<{ ok?: boolean; items: ParamDef[]; error?: string }>;
      engineScan: (
        dir: string,
        exts: string[],
        limit: number
      ) => Promise<{ ok?: boolean; files: string[]; n?: number }>;
      engineLoad: (paths: string[]) => Promise<LoadResult>;
      engineBase: (id: string) => Promise<ImageResult>;
      engineRender: (id: string, opts: RenderOpts) => Promise<ImageResult>;
      engineRawUrl: (
        sessionPath: string,
        rel: string
      ) => Promise<ImageResult>;
      /* ---- 调色参数按主题存 ---- */
      getGrade: (themeName: string) => Promise<GradeState | null>;
      setGrade: (themeName: string, grade: GradeState) => Promise<boolean>;
      exportGrade: (payload: any) => Promise<any>;
      /* ---- 照片导入 ---- */
      pickFile: (opts?: { title?: string; filters?: any[] }) => Promise<string | null>;
      importDetect: () => Promise<ImportDetectResult>;
      importPreview: (opts: ImportOpts) => Promise<ImportResult>;
      importRun: (opts: ImportOpts) => Promise<ImportResult>;
      /** 返回取消订阅函数 */
      onImportProgress: (cb: (line: string) => void) => () => void;
    };
  }
}

/* ---------------- 数据类型 ---------------- */

export interface Session {
  name: string;
  count?: number;
  /** 老代码里 session 可能带这些：done/errors 等 */
  [k: string]: any;
}

export interface Photo {
  name: string;
  rel: string;
  dir?: string;
  /** 排序键 */
  k?: string | number;
  /** 连拍分组 */
  grp?: string | number;
  lo?: string;
  hi?: string;
  hasRaw?: boolean;
  /** ★★ 出图源（**喂引擎用的那个文件的绝对路径**）：同名 RAW 优先，没有才回落到 JPG。
   *  main.js 的 `attachLoadPath()` 给的。⚠ 别拿 `rel` 当出图源 —— 它是身份键（星级/归档按它索引）。
   *  为什么必须 RAW：入口那一段（零点/成形/趾部/护栏）只在 `io.load_raw` 里跑，
   *  喂 JPG 的话「整张亮暗(总)」「暗部亮度」永远是死的。 */
  loadPath?: string;
  /** `loadPath` 是不是一张 RAW（false = 这个主题只有 JPG，入口那两根滑杆不生效） */
  loadIsRaw?: boolean;
  archived?: boolean;
  [k: string]: any;
}

export interface ThumbMeta {
  ow: number;
  oh: number;
}

/** get-thumb 的返回（★ 是对象不是字符串 —— 09-15 裂图就是把它当字符串用了） */
export interface Thumb {
  url: string;
  ow: number;
  oh: number;
}

export interface ExifInfo {
  [k: string]: any;
}

export interface ArchiveOpts {
  themePath: string;
  items: { rel: string; star: number }[];
}

export interface ArchiveResult {
  done?: any[];
  removed?: any[];
  failed?: any[];
  [k: string]: any;
}

export interface ConfirmOpts {
  title?: string;
  message: string;
  detail?: string;
  buttons?: string[];
}

export interface Stock {
  name: string;
  label?: string;
  desc?: string;
  /** true = 这是真卷（走 spektrafilm）；false/undefined = neutral */
  spek?: boolean;
}

export interface Base {
  name: string;
  label?: string;
  desc?: string;
  /** ★★ 引擎当前配置的默认基准（引擎侧 `config.BASE`，`/bases` 里恰好一条为 true）。
   *  前端**只认这个**来定初值 —— 不许自己写死基准名：
   *  过去写死 `'all'`，而引擎的基准表里没有 `'all'` ⇒ `resolve_base` **静默**回落成
   *  `BASE_NONE`（"不套基准"）⇒ 默认出图等于没套基准，且界面上四支**一支都选不中**。 */
  isDefault?: boolean;
}

export interface ParamDef {
  k: string;
  name: string;
  lo: number;
  hi: number;
  step: number;
  /**
   * ★★ 引擎**此刻实际在用**的值 —— 滑杆初值必须用它。
   * 由引擎现读 config（`service._param_defs()`），所以永远跟出厂值同步。
   * ⚠ 历史坑：这里原本没有 dv，前端退而取「区间中点」`(lo+hi)/2`，
   *   而引擎用的是 config 出厂值 ⇒ 13 根滑杆里 12 根**显示的数字和实际生效的对不上**
   *   （「整张浓淡」显示 0.50 / 实际 0.00）。别再退回中点。
   * ⚠ `inv` 的项，dv 已经翻成"人话方向"了（显示值），别自己再换算。
   */
  dv?: number;
  /** 分组：真卷 / 脸 / 影调 / 质感 */
  grp?: string;
  /** true=只真卷下生效；false=只非真卷下生效；undefined=都生效 */
  spek?: boolean;
  d?: string;
  /** 显示方向与引擎值相反（引擎侧已做换算）—— 前端只用它画个提示，不用自己换算 */
  inv?: boolean;
  [k: string]: any;
}

/**
 * ★★ 引擎侧返回形状的**唯一契约 = `main.js` 的 IPC 处理器**。
 *    别在这个文件里自己发明字段（09-15 踩过：前端读 `r.id` / `r.bytes` / `{stocks}`，
 *    而 main.js 实际给的是 `{items:[{id,…}]}` / `{image}` / `{items:[…]}`
 *    ⇒ 卷/基准列表全空、一张图都渲染不出来，而且 mock 也跟着错、自检全绿）。
 *
 *    engine-load                      → { ok, items: [{id,path,ms} | {path,error}] }
 *    engine-base / -render / -raw-url → { ok, image }   （image = data:URL 字符串）
 *    engine-stocks / -bases / -params → { ok, items: [...] }
 *    engine-scan                      → { ok, files, n }
 */
export interface LoadItem {
  id?: number;
  path?: string;
  ms?: number;
  error?: string;
}

export interface LoadResult {
  ok?: boolean;
  items?: LoadItem[];
  error?: string;
}

export interface ImageResult {
  ok?: boolean;
  /** data:image/...;base64,... —— 直接塞 <img src> */
  image?: string;
  error?: string;
}

export interface RenderOpts {
  stock?: string;
  base?: string;
  params?: Record<string, number>;
  [k: string]: any;
}

export interface RenderResult {
  ok?: boolean;
  image?: string;
  error?: string;
}

export interface GradeState {
  stock?: string;
  base?: string;
  params?: Record<string, number>;
  [k: string]: any;
}

/* ---------------- 照片导入（SD 卡 / U 盘 → 照片库） ---------------- */

/** 检测到的一张卡（或任何带 `DCIM` 的目录） */
export interface ImportCard {
  /** 盘符，如 'J' */
  drive: string;
  /** 有照片的那个子目录，如 `J:\DCIM\100_FUJI` */
  path: string;
  /** 里面有多少个照片/视频 */
  n: number;
}

export interface ImportDetectResult {
  ok: boolean;
  cards: ImportCard[];
  /** 导入脚本路径（**空串 = 还没配**，界面要引导用户选一次，之后写进 config） */
  script: string;
  /** 目标库根 = 工作台当前的照片库（片导到别处就"导入后看不见"了） */
  libRoot: string;
}

/** 导入表单。`destRoot` 留空 ⇒ main.js 自动取工作台当前的照片库 */
export interface ImportOpts {
  src?: string;
  topic?: string;
  place?: string;
  /** YYYY-MM-DD；留空/'auto' = 从 EXIF 推断 */
  date?: string;
  destRoot?: string;
  destDrive?: string;
  /** 完整文件夹名（覆盖自动命名） */
  folder?: string;
  /** 跨多天也平铺，不按日期建子目录 */
  keepFlat?: boolean;
  /** 跳过拷完的校验（不建议） */
  noVerify?: boolean;
}

/**
 * ★★ 导入脚本 stdout 解析出来的「计划 / 结果」。
 *    锚的是脚本里的固定台词（`源目录 :` / `文件数 :` / `目标目录:` /
 *    `复制完成：新增 N，跳过(已存在) N，失败 N` / `耗时 N 秒`）。
 *    唯一出处是 `main.js` 的 `parseImportOutput()` —— 改脚本台词必须两边一起改
 *    （`_check/ui_smoke.mjs` 有静态检查把这两边钉在一起）。
 */
export interface ImportPlan {
  src?: string;
  files?: number | null;
  total?: string;
  types?: string;
  dates?: string;
  candidates?: string;
  destDrive?: string;
  /** 目标目录（完整路径） */
  dest?: string;
  /** 文件夹名 = **新主题名**（跑完直接进它的选片台） */
  folder?: string;
  /** 目标库根 */
  destRoot?: string;
  copied?: number | null;
  skipped?: number | null;
  failed?: number | null;
  elapsed?: string;
  verified?: boolean;
  dryRun?: boolean;
  renamed?: string;
  warning?: string;
  error?: string;
}

export interface ImportResult {
  ok: boolean;
  /** 原始 stdout（出错时给人看现场，别只给一句"失败了"） */
  text?: string;
  plan?: ImportPlan | null;
  error?: string;
}

/* ---------------- 封装 ---------------- */

const noApi = () => {
  throw new Error(
    'window.api 不存在 —— preload 没挂上。检查 main.js 的 webPreferences.preload。'
  );
};

function api() {
  if (typeof window === 'undefined' || !window.api) noApi();
  return window.api;
}

export const API = {
  /** 渲染层日志：写到调试根下的 svstudio_render.log（黑屏定位用） */
  logLine: (line: string) => api().logLine(line),

  /* 配置 */
  getConfig: () => api().getConfig(),
  setConfig: (patch: any) => api().setConfig(patch),
  pickDirectory: () => api().pickDirectory(),

  /* 照片库 */
  scanSessions: (libRoot: string) => api().scanSessions(libRoot),
  listPhotos: (sessionPath: string) => api().listPhotos(sessionPath),
  readImage: (sessionPath: string, rel: string) =>
    api().readImage(sessionPath, rel),
  getThumb: (sessionPath: string, rel: string, width: number) =>
    api().getThumb(sessionPath, rel, width),
  getThumbMeta: (sessionPath: string, rel: string) =>
    api().getThumbMeta(sessionPath, rel),
  getExif: (sessionPath: string, rel: string) =>
    api().getExif(sessionPath, rel),

  /* 星级与归位 */
  saveRatings: (ratings: Record<string, number>) => api().saveRatings(ratings),
  importRatings: (libRoot: string) => api().importRatings(libRoot),
  archivePhotos: (opts: ArchiveOpts) => api().archivePhotos(opts),
  resetColorGrade: (themePath: string) => api().resetColorGrade(themePath),

  /* 对话框 */
  confirmDialog: (opts: ConfirmOpts) => api().confirmDialog(opts),

  /* 引擎 */
  engineHealth: () => api().engineHealth(),
  engineStart: () => api().engineStart(),
  engineStocks: () => api().engineStocks(),
  engineBases: () => api().engineBases(),
  engineParams: () => api().engineParams(),
  engineScan: (dir: string, exts: string[], limit: number) =>
    api().engineScan(dir, exts, limit),
  engineLoad: (paths: string[]) => api().engineLoad(paths),
  engineBase: (id: string) => api().engineBase(id),
  engineRender: (id: string, opts: RenderOpts) => api().engineRender(id, opts),
  engineRawUrl: (sessionPath: string, rel: string) =>
    api().engineRawUrl(sessionPath, rel),

  /* 调色参数（按主题） */
  getGrade: (themeName: string) => api().getGrade(themeName),
  setGrade: (themeName: string, grade: GradeState) =>
    api().setGrade(themeName, grade),
  exportGrade: (payload: any) => api().exportGrade(payload),

  /* 照片导入 */
  pickFile: (opts?: { title?: string; filters?: any[] }) => api().pickFile(opts),
  importDetect: () => api().importDetect(),
  importPreview: (opts: ImportOpts) => api().importPreview(opts),
  importRun: (opts: ImportOpts) => api().importRun(opts),
  /** ★ 返回**取消订阅函数**（`useEffect` 里直接 return 它） */
  onImportProgress: (cb: (line: string) => void) => api().onImportProgress(cb),
};
