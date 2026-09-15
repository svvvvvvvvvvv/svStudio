const { app, BrowserWindow, ipcMain, dialog, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const http = require('http');
const { spawn } = require('child_process');

/* 引擎启动用哪份 Python —— 必须是**装了 spektrafilm 依赖的那个解释器**
   （缺 `colour` 库的话，引擎起得来、但一 /render 就 ModuleNotFoundError）。
   ★ 不要把本机路径写死在这儿（要开源）。优先级：
     ① 环境变量 SVFILM_PY  ② 配置里的 enginePy  ③ PATH 上的 python
   自己那份写进配置（在用户目录里，不进仓库）或设 SVFILM_PY。 */
function enginePy() {
  let cfgPy = '';
  try {
    cfgPy = String(loadConfig().enginePy || '').trim();
  } catch (e) {
    /* app 还没 ready 等等，忽略 */
  }
  const envPy = String(process.env.SVFILM_PY || '').trim();
  for (const c of [envPy, cfgPy]) {
    if (c && fs.existsSync(c)) return c;
  }
  // 都不存在也把「用户自己填的那个」返回出去，好让报错指名道姓
  return cfgPy || envPy || 'python';
}
/* ★★ 09-15：全部改成**相对本文件**算，不再写死 E:\WorkBuddy\...
   这样整个目录搬到哪儿（E:\svStudio 或别处）都能直接跑。
   svFilm 现在就在本仓库里的 `svFilm/` 子目录（已合仓）。 */
const ENGINE_CWD = path.join(__dirname, 'svFilm');
/* ★ 调试产出的根（引擎日志 / 渲染日志 / 实验中间结果）。
   09-15 SV 定的约定：统一放 `E:\Debug_svStudio` ⇒ 写进配置的 `debugDir`（**不进仓库**）。
   没配就落到应用自己的 userData 下 —— 别去猜「仓库的上级」，那样搬到哪都会错。
   注：结果会缓存，改了 debugDir 要重开台子。 */
let _debugDir = null;
function debugDir() {
  if (!_debugDir) {
    let d = '';
    try {
      d = String(loadConfig().debugDir || '').trim();
    } catch (e) {
      /* app 还没 ready 之类 */
    }
    if (!d) d = path.join(app.getPath('userData'), '_debug');
    try {
      fs.mkdirSync(d, { recursive: true });
    } catch (e) {
      /* ignore */
    }
    _debugDir = d;
  }
  return _debugDir;
}

/** ★ 引擎启动日志：引擎起不来时唯一的线索来源，助理直接读它 */
function engineLogFile() {
  return path.join(debugDir(), 'engine_start.log');
}
/* ⚠ 目录不存在时 `fs.openSync(...,'a')` 会 ENOENT，把整个 engine-start 抛掉 ——
   表现就是点「渲染」报「拉起服务失败」，而且因为日志都写不出来，一点线索都没有。
   debugDir() 里已经 mkdir 过，这里再兜一道（也当自检的锚点用）。 */
function ensureEngineLog() {
  const d = path.dirname(engineLogFile());
  if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
}

// 注意：app.getPath 必须等 app ready 之后才能调用，
// 因此这里做成惰性取值，避免模块顶层访问导致 undefined 崩溃。
let _configFile = null;
function configFile() {
  if (!_configFile) {
    _configFile = path.join(app.getPath('userData'), 'config.json');
  }
  return _configFile;
}

let _cacheDir = null;
function cacheDir() {
  if (!_cacheDir) {
    _cacheDir = path.join(app.getPath('userData'), 'thumbcache');
    try {
      fs.mkdirSync(_cacheDir, { recursive: true });
    } catch (e) {
      /* ignore */
    }
  }
  return _cacheDir;
}

const IMG_EXT = /\.(jpe?g)$/i;
const RAW_EXT = /\.(raf|raw|cr2|cr3|nef|arw|dng)$/i;

let win;

function defaultConfig() {
  return {
    // ★ 图库根：留空 = 先用系统的「图片」目录（见 withLibFallback），再在台子里切到自己的照片目录。
    //   不要把某个人的绝对路径写进仓库。
    libRoot: '',
    // ★ 引擎用哪份 Python（绝对路径）。留空 = 用 SVFILM_PY / PATH 上的 python
    enginePy: '',
    // ★ 调试产出根（日志/实验中间结果）。留空 = 应用自己的 userData/_debug
    debugDir: '',
    lastSession: null,   // { name } 上次进入的主题
    lastIdx: -1,         // 上次离开的照片 index（重启后恢复）
    lastFilter: 'all',   // 上次的筛选档（重启后恢复）
    mode: 'pick',        // 'pick' 选片台 | 'grade' 调色台（顶栏 tab，重启恢复）
    grades: {},          // 调色台参数：**按主题存** { 主题名: {stock, base, params:{...}} }
    ratings: {}
  };
}

function loadConfig() {
  try {
    const CF = configFile();
    if (fs.existsSync(CF)) {
      const c = JSON.parse(fs.readFileSync(CF, 'utf8'));
      const cfg = Object.assign(defaultConfig(), c);
      // 旧版死字段清理（archiveRoot/lrExe 已随 LR 工作流移除）
      delete cfg.archiveRoot;
      delete cfg.lrExe;
      return withLibFallback(cfg);
    }
  } catch (e) {
    console.error('config load failed', e);
  }
  return withLibFallback(defaultConfig());
}

/** 图库根为空时给个像样的默认（系统的「图片」目录），别让新用户对着空列表发呆 */
function withLibFallback(cfg) {
  if (!cfg.libRoot) {
    try {
      cfg.libRoot = app.getPath('pictures');
    } catch (e) {
      /* ignore */
    }
  }
  return cfg;
}

function saveConfig(cfg) {
  try {
    fs.mkdirSync(path.dirname(configFile()), { recursive: true });
    fs.writeFileSync(configFile(), JSON.stringify(cfg, null, 2), 'utf8');
    return true;
  } catch (e) {
    console.error('config save failed', e);
    return false;
  }
}

/* =========================================================
   星级目录规范（★累积桶模型，2026-09-06 定稿）
   主题根          = 原图（JPG+RAF）——永不移动、永不删除
   初筛1星         = 所有 ★≥1 的片（root 直出 JPG 复制件，星级升高也保留）
   调色待验收       = 调色管线输出暂存（星级同步永不动它，重置调色才清）
   调色后满意的2星  = 所有 ★≥2 的片（调色成片副本，升到 3 星也保留）
   待发布          = 所有 ★≥3 的片（成片副本）
   升级=往高桶复制新增；降级=删掉高桶里的（低桶产物无损保留，3→2 星成片原地还在）。
   全程只有复制与删除，绝不移动；桶里只放 JPG，RAF 始终留在 root。
   ★≥2 必须有调色成片（来源 待发布>2星桶>调色待验收），无成片拒绝同步——
   绝不拿 root 直出兜底冒充成片。
   注意：renderer 无 node 集成，这些常量在 app.js 有一份镜像，改名两处同步。
   ========================================================= */
const STAR1_DIR = '初筛1星';
const STAR2_DIR = '调色后满意的2星';
const PUBLISH_DIR = '待发布';
const REVIEW_DIR = '调色待验收';
const ARCHIVED_SUBDIRS = [STAR1_DIR, STAR2_DIR, PUBLISH_DIR];  // 归档桶
const THEME_SUBDIRS = [...ARCHIVED_SUBDIRS, REVIEW_DIR];       // 参与 merge 的全部子目录

function starDirOf(star) {
  if (!star || star < 1) return null;
  if (star >= 3) return PUBLISH_DIR;
  return star === 1 ? STAR1_DIR : STAR2_DIR;
}

/**
 * 扫描照片库：一级子目录视为一个「主题」。
 * 主题 = 根含原图，或含任一星级/调色子目录。张数含全部子目录（同主题全量可见）。
 */
function scanSessions(libRoot) {
  const out = [];
  if (!libRoot || !fs.existsSync(libRoot)) return out;
  let entries;
  try {
    entries = fs.readdirSync(libRoot, { withFileTypes: true });
  } catch (e) {
    return out;
  }
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    if (e.name.startsWith('.')) continue;
    const full = path.join(libRoot, e.name);
    let files = [];
    try {
      files = fs.readdirSync(full);
    } catch (err) {
      continue;
    }
    const jpgs = files.filter((f) => IMG_EXT.test(f));
    const subNames = files.filter((f) => THEME_SUBDIRS.includes(f));
    if (!jpgs.length && !subNames.length) continue;   // 不是主题
    // count 按文件名去重：桶里多是 root 同名复制件/成片，重复计数会虚高
    const rootSet = new Set(jpgs.map((f) => f.toLowerCase()));
    let count = jpgs.length;
    let archivedCount = 0;
    let hasRaw = files.some((f) => RAW_EXT.test(f));
    for (const sub of THEME_SUBDIRS) {
      const sd = path.join(full, sub);
      if (!fs.existsSync(sd)) continue;
      let sfiles = [];
      try { sfiles = fs.readdirSync(sd); } catch (err) { sfiles = []; }
      const sj = sfiles.filter((f) => IMG_EXT.test(f));
      count += sj.filter((f) => !rootSet.has(f.toLowerCase())).length;
      if (sfiles.some((f) => RAW_EXT.test(f))) hasRaw = true;
      if (ARCHIVED_SUBDIRS.includes(sub)) archivedCount += sj.length;
    }
    out.push({
      name: e.name,
      path: full,
      count,
      archivedCount,
      hasRaw
    });
  }
  out.sort((a, b) => (a.name < b.name ? 1 : a.name > b.name ? -1 : 0));
  return out;
}

