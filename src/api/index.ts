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
      ) => Promise<string | null>;
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
      engineStocks: () => Promise<{ stocks: Stock[] }>;
      engineBases: () => Promise<{ bases: Base[] }>;
      engineParams: () => Promise<{ params: ParamDef[] }>;
      engineScan: (
        dir: string,
        exts: string[],
        limit: number
      ) => Promise<{ files: string[] }>;
      engineLoad: (paths: string[]) => Promise<LoadResult>;
      engineBase: (id: string) => Promise<{ bytes: string } | { ok: false }>;
      engineRender: (id: string, opts: RenderOpts) => Promise<RenderResult>;
      engineRawUrl: (
        sessionPath: string,
        rel: string
      ) => Promise<string | null>;
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
  archived?: boolean;
  [k: string]: any;
}

export interface ThumbMeta {
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
  /** 分组：真卷 / 脸 / 影调 / 质感 */
  grp?: string;
  /** true=只真卷下生效；false=只非真卷下生效；undefined=都生效 */
  spek?: boolean;
  d?: string;
  [k: string]: any;
}

export interface LoadResult {
  ok?: boolean;
  id?: string;
  [k: string]: any;
}

export interface RenderOpts {
  stock?: string;
  base?: string;
  params?: Record<string, number>;
  [k: string]: any;
}

export interface RenderResult {
  ok?: boolean;
  bytes?: string;
  [k: string]: any;
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
