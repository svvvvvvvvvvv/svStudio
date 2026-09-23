/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * ★★ 这是 `preload.js` 里全部 IPC 通道的 **唯一 TypeScript 封装修**.
 *
 * ⚠⚠ 铁律（09-15 改写）：**加一个通道必须四边同步**，少一边就是静默坏掉：
 *      ① `main.js` 里 `ipcMain.handle('xxx', …)`（返回形状 = 契约，见本文件末尾那段注释）
 *      ② `preload.js` 里 `contextBridge` 暴露同名方法
 *      ③ 本文件 `Window.api` 的类型 + `export const API` 里的封装（静态自检 [4] 组双向查这三边）
 *      ④ 用它的组件
 *    「`preload.js` 一个字节都不许改」是**老规矩、已经作废** —— 写那句话的时候改它的风险
 *    大于收益；但**要动主进程能力的功能**绕不开它。规矩换成：
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
      /** ★ 拖进来的文件夹 → 真实路径（Electron 33 没有 `File.path`，必须走 preload 的 webUtils） */
      getPathForFile: (file: File) => string;
      scanSessions: () => Promise<Session[]>;
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
      /** ★★ 相纸表（09-15）：`stock` 必传 —— **默认相纸跟着卷走**。
       *  返回里恰好一条 `isDefault`，前端**只认它**定初值（别自己挑一张）。 */
      enginePapers: (
        stock: string
      ) => Promise<{ ok?: boolean; items: Paper[]; error?: string }>;
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
      /** ★★ 导出成片（09-15 SV 选「A」）：**引擎渲染完直接写盘** ⇒ 回来的是文件路径，
       *  不是图片数据。`{ ok, path, w, h, bytes, ms }`；取消 ⇒ `{ ok:false, canceled:true }`。
       *  ⚠ 导出尺寸由引擎定（不传 side），前端不许写死 —— 用回来的 w/h 显示。 */
      exportImage: (payload: any) => Promise<any>;
      /* ---- 库外·纯 RAW 目录的预览索引 ---- */
      /** 给一个**纯 RAW 的库外目录**建/补预览索引（幂等）。
       *  ★ 源目录只读；缓存写在应用目录下、可以整个删。
       *  返回 `{ ok, n, skip, fail, dir, text, error, note? }`（`n` = 这次真转出来的张数）。 */
      extIndex: (srcDir: string) => Promise<ExtIndexResult>;
      /** 返回取消订阅函数 */
      onExtIndexProgress: (cb: (line: string) => void) => () => void;
    };
  }
}

/* ---------------- 数据类型 ---------------- */