/**
 * 列出主题内所有图片（JPG 用于预览），配对 root 原图的同名 RAW。
 * ★ 同名多形态合并：root 原图 / 初筛1星复制件 / 调色待验收输出 / 2星成片 / 待发布成片
 *   共用同一评分 key（主题名||文件名），dock 只显示「最新工作形态」一条：
 *   优先级 调色待验收 > 待发布 > 调色后满意的2星 > 初筛1星 > root。
 *   每张照片带 `dir`（显示形态的真实目录）与 `archived`（形态是否在星级桶内）。
 */
function listPhotos(sessionPath) {
  const PRIO = {};
  PRIO[REVIEW_DIR] = 4; PRIO[PUBLISH_DIR] = 3; PRIO[STAR2_DIR] = 2; PRIO[STAR1_DIR] = 1;
  const byRel = new Map();   // rel -> {p, prio}
  const put = (p, prio) => {
    const cur = byRel.get(p.rel);
    if (cur && cur.prio >= prio) return;
    p.archived = prio > 0;
    byRel.set(p.rel, { p, prio });
  };
  for (const p of photosInDir(sessionPath)) put(p, 0);
  for (const sub of THEME_SUBDIRS) {
    const sd = path.join(sessionPath, sub);
    if (!fs.existsSync(sd)) continue;
    for (const p of photosInDir(sd)) put(p, PRIO[sub]);
  }
  const photos = [...byRel.values()].map((x) => x.p);
  photos.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  return photos;
}

/** 扫单个目录内所有 JPG（配对同名 RAW），返回未排序数组。
    调用方负责排序与打标记。 */
