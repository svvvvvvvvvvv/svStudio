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
      getThumb: (
        sessionPath: string,
        rel: string,
        width: number
      ) => Promise<Thumb | null>;
      getExif: (sessionPath: string, rel: string) => Promise<ExifInfo | null>;
      saveRatings: (ratings: Record<string, number>) => Promise<boolean>;
      archivePhotos: (opts: ArchiveOpts) => Promise<ArchiveResult>;
      confirmDialog: (opts: ConfirmOpts) => Promise<boolean>;
      resetColorGrade: (dirPath: string) => Promise<any>;
      /* ---- 调色台：svFilm 引擎（本机常驻 HTTP 服务） ---- */
      engineHealth: () => Promise<{ ok: boolean } & Record<string, any>>;
      engineStart: () => Promise<any>;
      engineStocks: () => Promise<{ ok?: boolean; items: Stock[]; error?: string }>;
      /** ★★ 曝光风格（09-23）：三条档，靶值从大师真片量出来，引擎侧唯一出处。 */
      engineStyles: () => Promise<{ ok?: boolean; items: Style[]; error?: string }>;
      engineLoad: (paths: string[]) => Promise<LoadResult>;
      engineBase: (id: string) => Promise<ImageResult>;
      engineRender: (id: string, opts: RenderOpts) => Promise<ImageResult>;
      /* ---- 调色参数按目录存 ---- */
      getGrade: (dirName: string) => Promise<GradeState | null>;
      setGrade: (dirName: string, grade: GradeState) => Promise<boolean>;
      /** ★★ 导出成片（09-15 SV 选「A」）：**引擎渲染完直接写盘** ⇒ 回来的是文件路径，
       *  不是图片数据。`{ ok, path, w, h, bytes, ms }`；取消 ⇒ `{ ok:false, canceled:true }`。
       *  ⚠ 导出尺寸由引擎定（不传 side），前端不许写死 —— 用回来的 w/h 显示。 */
      exportImage: (payload: any) => Promise<any>;
      /** 批量出片：整个目录用同一套选择器全出，写进 `<目录>/调色待验收`。
       *  进度走 `onExportBatchProgress` 事件（几百张要跑几十分钟，等返回值界面像死机）。 */
      exportBatch: (payload: BatchOpts) => Promise<BatchResult>;
      exportBatchCancel: () => Promise<boolean>;
      onExportBatchProgress: (cb: (o: BatchProgress) => void) => () => void;
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
  /** 目录的**真实路径**（库内目录 = `libRoot\\名字`；库外目录 = 加进来的那个目录本身）。
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
  /** 显示名 = 那张 RAW 的真实文件名（`DSCF1000.RAF`） */
  name: string;
  /** ★★ **身份键** = 文件名去掉扩展名（大写）。星级 / 桶合并 / 缩略图缓存 / 配方全按它索引。
   *  一张片 = 一个 RAW；从它取出的机内 JPG 与它共用这个键。
   *  ⚠ 不许当路径用 —— 磁盘上没有这个名字。 */
  rel: string;
  dir?: string;
  /** 排序键 */
  k?: string | number;
  /** 连拍分组 */
  grp?: string | number;
  lo?: string;
  hi?: string;
  /** ★★ 出图源：**喂引擎用的那个文件的绝对路径**（永远指向根目录里的 RAW）。
   *  main.js 的 `attachLoadPath()` 给的。 */
  loadPath?: string;
  archived?: boolean;
  [k: string]: any;
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
  dirPath: string;
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

/**
 * ★★ 曝光风格（09-23 SV 重新划边界：调色台只剩两个选择器 —— 胶片风格 + 曝光风格）。
 *   三条档的靶是**从大师真片量出来的**（1144 张），唯一出处是引擎的 tone.STYLES
 *   ⇒ 前端**不许**自己写死档位名或数值。
 */