export interface Session {
  name: string;
  count?: number;
  /** 目录的**真实路径**（库内主题 = `libRoot\\名字`；库外目录 = 加进来的那个目录本身）。
   *  ⚠★ `enterSession` **必须**用它 —— 不能再自己拼 `libRoot + '\\' + name`：
   *    库外目录不在 `libRoot` 底下，拼出来的路径根本不存在。
   *    （老返回里没有这个字段 ⇒ 调用点要留回落，见 `useStore.enterSession`。） */
  path?: string;
  /** 库外目录（左栏「加入目录…」加进来的；**原地读，没有复制进库**） */
  external?: boolean;
  /** 这个条目是从哪个库外根来的（一个根可能列成好几条）——「移除」按它移除 */
  rootDir?: string;
  /** 目录不在了（被删 / 改名）。条目照样列出来但点不进去 —— 静默消失最难查 */
  missing?: boolean;
  /** 预览索引目录（缓存，只放 1600 的机内 JPG）。**移除目录不会删它** */
  indexDir?: string;
  /** 索引还没建 / 源目录又多了新片 ⇒ 界面去跑一次 `extIndex(path)`。不是错误 */
  needsIndex?: boolean;
  /** 这次缺多少张（给进度提示用） */
  indexMiss?: number;
  /** 源目录里有几张 RAW（= 这次要抠多少张预览小图） */
  rawCount?: number;
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
  /** true = **走引擎物理链**（预设 `preset=` / 老真卷 `spek=`）；false/undefined = neutral。
   *  ⚠ 09-23 前叫 `spek` 且只看真卷 —— 卷表换成 9 条预设后会全判成中性卷。 */
  engine?: boolean;
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

/**
 * ★★ 相纸（09-15 新增）：真卷的那半张"纸"。
 *
 *   一张真卷出图 = **(负片, 相纸)** 的二元组，两者可以独立换：
 *   负片决定"什么胶卷"，相纸决定"冲印在什么纸上"（肤色/冷暖/饱和/暗部厚薄）。
 *   引擎侧 `spektra.PAPERS` + `spektra.papers(stock)` 是唯一出处。
 *
 *   ★ 默认值**由引擎给**（`papers(stock)` 里恰好一条 `isDefault = true` = 这一卷的配套纸）。
 *     前端不许自己写死纸名 —— 这是本项目最阴的一类坑（名字不认得 ⇒ 静默走默认）。
 *   ★ 名字认不得时引擎的 `resolve_paper()` 会**回落到配套纸并说出来**
 *     （`/stats` 里带 `print_fallback` / `print_fallback_reason`），不会崩。
 */
export interface Paper {
  name: string;
  label?: string;
  desc?: string;
  /** ★★ 这一卷**配套**的那张纸（默认值由引擎给，前端只认它） */
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
  dv?: number | boolean | string;
  /** 分组：影调 / 真卷 / 质感 / 脸（顺序就是右栏从上到下的顺序） */
  grp?: string;
  /**
   * ★★ 09-15（B3）：这一行画什么控件 —— **由引擎给，前端不许自己猜**。
   *   `num`（默认）= 滑杆；`bool` = 勾选框（整层开关）；`enum` = 下拉（选项在 `opts`）。
   */
  kind?: 'num' | 'bool' | 'enum';
  /** 下拉的选项：`v` = 传给引擎的值（也是 `dv` 的取值域），`t` = 给人看的名字 */
  opts?: { v: string; t: string }[];
  /**
   * ★★ 这一行**管的那个「整层开关」**（config 里 `*_ENABLE` 的名字，例如 `GRAIN_ENABLE`）。
   *  勾选框画在参数名**前面**；勾掉 = 那一层完全不跑（**不是**把强度拧到 0）。
   *  `gate_dv` 是它此刻的值（由引擎现读，前端不许写死）。
   */
  gate?: string;
  gate_dv?: boolean;
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
 *    engine-load                                  → { ok, items: [{id,path,ms} | {path,error}] }
 *    engine-base / -render / -raw-url             → { ok, image }   （image = data:URL 字符串）
 *    engine-stocks / -bases / -params / -papers   → { ok, items: [...] }
 *    engine-scan                                  → { ok, files, n }
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
  /** ★★ 相纸（真卷专用）。空串 = 用这一卷的配套纸（引擎侧 `resolve_paper()` 兜底）。
   *  名字不认得时引擎回落并标出来，不会崩 —— 别在前端自己兜。 */
  paper?: string;
  /** ⚠ 除了数字，还有整层开关（boolean）和下拉型号（string）—— 见 `ParamDef.kind` */
  params?: Record<string, number | boolean | string>;
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
  /** ★★ 相纸（真卷专用，按主题存）。老配方里没有这个字段 ⇒ undefined = 用配套纸。 */
  paper?: string;
  /** ⚠ 除了数字，还有整层开关（boolean）和下拉型号（string）—— 见 `ParamDef.kind` */
  params?: Record<string, number | boolean | string>;
  [k: string]: any;
}

/* ------------- 库外·纯 RAW 目录的预览索引（`ext-index`） ------------- */

/**
 * ★ 唯一出处 = `main.js` 的 `ipcMain.handle('ext-index')`；结果行由
 *   `tools/make_jpg_index.py` 末行的 `KS_EXT_INDEX_OK {…}` 给（**别改那行的格式**）。
 *
 * `ok:true` + `n:0` + `note` = **没活干**（已经是最新的 / 这目录没有纯 RAW）；
 * `ok:false` = 真出事了（脚本不在 / 解释器缺 rawpy / 目录读不到）——
 * 这种情况下那个目录在左栏里**会是空的**，而"空"和"坏了"在界面上长得一样，
 * 所以调用方必须把 `error` 说出来。
 */
export interface ExtIndexResult {
  ok: boolean;
  /** 这次真转出来的张数 */
  n?: number;
  /** 已有、跳过的张数 */
  skip?: number;
  fail?: number;
  /** 索引目录（缓存） */
  dir?: string;
  /** 脚本原始输出（出错时给人看现场） */
  text?: string;
  error?: string;
  /** 没活干时的说明（人话） */
  note?: string;
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
  /* ★ 拖进来的文件夹 → 真实路径（Electron 33 没有 `File.path`，走 preload 的 webUtils） */
  getPathForFile: (file: File) => api().getPathForFile(file),

  /* 照片库 */
  scanSessions: () => api().scanSessions(),
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
  /** ★ 相纸表：`stock` 必传（默认相纸跟着卷走） */
  enginePapers: (stock: string) => api().enginePapers(stock),
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
  exportImage: (payload: any) => api().exportImage(payload),

  /* 库外·纯 RAW 目录的预览索引 */
  extIndex: (srcDir: string) => api().extIndex(srcDir),
  onExtIndexProgress: (cb: (line: string) => void) =>
    api().onExtIndexProgress(cb),
};