function photosInDir(dir) {
  let files = [];
  try {
    files = fs.readdirSync(dir);
  } catch (e) {
    return [];
  }
  const photos = files
    .filter((f) => IMG_EXT.test(f))
    .map((f) => {
      const base = f.replace(/\.[^.]+$/, '');
      let rawName = null;
      for (const r of files) {
        if (RAW_EXT.test(r) && r.replace(/\.[^.]+$/, '').toUpperCase() === base.toUpperCase()) {
          rawName = r;
          break;
        }
      }
      return { name: f, rel: f, hasRaw: !!rawName, rawName, dir };
    });
  return photos;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: '#1c1c1e',
    title: 'svStudio',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

/* ---------------- IPC ---------------- */

/* ★★ 渲染进程日志（09-15 新增，纯新增不动已有通道）：
   黑屏/白屏时 SV 不用截图 —— 助理直接读这个文件定位。
   写到 debugDir() 下，每行带时间戳。 */
ipcMain.handle('log-line', (_e, line) => {
  try {
    const f = path.join(debugDir(), 'svstudio_render.log');
    const ts = new Date().toISOString().replace('T', ' ').slice(0, 19);
    fs.appendFileSync(f, '[' + ts + '] ' + String(line) + '\n');
  } catch { /* 日志失败不影响主流程 */ }
  return true;
});

ipcMain.handle('get-config', () => loadConfig());

ipcMain.handle('set-config', (e, patch) => {
  const cfg = Object.assign(loadConfig(), patch || {});
  saveConfig(cfg);
  return cfg;
});

ipcMain.handle('pick-directory', async () => {
  const res = await dialog.showOpenDialog(win, {
    properties: ['openDirectory']
  });
  if (res.canceled || !res.filePaths.length) return null;
  return res.filePaths[0];
});

ipcMain.handle('scan-sessions', (e, libRoot) => scanSessions(libRoot));

ipcMain.handle('list-photos', (e, sessionPath) => listPhotos(sessionPath));

/* 缩略图串行队列：
   解码/缩放若并发跑多张会长时间独占主进程导致 UI 冻结。
   用一条串行队列，每处理完一张就 setImmediate 让出事件循环，
   保证窗口始终能响应，缩略图渐进出现。task 可为同步函数或返回 Promise。 */
let _thumbQueue = Promise.resolve();
function enqueueThumb(task) {
  const run = () => Promise.resolve().then(task);
  const p = _thumbQueue.then(run, run);
  _thumbQueue = p.then(
    () => new Promise((r) => setImmediate(r)),
    () => new Promise((r) => setImmediate(r))
  );
  return p;
}

/** 取原图"显示方向正确"的宽高（用于悬浮预览按比例自适应）。
    关键坑：相机竖拍 JPG 物理像素常是横的（如富士 4416×2944），
    真正横竖由 EXIF Orientation(0x0112) 决定：6/8(±90°)应宽高互换。
    nativeImage.getSize() 返回物理横尺寸，忽略 orientation →
    必须读 EXIF 修正，否则竖图会先横框再"闪"成竖。
    带进程内 Map 缓存（全路径维度，稳），每次最多付出一次解析成本。 */
const _oriDimCache = new Map(); // full -> {ow, oh}
function _readOrientation(full) {
  // 只读头部，尽量轻。返回 0 表示未知/无。
  try {
    const buf = readJPEGBuffer(full);
    const ts = findExifSegment(buf);
    if (ts < 0) return 0;
    const raw = parseTIFF(buf, ts);
    const o = parseInt(raw.orientation, 10);
    return isNaN(o) ? 0 : o;
  } catch (e) {
    return 0;
  }
}
function getOriDim(full) {
  const hit = _oriDimCache.get(full);
  if (hit) return hit;
  let ow = 0, oh = 0;
  try {
    const szImg = nativeImage.createFromPath(full);
    if (szImg && !szImg.isEmpty()) {
      const s = szImg.getSize();
      ow = s.width; oh = s.height;
    }
  } catch (e) { /* keep 0 */ }
  // Orientation 5/6/7/8 = 旋转 ±90°，实际显示横竖互换
  const ori = _readOrientation(full);
  if (ow > 0 && oh > 0 && (ori === 5 || ori === 6 || ori === 7 || ori === 8)) {
    const t = ow; ow = oh; oh = t;
  }
  const dim = { ow, oh };
  _oriDimCache.set(full, dim);
  return dim;
}

/** 生成单张缩略图：优先系统缩略图 API（只解码最小必要数据，异步）；
    不可用时回退到 createFromPath + resize（整图解码）。带落盘缓存。
    返回 { url, ow, oh }：url=base64 dataURL；ow/oh=原图像素宽高（给悬浮预览自适应用） */
async function makeThumb(full, width) {
  const w = width || 260;
  const key = crypto.createHash('md5').update(full + '|' + w).digest('hex');
  const cacheFile = path.join(cacheDir(), key + '.jpg');
  let url = null;
  try {
    if (fs.existsSync(cacheFile)) {
      const buf = fs.readFileSync(cacheFile);
      url = 'data:image/jpeg;base64,' + buf.toString('base64');
    }
  } catch (e) {
    /* fall through */
  }

  if (!url) {
    const serve = (buf) => {
      if (!buf) return null;
      try {
        fs.writeFileSync(cacheFile, buf);
      } catch (e) {
        /* ignore cache write failure */
      }
      return 'data:image/jpeg;base64,' + buf.toString('base64');
    };

    // 1) 优先系统缩略图：createThumbnailFromPath 用系统/框架级缩略，
    //    只解码必要最小数据，比整图解码快一个量级。返回 Promise。
    if (typeof nativeImage.createThumbnailFromPath === 'function') {
      try {
        const nt = await nativeImage.createThumbnailFromPath(full, {
          width: w,
          height: w,
        });
        if (nt && !nt.isEmpty()) url = serve(nt.toJPEG(82));
      } catch (e) {
        /* fall through to createFromPath */
      }
    }

    // 2) 回退：整图解码再缩放
    if (!url) {
      try {
        const img = nativeImage.createFromPath(full);
        if (img.isEmpty()) return null;
        const size = img.getSize();
        const h = Math.max(1, Math.round((size.height / size.width) * w));
        const resized = img.resize({ width: w, height: h, quality: 'good' });
        url = serve(resized.toJPEG(82));
      } catch (e) {
        return null;
      }
    }
  }

  // 顺道取原图比例（缓存命中也只算一次）
  const dim = getOriDim(full);
  return { url, ow: dim.ow, oh: dim.oh };
}

/** 按需取单张缩略图（前端只请求可见的，避免 744 张全量解码）
    返回 {url, ow, oh} */
ipcMain.handle('get-thumb', async (e, sessionPath, rel, width) => {
  const full = path.join(sessionPath, rel);
  if (!full.startsWith(sessionPath)) return null;
  if (!fs.existsSync(full)) return null;
  return enqueueThumb(() => makeThumb(full, width || 260));
});

/** 仅取原图尺寸（用于悬浮预览按比例自适应，无图，比 get-thumb 快）。
    内部缓存，命中即同步返回。 */
ipcMain.handle('get-thumb-meta', (e, sessionPath, rel) => {
  const full = path.join(sessionPath, rel);
  if (!full.startsWith(sessionPath)) return { ow: 0, oh: 0 };
  if (!fs.existsSync(full)) return { ow: 0, oh: 0 };
  return getOriDim(full);
});

ipcMain.handle('save-ratings', (e, ratings) => {
  const cfg = loadConfig();
  cfg.ratings = ratings || {};
  saveConfig(cfg);
  return true;
});

/**
 * 从 _选片.html 迁移已有评级。
 * 旧工具把评级存在浏览器 localStorage，结构 rated = { 场次名: { "图.jpg": star } }
 * （star≥1 中选、0 略过、无 key 未评）。SV 导出成 libRoot/_ratings_import.json 后，
 * 这里读取并把中选(≥1)记录合并进选片台 ratings（扁平 key：场次名||文件名）。
 * 略过(0)无归档价值，不迁移，等同重新未评。
 */
ipcMain.handle('import-ratings', (e, libRoot) => {
  // 兼容旧工具导出/约定两种文件名
  const cands = ['_选片评级.json', '_ratings_import.json'];
  let file = null, filePath = null;
  for (const c of cands) {
    const p = path.join(libRoot || '', c);
    if (fs.existsSync(p)) { file = c; filePath = p; break; }
  }
  if (!filePath) {
    return { ok: false, error: '照片库根未找到评级文件(_选片评级.json)\n请在旧 _选片.html 页面点右上角“导出”按钮，把下载的 json 放到这里。' };
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (err) {
    return { ok: false, error: '评级文件(' + file + ')解析失败: ' + err.message };
  }
  // 兼容两种壳：直接 rated 对象，或 {app, rated}
  const rated = (data && typeof data === 'object' && data.rated && typeof data.rated === 'object')
    ? data.rated : data;
  if (!rated || typeof rated !== 'object') {
    return { ok: false, error: '评级文件里没有 rated 数据' };
  }

  // 真实场次：用于场次名解析。旧工具的 JSON 场次 key 常是“照片库”之类(选根时把图放根下)，
  // 与磁盘“场次=一级子目录名”对不上。这里按【文件名实际所在目录】归到真实场次，
  // 保证星标正确挂到图：key = 真实场次名||文件名。
  const realSess = scanSessions(libRoot);      // [{name,path}]
  const byFile = {};                            // 文件名(全大写目录内容) -> 真实场次名
  for (const s of realSess) {
    let names = [];
    try { names = fs.readdirSync(s.path); } catch (e) { continue; }
    for (const n of names) if (!(n in byFile)) byFile[n] = s.name;
  }
  const nameIsReal = (n) => realSess.some((s) => s.name === n);

  const cfg = loadConfig();
  const cur = cfg.ratings || {};
  let imported = 0, skipped = 0, unmapped = 0;
  for (const sess of Object.keys(rated)) {
    const rm = rated[sess];
    if (!rm || typeof rm !== 'object') continue;
    for (const fname of Object.keys(rm)) {
      const star = rm[fname];
      if (typeof star !== 'number' || isNaN(star)) continue;
      if (star < 1) { skipped++; continue; }      // 0/略过 不迁移
      // 决定真实场次名：JSON 场次名是真实场次则直接用；否则按文件归属；找不到则跳过
      const realName = nameIsReal(sess) ? sess : (byFile[fname] || null);
      if (!realName) { unmapped++; continue; }
      const k = realName + '||' + fname;
      if (!(k in cur) || (cur[k] || 0) < star) cur[k] = star;  // 不覆盖已有更高/相同分
      imported++;
    }
  }
  const before = Object.keys(cfg.ratings || {}).length;
  cfg.ratings = cur;
  saveConfig(cfg);
  const after = Object.keys(cur).length;
  return { ok: true, imported, skipped, unmapped, before, after };
});

/**
 * 读取图片文件，转成 file 协议 URL 给前端显示。
 * 大图直接读原图（Electron 无 file:// 限制）。
 */
ipcMain.handle('read-image', (e, sessionPath, rel) => {
  const full = path.join(sessionPath, rel);
  if (!full.startsWith(sessionPath)) return null;
  if (!fs.existsSync(full)) return null;
  return 'file:///' + full.replace(/\\/g, '/');
});

/**
 * 星级目录同步（归位）：让每张片在主题内的物理形态与当前星级一致。
 * ★ root 原图（JPG+RAF）永不移动/删除；星级桶里只放 JPG（RAF 留 root 供 LR 精选）。
 *   star=1    ：作废三个成片桶同名文件（旧成片重调）+ 确保 初筛1星 有直出 JPG 复制件
 *               （root 复制，RAF 不复制）
 *   star=2..5 ：成片在 待发布/2星/调色待验收 间移动到星级对应桶（≥3 → 待发布）；
 *               无成片可移时从 root 兑底复制直出 JPG；并清理 初筛1星 复制件（升级清阶）
 *   star<1    ：删除各星级桶同名文件（清星=不要了；root 原图不动）
 * items: [{ rel, star }]
 */
ipcMain.handle('archive-photos', (e, opts) => {
  const { themePath, items } = opts;
  const dirOf = (sub) => path.join(themePath, sub);
  const done = [];
  const failed = [];
  for (const it of items) {
    const rel = it.rel;
    const star = it.star || 0;
    try {
      /* ★累积桶模型（2026-09-06 定稿）：
         星级决定哪些桶非空——★≥1 → 初筛1星(直出复制件)；★≥2 → 2星桶(成片)；★≥3 → 待发布(成片)。
         升级=往高桶复制新增（低桶保留）；降级=删掉高桶里的（低桶产物无损保留）。
         全程只有复制与删除，绝不移动；root 直出与 RAF 永不动。
         调色待验收 = 调色管线输出暂存，星级同步永不动它（重置调色才清）。 */
      // 1) 应在的桶 → 补齐
      if (star >= 1) {
        const s1 = path.join(dirOf(STAR1_DIR), rel);
        if (!fs.existsSync(s1)) {
          const src = path.join(themePath, rel);
          if (!fs.existsSync(src)) { failed.push({ file: rel, error: 'root 原图不存在' }); continue; }
          fs.mkdirSync(dirOf(STAR1_DIR), { recursive: true });
          fs.copyFileSync(src, s1);
        }
      }
      if (star >= 2) {
        // 成片来源优先级：待发布 > 2星桶 > 调色待验收（已有的优先，无则必须有调色输出）
        let gradedSrc = null;
        for (const sub of [PUBLISH_DIR, STAR2_DIR, REVIEW_DIR]) {
          const f = path.join(dirOf(sub), rel);
          if (fs.existsSync(f)) { gradedSrc = f; break; }
        }
        if (!gradedSrc) {
          // ★无成片绝不拿 root 直出兜底——直出冒充成片会污染整个验收链
          failed.push({ file: rel, error: '无调色成片（先跑调色，成片进 调色待验收 后再打 ≥2 星）' });
          continue;
        }
        const wantSubs = star >= 3 ? [STAR2_DIR, PUBLISH_DIR] : [STAR2_DIR];
        for (const sub of wantSubs) {
          const f = path.join(dirOf(sub), rel);
          if (!fs.existsSync(f)) {
            fs.mkdirSync(dirOf(sub), { recursive: true });
            fs.copyFileSync(gradedSrc, f);
          }
        }
      }
      // 2) 不应在的桶 → 删除（REVIEW 不在 ARCHIVED_SUBDIRS，永不被星级清理）
      const should = {
        [STAR1_DIR]: star >= 1,
        [STAR2_DIR]: star >= 2,
        [PUBLISH_DIR]: star >= 3
      };
      for (const sub of ARCHIVED_SUBDIRS) {
        if (should[sub]) continue;
        const f = path.join(dirOf(sub), rel);
        if (fs.existsSync(f)) fs.unlinkSync(f);
      }
      done.push(rel);
    } catch (err) {
      failed.push({ file: rel, error: err.message });
    }
  }
  return { ok: true, done: done.length, failed };
});

/* 原生确认弹窗（重置调色等危险操作用）。取消是默认按钮，回车/Esc 都安全。 */
ipcMain.handle('confirm-dialog', (e, opts) => {
  const o = opts || {};
  const res = dialog.showMessageBoxSync(win, {
    type: 'warning',
    buttons: [o.cancelLabel || '取消', o.okLabel || '确认'],
    defaultId: 0,
    cancelId: 0,
    title: o.title || '确认',
    message: o.message || '',
    detail: o.detail || ''
  });
  return res === 1;
});

/* 重置调色：清空调色待验收 / 调色后满意的2星 / 待发布 三个目录里的全部 JPG。
   ★只删 IMG_EXT 匹配的 JPG——RAF/xmp/其他文件一律不碰；主题根原图与 初筛1星 不动。
   （换调色思路重跑调色管线前用；评分重置由 renderer 侧完成） */
ipcMain.handle('reset-color-grade', (e, themePath) => {
  let removed = 0;
  const errors = [];
  for (const sub of [REVIEW_DIR, STAR2_DIR, PUBLISH_DIR]) {
    const d = path.join(themePath, sub);
    if (!fs.existsSync(d)) continue;
    let files = [];
    try { files = fs.readdirSync(d); } catch (err) { continue; }
    for (const f of files) {
      if (!IMG_EXT.test(f)) continue;   // 非 JPG 一律跳过（RAF 完全不动）
      try {
        fs.unlinkSync(path.join(d, f));
        removed++;
      } catch (err) {
        errors.push(sub + '/' + f + ': ' + err.message);
      }
    }
  }
  return { ok: true, removed, errors };
});


/* =========================================================
   EXIF 解析 —— 纯 Node 手写，零第三方依赖。
   读取 JPEG 文件头部的 APP1(Exif) 段，解析 TIFF IFD，
   提取关键拍摄参数。只在主进程跑一次文件读取 + 字节解析，
   配磁盘缓存(文件名+修改时间做 key)，避免逐张重读。
   ========================================================= */
const EXIF_CACHE = new Map(); // rel -> {mtime, data}
const EXIF_CACHE_MAX = 2000;

function readJPEGBuffer(full) {
  // JPEG 头部段结构紧凑，前 64KB 足够容纳 EXIF(APP1) + 缩略图偏移
  const fd = fs.openSync(full, 'r');
  try {
    const buf = Buffer.alloc(65536);
    const n = fs.readSync(fd, buf, 0, 65536, 0);
    return buf.subarray(0, n);
  } finally {
    fs.closeSync(fd);
  }
}

/* 找一个 EXIF APP1 段，返回其 TIFF 起始 offset（DataView 是 JS 的，这里用 Buffer 自己解析） */
function findExifSegment(buf) {
  if (buf.length < 8 || buf.readUInt16BE(0) !== 0xffd8) return -1;
  let off = 2;
  while (off + 4 <= buf.length) {
    if (buf[off] !== 0xff) { off++; continue; }
    const marker = buf.readUInt16BE(off);
    if (marker === 0xffda) break;                 // SOS
    if (marker === 0xffd8 || (marker >= 0xffd0 && marker <= 0xffd7)) { off += 2; continue; }
    const segLen = buf.readUInt16BE(off + 2);
    if (segLen < 2) break;
    if (marker === 0xffe1 && segLen > 8 &&
        buf.readUInt32BE(off + 4) === 0x45786966 &&   // "Exif"
        buf.readUInt16BE(off + 8) === 0) {
      return off + 10;   // 跳过 "Exif\0\0"
    }
    off += 2 + segLen;
  }
  return -1;
}

/* TIFF：从 tiffStart 开始。小端 Intel(II) 为标准。返回 tag 值映射。 */
function parseTIFF(buf, tiffStart) {
  const endian = buf.readUInt16BE(tiffStart);        // 0x4949=II, 0x4d4d=MM
  const le = endian === 0x4949;
  const u16 = (o) => (le ? buf.readUInt16LE(o) : buf.readUInt16BE(o));
  const u32 = (o) => (le ? buf.readUInt32LE(o) : buf.readUInt32BE(o));
  const i16 = (o) => (le ? buf.readInt16LE(o) : buf.readInt16BE(o));
  const i32 = (o) => (le ? buf.readInt32LE(o) : buf.readInt32BE(o));
  const rat = (o) => {
    const num = u32(o), den = u32(o + 4);
    return den === 0 ? 0 : num / den;
  };
  if (buf.length < tiffStart + 8) return {};
  if (u16(tiffStart + 2) !== 42) return {};          // magic 42
  const ifd0 = tiffStart + u32(tiffStart + 4);
  const out = {};
  const tags = { 0x010f: 'make', 0x0110: 'model', 0x0112: 'orientation',
                 0x829a: 'exp', 0x829d: 'fnum', 0x8827: 'iso', 0x9003: 'dt',
                 0x9204: 'bias', 0x9209: 'flash', 0x920a: 'focal', 0xa002: 'pxX', 0xa003: 'pxY',
                 0xa403: 'wb', 0xa408: 'k' };
  const strTag = { 0x010f: 1, 0x0110: 1, 0x9003: 1 };
  const readIfd = (start, depth) => {
    if (depth > 2 || start + 2 > buf.length) return;
    const n = u16(start);
    for (let i = 0; i < n; i++) {
      const e = start + 2 + i * 12;
      if (e + 12 > buf.length) break;
      const tag = u16(e), type = u16(e + 2), count = u32(e + 4);
      if (tag === 0x8769 && type === 4) {             // ExifSubIFD
        if (depth === 0) readIfd(tiffStart + u32(e + 8), depth + 1);
        continue;
      }
      if (tag === 0xa434 && type === 2) {             // LensModel 字符串，可能超过4字节
        const off = tiffStart + u32(e + 8);
        const len = Math.min(count, buf.length - off);
        if (len > 0) out.lens = buf.toString('utf8', off, off + len).replace(/\0.*$/, '');
        continue;
      }
      if (!(tag in tags)) continue;
      const key = tags[tag];
      const p = type === 3 ? u16(e + 8) : type === 4 ? u32(e + 8) : tiffStart + u32(e + 8);
      if (strTag[tag]) {
        const len = Math.min(count, buf.length - p);
        if (len > 0) out[key] = buf.toString('utf8', p, p + len).replace(/\0.*$/, '');
      } else if (type === 5) {                        // RATIONAL (曝光/光圈/焦距/偏置)
        out[key] = rat(p);
      } else if (type === 3) {
        out[key] = key === 'wb' ? u16(e + 8) : u16(e + 8);
        if (key === 'bias') out[key] = i16(e + 8);
      } else {
        out[key] = p;
      }
    }
  };
  readIfd(ifd0, 0);
  return out;
}

const EXIF_LABELS = { 0: '自动', 1: '日光', 2: '阴天', 3: '白炽灯', 4: '荧光灯',
                      5: '闪光', 6: '晴天', 7: '云端', 8: '阴影', 9: '自定义', 10: '色温' };

function humanizeExif(raw) {
  const o = {};
  if (raw.make) o.camera = raw.make.trim() + (raw.model ? ' ' + raw.model.trim() : '');
  if (raw.lens) o.lens = raw.lens.trim();
  if (raw.focal) o.focal = raw.focal.toFixed(1) + ' mm';
  if (raw.fnum) o.aperture = 'ƒ/' + (Math.round(raw.fnum * 100) / 100).toFixed(1);
  if (raw.exp) o.shutter = formatShutter(1 / raw.exp);
  if (raw.iso) o.iso = 'ISO ' + raw.iso;
  if (raw.bias !== undefined) o.bias = (raw.bias > 0 ? '+' : '') + raw.bias.toFixed(2) + ' EV';
  if (raw.flash !== undefined) o.flash = raw.flash ? '开' : '关';
  if (raw.wb !== undefined) o.wb = EXIF_LABELS[raw.wb] || ('模式 ' + raw.wb);
  if (raw.k) o.k = raw.k + ' K';
  if (raw.dt) o.time = raw.dt.replace(/^(\d{4}):(\d{2}):(\d{2})/, '$1-$2-$3');
  if (raw.pxX && raw.pxY) o.size = raw.pxX + ' × ' + raw.pxY;
  return o;
}

function formatShutter(sec) {
  if (sec < 0.001) return '1/' + Math.round(1 / sec);
  return sec.toFixed(1) + 's';
}

/** 读取一张 JPG 的 EXIF 并转为人类可读参数对象（带内存缓存） */
function readExif(sessionPath, rel) {
  const full = path.join(sessionPath, rel);
  if (!full.startsWith(sessionPath)) return null;
  if (!fs.existsSync(full)) return null;
  const st = fs.statSync(full);
  const key = sessionPath + '||' + rel;
  const hit = EXIF_CACHE.get(key);
  if (hit && hit.mtime === st.mtimeMs) return hit.data;
  try {
    const buf = readJPEGBuffer(full);
    const ts = findExifSegment(buf);
    let data = {};
    if (ts >= 0) data = humanizeExif(parseTIFF(buf, ts));
    EXIF_CACHE.set(key, { mtime: st.mtimeMs, data });
    if (EXIF_CACHE.size > EXIF_CACHE_MAX) {
      const first = EXIF_CACHE.keys().next().value;
      EXIF_CACHE.delete(first);
    }
    return data;
  } catch (e) {
    return null;
  }
}

ipcMain.handle('get-exif', (e, sessionPath, rel) => {
  return readExif(sessionPath, rel);
});


/* =========================================================
   调色台侧 —— svFilm 引擎客户端
   ---------------------------------------------------------
   边界（README 的契约）：**svFilm 不许有界面，svStudio 不许有算法**。
   台子只做三件事：① 把用户点选的片子交给引擎 ② 把滑杆的值原样转过去
   ③ 把引擎吐出来的图显示出来。**所有调色数学都在 svFilm 那边**，
   这里一个像素都不算。

   引擎是**独立的常驻进程**（`python -m svFilm.service`），这里只当 HTTP 客户端：
   好处是台子崩了/重启了，引擎的解码缓存还在，不用重跑十几秒。
   服务没起时所有调用统一回落 `{ok:false, error}`，前端显示提示而不是卡死。
   ========================================================= */
const ENGINE = { host: '127.0.0.1', port: 8765, base: 'http://127.0.0.1:8765' };

function engineGet(pathname, timeoutMs) {
  return new Promise((resolve) => {
    let u;
    try { u = new URL(pathname, ENGINE.base); } catch (e) { return resolve({ ok: false, error: 'URL 非法' }); }
    const req = http.get({ host: u.hostname, port: u.port, path: u.pathname + u.search }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        if (res.statusCode >= 400) {
          let msg = 'HTTP ' + res.statusCode;
          try { const j = JSON.parse(buf.toString('utf8')); if (j && j.error) msg = j.error; } catch (e2) { /* 非 json */ }
          return resolve({ ok: false, error: msg, status: res.statusCode });
        }
        const ct = String(res.headers['content-type'] || '');
        if (/^image\//.test(ct)) {
          return resolve({ ok: true, image: 'data:' + ct + ';base64,' + buf.toString('base64') });
        }
        try { return resolve({ ok: true, data: JSON.parse(buf.toString('utf8')) }); }
        catch (e3) { return resolve({ ok: false, error: '引擎返回的不是 JSON' }); }
      });
    });
    req.on('error', (err) => {
      resolve({ ok: false, error: '引擎没在跑（' + (err.code || err.message) + '）', offline: true });
    });
    if (timeoutMs) req.setTimeout(timeoutMs, () => { req.destroy(); resolve({ ok: false, error: '引擎超时' }); });
  });
}

