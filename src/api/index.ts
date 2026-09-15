/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * ★★ 这是 preload.js 里 30 个 IPC 通道的 **唯一 TypeScript 封装修**.
 *
 * ⚠⚠ 铁律：`preload.js` 一个字节都不许改 —— 它是主进程与渲染进程之间的契约,
 *      改动它的风险最大、收益为零。这里只做"类型标注 + 少量化名"。
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
};