export interface Style {
  name: string;
  desc?: string;
  /** ★★ 引擎当前配置的默认档（config.STYLE）⇒ 前端只认它定初值 */
  isDefault?: boolean;
  /** 落点（整张中位亮度 L*）/ 黑位（L5）/ 亮部（L95）—— 给人话说明用。
   *  ⚠ 只在「曝光风格作用在**引擎之前**」那套配置下才有（打绝对靶）。 */
  L50?: number;
  L5?: number;
  L95?: number;
  /** ★★ 「曝光风格作用在**引擎之后**」那套配置下的三个力度（相对量）——
   *  **都是"往下搬多少"**：中位几档 / 亮部几个 L* / 黑位几个 L*。
   *  数值来自鹿井 32 张成片的内容归一形状（见 svFilm/svFilm/tone.py 的 REL）。 */
  evDown?: number;
  hiDown?: number;
  blDown?: number;
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
  /** 胶片风格 = 9 条预设之一（名字必须来自 `/stocks`） */
  stock?: string;
  /** 曝光风格 = 高长调 / 中性调 / 暗调 之一（名字必须来自 `/styles`） */
  style?: string;
  [k: string]: any;
}

export interface RenderResult {
  ok?: boolean;
  image?: string;
  error?: string;
}

export interface GradeState {
  /** 胶片风格（9 条预设之一） */
  stock?: string;
  /** 曝光风格（高长调 / 中性调 / 暗调） */
  style?: string;
  [k: string]: any;
}

/** 批量出片的请求：整个目录、一套选择器、一个目标尺寸。 */
export interface BatchOpts {
  dirPath: string;
  items: { rel: string }[];
  stock?: string;
  style?: string;
  /** 长边像素。⚠ 不传默认 2048 —— 原图全尺寸一张 RAW 约 6 分半。 */
  side?: number;
}

export interface BatchResult {
  ok?: boolean;
  /** 成片写进的目录（`<目录>/调色待验收`） */
  dir?: string;
  done?: number;
  canceled?: boolean;
  failed?: { file: string; error: string }[];
  error?: string;
}

export interface BatchProgress {
  /** start = 这一张开跑；one = 这一张出完；end = 整批结束 */
  phase: 'start' | 'one' | 'end';
  i: number;
  n: number;
  name?: string;
  ok?: boolean;
  done?: number;
  failed?: number;
  ms?: number;
  out?: string;
  canceled?: boolean;
  error?: string;
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
  getThumb: (sessionPath: string, rel: string, width: number) =>
    api().getThumb(sessionPath, rel, width),
  getExif: (sessionPath: string, rel: string) =>
    api().getExif(sessionPath, rel),

  /* 星级与归位 */
  saveRatings: (ratings: Record<string, number>) => api().saveRatings(ratings),
  archivePhotos: (opts: ArchiveOpts) => api().archivePhotos(opts),
  resetColorGrade: (dirPath: string) => api().resetColorGrade(dirPath),

  /* 对话框 */
  confirmDialog: (opts: ConfirmOpts) => api().confirmDialog(opts),

  /* 引擎 */
  engineHealth: () => api().engineHealth(),
  engineStart: () => api().engineStart(),
  engineStocks: () => api().engineStocks(),
  engineStyles: () => api().engineStyles(),
  engineLoad: (paths: string[]) => api().engineLoad(paths),
  engineBase: (id: string) => api().engineBase(id),
  engineRender: (id: string, opts: RenderOpts) => api().engineRender(id, opts),

  /* 调色参数（按目录） */
  getGrade: (dirName: string) => api().getGrade(dirName),
  setGrade: (dirName: string, grade: GradeState) =>
    api().setGrade(dirName, grade),
  exportImage: (payload: any) => api().exportImage(payload),
  exportBatch: (payload: BatchOpts) => api().exportBatch(payload),
  exportBatchCancel: () => api().exportBatchCancel(),
  onExportBatchProgress: (cb: (o: BatchProgress) => void) =>
    api().onExportBatchProgress(cb),

  /* 库外·纯 RAW 目录的预览索引 */
  extIndex: (srcDir: string) => api().extIndex(srcDir),
  onExtIndexProgress: (cb: (line: string) => void) =>
    api().onExtIndexProgress(cb),
};