ipcMain.handle('engine-health', () => engineGet('/health', 1500));

/** 一键把引擎拉起来（本机、后台、不弹窗）。已在跑则直接返回健康状态。 */
ipcMain.handle('engine-start', async () => {
  const alive = await engineGet('/health', 1200);
  if (alive.ok) return { ok: true, already: true, data: alive.data };
  const py = enginePy();
  if (!fs.existsSync(py)) {
    return { ok: false, error: '找不到引擎用的 Python：' + py };
  }
  try {
    ensureEngineLog();
    const child = spawn(py, ['-u', '-m', 'svFilm.service', '--port', String(ENGINE.port)], {
      cwd: ENGINE_CWD,
      detached: true,
      /* ★ 09-15：原来是 stdio:'ignore' —— 引擎起不来时**一点线索都没有**。
         改成把 stdout/stderr 追加到日志文件，下次失败直接看文件。 */
      stdio: ['ignore', fs.openSync(engineLogFile(), 'a'), fs.openSync(engineLogFile(), 'a')],
      windowsHide: true
    });
    child.on('error', (err) => {
      fs.appendFileSync(engineLogFile(), '\n[spawn error] ' + (err && err.message) + '\n');
    });
    child.on('exit', (code) => {
      fs.appendFileSync(engineLogFile(), '\n[child exit] code=' + code + '\n');
    });
    child.unref();
    fs.appendFileSync(
      engineLogFile(),
      '\n[spawn] pid=' + child.pid + ' cwd=' + ENGINE_CWD + ' py=' + py + '\n'
    );
  } catch (err) {
    return { ok: false, error: '启动失败：' + err.message };
  }
  // 引擎要 import 一堆东西（含模型），给几秒；轮询到通为止
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const h = await engineGet('/health', 1200);
    if (h.ok) return { ok: true, started: true, seconds: (i + 1) * 0.5, data: h.data };
  }
  return { ok: false, error: '引擎启动了但 20 秒内没响应（看引擎日志：' + engineLogFile() + '）' };
});

ipcMain.handle('engine-stocks', async () => {
  const r = await engineGet('/stocks', 4000);
  return r.ok ? { ok: true, items: r.data } : r;
});
ipcMain.handle('engine-bases', async () => {
  const r = await engineGet('/bases', 4000);
  return r.ok ? { ok: true, items: r.data } : r;
});
ipcMain.handle('engine-params', async () => {
  const r = await engineGet('/params', 4000);
  return r.ok ? { ok: true, items: r.data } : r;
});
ipcMain.handle('engine-scan', async (e, dir, exts, limit) => {
  const qs = '?dir=' + encodeURIComponent(dir || '') +
    '&ext=' + encodeURIComponent(exts || 'raf,jpg') +
    '&limit=' + encodeURIComponent(String(limit || 400));
  const r = await engineGet('/scan' + qs, 20000);
  return r.ok ? { ok: true, files: r.data.files, n: r.data.n } : r;
});
ipcMain.handle('engine-load', async (e, paths) => {
  const qs = '?paths=' + encodeURIComponent((paths || []).join(','));
  // 解码一张 RAW 约 2~3 秒，一次可多张 ⇒ 给足时间
  const r = await engineGet('/load' + qs, 180000);
  return r.ok ? { ok: true, items: r.data } : r;
});
/** 「原图」栏：引擎缓存里的 Sample 直接出图（恒等，不跑调色）。
    ★ 不再让前端去读原始 JPG —— 那样出来的尺寸/方向和渲染结果不是一把尺子。 */
ipcMain.handle('engine-base', async (e, id) => {
  const r = await engineGet('/base?id=' + encodeURIComponent(String(id)) + '&fmt=jpg&q=90', 60000);
  return r.ok ? { ok: true, image: r.image } : r;
});

/** 把滑杆参数转成引擎认的那一串：`KEY:VAL,KEY:VAL`。
 *
 *  ★★ 09-15 修一个**静默到极点**的 bug（SV 报「调滑杆点渲染没反应」的根因）：
 *    前端 `grade.params` 是**对象** `{ SPEK_PE_SHIFT: 0.91 }`（见 GradePanel 的 setGrade），
 *    而这里原来直接 `encodeURIComponent(o.params || '')` —— 对对象做 encodeURIComponent
 *    会先 ToString，结果是 `%5Bobject%20Object%5D`；引擎那边 `_parse_params` 按 `KEY:VAL`
 *    切分、切不出冒号就**静默丢掉**（契约就是"不合法不报错"）⇒ 解出空字典 `{}`。
 *    实测（`_probe_wire_e2e.py`，真起服务发真 HTTP）：
 *        params=[object Object]  → 中位亮度 41.76（与"不传参数"**逐位一样**）
 *        params=SPEK_PE_SHIFT:0.91,ENTERYSETTLE...,→ 36.70（−5.06 L*）
 *    ⇒ 也就是说：**23 根滑杆一根都没接上**，出图永远是"出厂值"那一张。
 *      ⚠ 上一轮的自检只验到"引擎收得下参数"，没验到"前端发得出参数" —— 闸挡住了 ≠ 没脸。
 *
 *  收口在这里（IPC 边界）而不是前端：这样以后不管谁调 `engine-render` 都不会再踩。
 *  字符串照原样透传（留给脚本风格的 A/B 调用），非数字/NaN 一律丢掉（别污染 float() 解析）。
 */
function paramStr(p) {
  if (!p) return '';
  if (typeof p === 'string') return p;
  if (typeof p !== 'object') return '';
  const out = [];
  for (const k of Object.keys(p)) {
    const v = p[k];
    if (typeof v !== 'number' || !isFinite(v)) continue;
    out.push(k + ':' + v);
  }
  return out.join(',');
}

ipcMain.handle('engine-render', async (e, id, opts) => {
  const o = opts || {};
  const qs = '?id=' + encodeURIComponent(String(id)) +
    '&stock=' + encodeURIComponent(o.stock || '') +
    '&base=' + encodeURIComponent(o.base || '') +
    '&side=' + encodeURIComponent(String(o.side || 700)) +
    '&fmt=jpg&q=' + encodeURIComponent(String(o.q || 92)) +
    '&params=' + encodeURIComponent(paramStr(o.params));
  // 换卷 6~7 秒，首次含模型加载更久
  const r = await engineGet('/render' + qs, 300000);
  return r.ok ? { ok: true, image: r.image } : r;
});

/** 原图的「已按方向转正」预览（给调色台左栏做 before 用）。
    走引擎的 /load 拿 Sample.disp —— 和渲染结果同一把尺子、同一分辨率，
    比直接开原图更公平（原图可读但方向/尺寸口径不一致，会误导 A/B）。 */
ipcMain.handle('engine-raw-url', async (e, sessionPath, rel) => {
  const full = path.join(sessionPath, rel);
  if (!full.startsWith(sessionPath) || !fs.existsSync(full)) {
    return { ok: false, error: '文件不存在' };
  }
  const r = await engineGet('/load?paths=' + encodeURIComponent(full), 180000);
  return r.ok ? { ok: true, image: r.image } : r;
});

/* ---- 调色参数「按主题存」：一个主题一份，存进 config.grades[主题名] ---- */
ipcMain.handle('get-grade', (e, themeName) => {
  const cfg = loadConfig();
  const g = (cfg.grades || {})[themeName];
  return g || null;
});

ipcMain.handle('set-grade', (e, themeName, grade) => {
  if (!themeName) return false;
  const cfg = loadConfig();
  cfg.grades = cfg.grades || {};
  cfg.grades[themeName] = grade || {};
  saveConfig(cfg);
  return true;
});

/** 导出「选片台 + 调色台」的全部设置成一份 json（换机器/备份用）。 */
ipcMain.handle('export-grade', async (e, payload) => {
  const res = await dialog.showSaveDialog(win, {
    title: '导出工作台设置',
    defaultPath: 'svStudio_设置.json',
    filters: [{ name: 'JSON', extensions: ['json'] }]
  });
  if (res.canceled || !res.filePath) return { ok: false, canceled: true };
  try {
    fs.writeFileSync(res.filePath, JSON.stringify(payload, null, 2), 'utf8');
    return { ok: true, path: res.filePath };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

