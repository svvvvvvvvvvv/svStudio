const { app, BrowserWindow, ipcMain, dialog, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const http = require('http');
const { spawn } = require('child_process');
/* ★ 子进程输出要用它把"一个汉字被切在两个 chunk 之间"接回来（否则进度行上出乱码） */
const { StringDecoder } = require('string_decoder');

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
    // ★★ 09-15 SV 选「B」：**「图库根」这一层已经砍掉**（`libRoot` 键不再写入）。
    //   左栏列的就是「你加过的文件夹」（`extraRoots`），不再是"先设一个大库、库底下分主题"。
    //   老配置里的 `libRoot` 由下面的 `migrateConfig` 一次性搬进 `extraRoots`，不会凭空消失。
    //   ⚠ 也别再把某个人的绝对路径写进仓库。
    // ★ 引擎用哪份 Python（绝对路径）。留空 = 用 SVFILM_PY / PATH 上的 python
    enginePy: '',
    // ★ 调试产出根（日志/实验中间结果）。留空 = 应用自己的 userData/_debug
    debugDir: '',
    // ★ 跑「纯 RAW 目录的预览索引」用哪份 Python（要能 import rawpy + PIL）。
    //   留空 = 跟引擎共用那份（引擎本来就依赖 rawpy，正常不用配）。
    extIndexPy: '',
    // ★★ 「加入目录…」加进来的**库外目录**（硬盘上已有的照片文件夹，原地读、不复制一份）。
    //   扫描主题列表时和库内主题并排列出（见 `scanSessions`）；存的是绝对路径。
    //   ⚠ 这些目录里的照片**不会**被拷进照片库 —— 原目录被删/改名，它们就从列表里消失
    //     （那时列表里会留一条「找不到」，不静默消失）。
    extraRoots: [],
    lastSession: null,   // 上次进入的主题（下面两个平铺键由 src/store/useStore.ts 的 saveLast 写）
    lastCur: 0,          // 上次选到第几张（恢复时会按实际张数夹范围，防"主题变小了"）
    lastMode: 'pick',    // 上次在哪个台：'pick' 选片台 | 'grade' 调色台
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
      /* ★ 更早那版的「上次状态」键 —— 现在走平铺的 lastSession/lastCur/lastMode
         （见 src/store/useStore.ts 的 saveLast）。下面这几个**全仓库谁都不读**：
         `lastIdx` 的注释当年写着"重启后恢复"，但没有任何一处真的读它。
         留着只会让下一个人以为"读它就能恢复"（读到的还是几周前的 220）⇒ 清掉。 */
      delete cfg.lastIdx;
      delete cfg.lastFilter;
      delete cfg.mode;
      delete cfg.last;      // 老版 saveLast 存过的 {cur} 对象（后来改平铺键，这个就成孤儿了）
      migrateConfig(cfg);
      return cfg;
    }
  } catch (e) {
    console.error('config load failed', e);
  }
  return migrateConfig(defaultConfig());
}

/** 路径比较用（斜杠方向统一、去尾分隔符、大小写不敏感）—— Windows 上必须这么比。
 *  ⚠ 只用来**判等**，绝不拿它去读文件（大小写敏感的系统上会读不到）。
 *  ⚠ 光去尾斜杠不够：`d:/示例库/` 和 `D:\示例库` 是同一个目录，
 *     不把 `/` 统一成 `\` 就判成两个 ⇒ 同一个库在列表里出现两条（09-15 自检抓到的）。 */
function samePath(a, b) {
  const N = (p) => String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
  return N(a) === N(b);
}

/**
 * ★★ 09-15 SV 选「B」：「图库根」这一层砍掉了。
 *
 * 老版本存过 `cfg.libRoot`（先设一个大库，左栏列它的一级子目录当"主题"）。这套模型在
 * "我手上就是一个文件夹、照片直接躺在里面"的时候**直接崩** —— 库根自己列不出来
 * （`scanSessions` 只认子目录），而「加入目录」又因为"这就是库根自己"被挡掉 ⇒ 锁死。
 * ⇒ 现在只有一种东西：**你加过的文件夹**（`extraRoots`）。
 *
 * 但**不能让它凭空消失**：老配置里的 `libRoot` 要变成"加过的第一个目录"。
 * 这里**搬完就落盘**（`delete cfg.libRoot` ⇒ 下次不再触发，幂等）。
 */
function migrateConfig(cfg) {
  if (!cfg || !cfg.libRoot) return cfg;
  const lr = String(cfg.libRoot).trim();
  if (!Array.isArray(cfg.extraRoots)) cfg.extraRoots = [];
  if (lr && !cfg.extraRoots.some((x) => samePath(x, lr))) cfg.extraRoots.unshift(lr);
  delete cfg.libRoot;
  saveConfig(cfg);
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
 * ★★★ 「一层目录里有哪几张片」的**唯一口径**（09-15 SV 定：**片 = RAW**）。
 *
 * · **一张片 = 一个 RAW**（同名多形态只算一张）。旁边有同名 JPG 也**不影响** —— 那张 JPG
 *   不是另一张片，只是同一张片的另一个文件形态。
 * · **孤 JPG**（没有同名 RAW）**也算一张**（SV 09-15 选 ②A）：以前拍的纯 JPG 片不能凭空消失。
 * · 身份键 = `<stem>.JPG`（**故意写成 .JPG**）：它是**身份键**，磁盘上**不一定有这个文件**。
 *   星级的四个桶里放的是 JPG 复制件，要靠**同一个字符串**跟 root 原图合并成一条
 *   （`listPhotos` 的 `byRel`）⇒ 键必须保持这个形态。
 *   ⚠★ 改成 `.RAF` 会让桶合并失败（同一张片在列表里出现两条）而且**已有星级全部落空**。
 * · 孤 JPG 沿用**它自己的真实文件名**当键（和桶里的复制件对得上）。
 *
 * @returns {{keys: string[], hasRaw: boolean, count: number, rawOf: Map<string,string>}}
 *   `rawOf`：身份键 → 这一层里那个 RAW 的**真实文件名**（孤 JPG 查不到）。
 */
function photoFilesIn(files) {
  const raws = new Map();     // UPPER(stem) -> 真实 RAW 文件名
  const jpgs = new Map();     // UPPER(stem) -> 真实 JPG 文件名
  for (const f of files) {
    const s = f.replace(/\.[^.]+$/, '').toUpperCase();
    if (RAW_EXT.test(f)) { if (!raws.has(s)) raws.set(s, f); continue; }
    if (IMG_EXT.test(f)) { if (!jpgs.has(s)) jpgs.set(s, f); }
  }
  const keys = [];
  const rawOf = new Map();
  for (const pair of raws) {              // ① 每个 RAW 一张（哪怕旁边躺着同名 JPG）
    const key = pair[0] + '.JPG';
    keys.push(key);
    rawOf.set(key, pair[1]);
  }
  for (const pair of jpgs) {              // ② 孤 JPG（没有同名 RAW）也各算一张
    if (raws.has(pair[0])) continue;
    keys.push(pair[1]);
  }
  keys.sort();
  return { keys: keys, hasRaw: raws.size > 0, count: keys.length, rawOf: rawOf };
}

/**
 * 数一个「照片目录」里有几张片（root + 四个桶，按**身份键**去重）。
 * 返回 `{count, archivedCount, hasRaw}`；**不像照片目录就返回 null**（RAW 和 JPG 都没有）。
 *
 * ★★ 09-15 SV 定「片 = RAW」之后判定口径也换了：
 *   · **有 RAW 就算照片目录**（哪怕一张 JPG 都没有 —— 以前这种目录会被当成"空的"）；
 *   · 张数 = **片的身份键数**，不是 JPG 文件数（口径见 `photoFilesIn`）；
 *   · `opts.allowRawOnly` / `rawOnly` 两个概念**已经删掉** —— 不再有"只有纯 RAW 才走"的
 *     那条岔路，所有目录走同一条（缩略图一律从 RAW 抠，见 `viewFileOf`）。
 * @param {string} full 目录绝对路径
 * @param {string[]} [names] 已经 readdir 过的名字（省一次系统调用）
 */
function describeSessionDir(full, names) {
  let list = names;
  if (!list) {
    try {
      list = fs.readdirSync(full);
    } catch (e) {
      return null;
    }
  }
  const own = photoFilesIn(list);
  const subNames = list.filter((f) => THEME_SUBDIRS.includes(f));
  if (!own.count && !subNames.length) return null;   // 不是照片目录
  // count 按**身份键**去重：桶里多是 root 同名复制件/成片，重复计数会虚高
  const rootKeys = own.keys;
  let count = own.count;
  let archivedCount = 0;
  let hasRaw = own.hasRaw;
  for (const sub of THEME_SUBDIRS) {
    const sd = path.join(full, sub);
    if (!fs.existsSync(sd)) continue;
    let sfiles = [];
    try { sfiles = fs.readdirSync(sd); } catch (err) { sfiles = []; }
    const sOwn = photoFilesIn(sfiles);
    count += sOwn.keys.filter((k) => rootKeys.indexOf(k) < 0).length;
    if (sOwn.hasRaw) hasRaw = true;
    if (ARCHIVED_SUBDIRS.includes(sub)) archivedCount += sOwn.count;
  }
  return { count, archivedCount, hasRaw };
}

/* =========================================================
   照片目录的**预览索引**（每个目录都建 —— 09-15 SV 定「片 = RAW」）
   ---------------------------------------------------------
   ⚠ 起因：工作台以前列图**只按 JPG 列**（`IMG_EXT`），RAW 只当"这张有 RAF"的角标
     ⇒ 一张 JPG 都没有的目录在界面上是**空的**；更阴的是混着 JPG 的目录里，
     那些"没有同名 JPG"的 RAW **根本不出现**（连角标都没有）。这正是最烦的那类
     "看着对、其实对不上"：不止没报错，连个可疑的地方都没有。
   ★ 解法：**每张 RAW 都配一张预览小图** —— 把相机自带的机内 JPG 抠出来、
     缩到长边 1600，存进应用缓存目录（`%APPDATA%\svstudio\extpreview\`）。
     实测 3~4 ms/张、约 300 KB/张（700 张的目录 ≈ 200 MB）。
   ★★ 09-15 起它**不是"纯 RAW 目录专属的岔路"了**：所有目录一视同仁，
     缩略图/大图/EXIF 一律从这份索引取（见 `viewFileOf`）
     ⇒ **"目录里有没有 JPG"从此不影响任何事**。
     （抠内嵌 JPG 只是从文件里取现成的一段；真解码一张 4400 万像素要好几秒。）
   ★★ 源目录**一个字节都不动**（脚本只读，只往缓存写）；缓存可以整个删，下次自动重做。
   ★★ 出图（渲染）**永远用源目录里的 RAW 原件** —— 见 `attachLoadPath`。
     缓存里全都是 1600 的缩略图，拿它渲染＝能出图、界面也不报错，画质悄悄掉了。
     （所以看图走 `viewFileOf` 读缓存、出图走 `attachLoadPath` 读原件，两条链**分开**。）
   ★ 移除目录时**缓存留着**：下次加回来不用重转（反正缓存在应用目录里，不会脏他的盘）。
   ========================================================= */

/** 预览索引的根：应用缓存目录（**不写死任何本机路径、不进他的照片目录**，要开源） */
let _extIndexRoot = null;
function extIndexRoot() {
  if (!_extIndexRoot) {
    _extIndexRoot = path.join(app.getPath('userData'), 'extpreview');
    try {
      fs.mkdirSync(_extIndexRoot, { recursive: true });
    } catch (e) { /* ignore */ }
    /* 放一份人话说明：这个文件夹是**缓存**，删了不影响任何照片 */
    try {
      const note = path.join(_extIndexRoot, '_说明.txt');
      const txt = [
        '这个文件夹是什么：',
        '',
        '  它是 svStudio 的**缓存**，专门装「加入目录…」挂进来的那些',
        '  每张 RAW 的预览小图（把相机自带的机内 JPG 抠出来、缩到长边 1600）。',
        '  工作台看图就靠它 —— 目录里有没有 JPG 都不影响。',
        '',
        '可以删吗：',
        '',
        '  可以。整个文件夹删掉都行 —— 你的照片一个字节都没动过，',
        '  （照片还在原来的目录里），下次把目录加进来会自动重做一遍。',
        '',
        '为什么占地方：',
        '',
        '  约 300 KB/张。700 张的目录大概 200 MB。',
        '',
      ].join('\r\n');
      let old = '';
      try { old = fs.readFileSync(note, 'utf8'); } catch (e2) { old = ''; }
      if (old !== txt) fs.writeFileSync(note, txt, 'utf8');
    } catch (e) { /* 说明写不进去不影响功能 */ }
  }
  return _extIndexRoot;
}

/** 源目录 → 它的索引目录（名字里带目录名 + 8 位哈希，一眼能看出对的是谁、也绝不撞名） */
function extIndexDirFor(srcDir) {
  const norm = String(srcDir).replace(/[\\/]+$/, '');
  const h = crypto.createHash('sha1')
    .update(norm.replace(/\\/g, '/').toLowerCase())
    .digest('hex')
    .slice(0, 8);
  const base = (path.basename(norm) || 'dir').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 24);
  return path.join(extIndexRoot(), base + '@' + h);
}

/** 源目录里**每一张 RAW** 的 stem（大写、去重）。
 *  ★★ 09-15 SV 定「预览一律从 RAW 抠」⇒ **每张 RAW 都必须有索引**，
 *     不再"有同名 JPG 的就跳过" —— 那个跳过让"目录里有没有 JPG"影响了能不能看图，
 *     正是这一轮要清掉的东西（混着 JPG 的目录里，纯 RAW 那几张会整片消失）。
 *  返回 `null` = 目录读不到（和"空数组"不是一回事）。 */
function indexStems(srcDir) {
  let files = [];
  try {
    files = fs.readdirSync(srcDir);
  } catch (e) {
    return null;
  }
  const out = [];
  for (const f of files) {
    if (!RAW_EXT.test(f)) continue;
    const s = f.replace(/\.[^.]+$/, '').toUpperCase();
    if (out.indexOf(s) < 0) out.push(s);
  }
  return out;
}

/** 这份索引够不够用：源目录里**每一张 RAW** 都在缓存里。
 *  ★ 逐张比 stem —— 不是比张数："张数一样但换了一批片"真的会发生。 */
function extIndexNeeds(srcDir) {
  const stems = indexStems(srcDir);
  const dir = extIndexDirFor(srcDir);
  if (!stems || !stems.length) {
    return { need: false, dir: dir, have: 0, want: 0, miss: 0, stems: stems || [] };
  }
  const have = new Set();
  try {
    for (const f of fs.readdirSync(dir)) {
      if (IMG_EXT.test(f)) have.add(f.replace(/\.[^.]+$/, '').toUpperCase());
    }
  } catch (e) { /* 索引还没建 ⇒ have 空 */ }
  const miss = stems.filter((s) => !have.has(s));
  /* ⚠ 09-15 起**不再校验 `_src.txt`**：那是"path 指向缓存"时代的出图源凭据，
     现在 path 就是源目录、出图直接在源目录里找 RAW ⇒ 这个目录是纯缩略图缓存，
     缺了就补齐、多了也无所谓。 */
  return {
    need: miss.length > 0,
    dir: dir, have: have.size, want: stems.length, miss: miss.length, stems: stems,
  };
}

/**
 * ★★★ 「**看**这张片，到底读磁盘上哪个文件」的**唯一出口**（09-15 定「片 = RAW」后新增）。
 *
 * 1) `dir/rel` 真的在 ⇒ 就是它。两种正当情况：
 *    · 桶里的成片（`初筛1星/DSCF0001.JPG` 那些）—— 要看的就是**产物**本身，不能拿原图顶替；
 *    · 孤 JPG（没有同名 RAW）—— 它自己就是原始素材。
 * 2) 否则 `dir` 里有同名 RAW ⇒ 原图只有 RAW ⇒ 读**预览索引缓存**里那份从 RAW 抠出来的
 *    机内 JPG（`extpreview/<目录名>@<hash8>/<stem>.JPG`）。
 *
 * ⚠ 缓存**不一定已经在**（刚加进来的目录、预建漏掉的那几张）⇒ 要图的地方用
 *   `ensureViewFile()`；只想知道"读哪个文件"的地方用本函数。
 * ⚠ **出图（喂引擎）不走这里** —— 那条走 `attachLoadPath`，要的是 RAW **原件**；
 *   拿 1600 的预览小图去渲染 = 能出图、不报错、画质悄悄掉了。
 *
 * @returns {{file: string, cache: boolean, raw: string}|null}
 *   `raw` = 同名 RAW 的绝对路径（没有就空串）。`cache:true` 表示 `file` 是索引缓存里那份。
 */
function viewFileOf(dir, rel) {
  const base = String(rel || '').split(/[\\/]/).pop();
  if (!base) return null;
  const stem = base.replace(/\.[^.]+$/, '').toUpperCase();
  let files = [];
  try { files = fs.readdirSync(dir); } catch (e) { return null; }
  let rawName = '';
  for (const f of files) {
    if (RAW_EXT.test(f) && f.replace(/\.[^.]+$/, '').toUpperCase() === stem) { rawName = f; break; }
  }
  const rawFull = rawName ? path.join(dir, rawName) : '';
  /* ★ 顺序是 **RAW 优先**（和 `attachLoadPath` 同一条规矩）：
     目录里既有同名 JPG 又有 RAW ⇒ 缩略图也从 RAW 抠 ⇒ **一张片永远只有一种缩略图来源**，
     不会出现"有的目录用 JPG、有的用缓存"两副面孔 —— 那正是 09-15 要清掉的东西。
     桶里的成片没有同名 RAW ⇒ 自然落到"读它自己"，要的就是产物本身。 */
  if (rawName) {
    /* ⚠ 缓存文件名用的是 RAW 的 stem **原样大小写**（脚本 `splitext(n)[0] + '.JPG'`）
       ⇒ 这里**不能**用大写化的 stem 去拼，否则小写扩展名的源会永远找不到缓存。 */
    return {
      file: path.join(extIndexDirFor(dir), rawName.replace(/\.[^.]+$/, '') + '.JPG'),
      cache: true, raw: rawFull,
    };
  }
  const direct = path.join(dir, base);
  try {
    if (fs.existsSync(direct) && fs.statSync(direct).isFile()) return { file: direct, cache: false, raw: '' };
  } catch (e) { /* 文件不在了 ⇒ 当没有 */ }
  return null;
}

/** `viewFileOf` 的**兜底版**：缓存里缺这一张就**就地抠一张**（起 python，只处理这一张）。
 *  预建索引漏掉 / 失败过 / 源目录后来多了新片 —— 都靠它兜。
 *  返回能读的文件绝对路径；实在拿不到返回 `null`（并把原因推给界面）。 */
async function ensureViewFile(dir, rel) {
  const v = viewFileOf(dir, rel);
  if (!v) return null;
  if (!v.cache) return v.file;
  try { if (fs.existsSync(v.file)) return v.file; } catch (e) { /* 往下走，重抠 */ }
  try {
    fs.mkdirSync(path.dirname(v.file), { recursive: true });
    const r = await spawnPy(extIndexPy(), extIndexScriptPath(),
      ['--one=' + v.raw, '--out-file=' + v.file], () => { });
    if (fs.existsSync(v.file)) return v.file;
    const tail = String((r && r.text) || '').trim().split('\n').pop() || (r && r.error) || '原因没返回';
    sendExtIndexProgress('预览小图没生成（' + path.basename(String(rel)) + '）：' + tail);
  } catch (e) {
    sendExtIndexProgress('预览小图没生成（' + path.basename(String(rel)) + '）：' + String(e));
  }
  return null;
}

/** 在 `used`（小写名字集合）里取一个**唯一**的名字，重了就加 ` ·2` ` ·3` */
function uniqueName(used, base) {
  let nm = base;
  let i = 2;
  while (used.has(nm.toLowerCase())) {
    nm = base + ' ·' + i;
    i += 1;
  }
  used.add(nm.toLowerCase());
  return nm;
}

/**
 * ★★★ 「加进来的那个目录 → 要列成哪几条」的**唯一规则**。
 *
 * 凡是**自己有片**的目录（`describeSessionDir` 不是 null）就列一条；
 * **并且继续往下看**（不是"自己有片就封顶"）。深度上限 **2 层**。
 *
 * ⚠ 为什么不能封顶：真实结构是 `照片库（自己没片）→ 爆光修复（4 张 RAW）→ x100vi（6 张 JPG）`。
 *   只展开一层 ⇒ `x100vi` 那 6 张**从列表里凭空消失**，而界面上一点迹象都没有。
 *   ⇒ **这条规则永远不让照片消失** —— 这是唯一不能妥协的一点。
 * ⚠ 跳过四个**星级桶目录**：那是主题**内部**的产物（`初筛1星/…`），不是另一个主题；
 *   往下钻会把每条主题的每个桶都列成一条。
 * ⚠ 深度 2 是权衡：再深就不是"一次拍摄"而是"目录层级"了，列出来只会把左栏淹掉。
 *   条数再兜一道 400 —— 万一把整个盘拖进来，也不至于把界面打死。
 * ⚠ 纯只读：只 `statSync` / `readdirSync`，不动任何文件。
 */
function collectPhotoDirs(dir, depth, used, rootDir, out) {
  if (out.length > 400) return;
  const self = describeSessionDir(dir);
  if (self) out.push(externalSessionEntry(dir, path.basename(dir) || dir, used, rootDir, self));
  if (depth <= 0) return;
  let entries = [];
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
  for (const e of entries) {
    if (!e.isDirectory() || e.name.startsWith('.') || THEME_SUBDIRS.includes(e.name)) continue;
    collectPhotoDirs(path.join(dir, e.name), depth - 1, used, rootDir, out);
  }
}

/**
 * 「加入目录…」加进来的目录 → 主题列表条目（原地读，**没有**复制进库）。
 *
 * ★ 一个根可能列成**好几条**（规则见 `collectPhotoDirs`）：
 *   自己就是照片目录 ⇒ 一条；底下还有"有片"的子目录 ⇒ 也各一条（最多 2 层）。
 * ⚠ 目录不在了（被删 / 改名）**不静默跳过** —— 列一条 `missing:true`，界面上打「找不到」
 *   且点不进去。静默消失是本项目最烦的那类"看着对、其实对不上"。
 * ⚠ 纯只读：只 `statSync` / `readdirSync`，不动任何文件。
 */
function extraSessions(used) {
  const out = [];
  let roots = [];
  try {
    roots = loadConfig().extraRoots;
  } catch (e) { /* 配置读不到就当没加过 */ }
  if (!Array.isArray(roots)) return out;
  for (const r0 of roots) {
    const dir = typeof r0 === 'string' ? r0.trim() : '';
    if (!dir) continue;
    let entries = null;
    try {
      if (fs.statSync(dir).isDirectory()) entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (e) { entries = null; }
    if (!entries) {
      out.push({
        name: uniqueName(used, path.basename(dir) || dir),
        path: dir, count: 0, external: true, rootDir: dir, missing: true,
      });
      continue;
    }
    /* ★★ 09-15 起展开规则收口到 `collectPhotoDirs`：自己有片也**不封顶**，
       免得"照片库 → 某次拍摄 → 子目录"这种结构丢照片（见它的注释）。 */
    collectPhotoDirs(dir, 2, used, dir, out);
  }
  return out;
}

/**
 * 加进来的目录 → 一条主题条目（`extraSessions` 的零件）。
 *
 * ★★ `path` **永远是源目录**（09-15 改）：以前"纯 RAW 目录"把 `path` 指向预览缓存，
 *    于是列图/缩略图/打星走缓存、出图源另开 `srcDir` + `_src.txt` 指回去 ——
 *    那条岔路删了。现在只有"**看**这张图"那一处会去缓存取（`viewFileOf`），
 *    列表 / 桶 / 星级 / EXIF 口径全部照旧。
 * ⚠ `needsIndex` 只是"界面该去建一次索引"的旗子（还没建 / 源目录又多了新片），不是错误。
 *    `rawCount` = 源目录里 RAW 的张数（= 这次要抠多少张预览小图）。
 */
function externalSessionEntry(full, name, used, rootDir, info) {
  const e = Object.assign({
    name: uniqueName(used, name),
    path: full, external: true, rootDir: rootDir,
  }, info);
  const st = extIndexNeeds(full);
  e.indexDir = st.dir;
  e.needsIndex = st.need;
  e.rawCount = st.want;
  e.indexMiss = st.miss;
  return e;
}

/**
 * 主题列表 = ① 库内主题（`libRoot` 的直接子目录）＋ ② 库外目录（「加入目录…」）。
 *
 * ⚠★ 每条都带 `path`（真实路径）。前端 `enterSession` **必须**用它 —— 库外目录不在
 *   `libRoot` 底下，靠"库根 + 名字"拼出来的路径根本不存在（进去就是 0 张）。
 * ⚠ 库内主题的名字**原样保留**：它是星级 / 成片归档 / 配方的身份键，改一个字符
 *   = 那个人几周的星级全丢。只有库外条目才会为了**不撞名**加 ` ·2`。
 * ⚠ 库内主题先入 `used` ⇒ 库外条目跟库内重名时，被改名的一定是**库外**那条。
 */
/**
 * 主题列表 = **你加过的所有文件夹**（`config.extraRoots`，规则见 `extraSessions`）。
 *
 * ★★ 09-15 SV 选「B」：**「图库根」那条路已经删掉**（原来是 `libRoot` 的直接子目录）。
 *    那套模型在"我手上就是一个文件夹、照片直接躺在里面"的时候直接崩 —— 库根自己列不出来、
 *    「加入目录」又因为"这就是库根自己"被挡掉，等于锁死，界面上还看不出来。
 *    现在只有一种东西：加过的文件夹。老配置里的 `libRoot` 由 `migrateConfig`
 *    一次性搬进 `extraRoots`，不会凭空消失。
 *
 * ⚠★ 每条都带 `path`（真实路径）。前端 `enterSession` **必须**用它 —— 再没有
 *   "库根 + 名字"这种能拼出来的东西了（拼出来的路径根本不存在，进去就是 0 张）。
 * ⚠ 条目的名字（`s.name`）是星级 / 成片归档 / 调色配方的**身份键**，改一个字符
 *   = 那个人几周的星级全丢。只有跟前面重名的那条才会为了不撞名加 ` ·2`。
 */
function scanSessions() {
  const out = [];
  const used = new Set();
  for (const s of extraSessions(used)) out.push(s);
  out.sort((a, b) => (a.name < b.name ? 1 : a.name > b.name ? -1 : 0));
  return out;
}

/**
 * 列出主题内所有照片（★★ **一张片 = 一个 RAW**；`rel` 是**身份键**，不是文件名）。
 * **看**这张图读哪个文件 → `viewFileOf`；**出图**喂哪个文件 → `attachLoadPath`（同名 RAW 优先）。
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
  attachLoadPath(sessionPath, photos);          // ★ 出图源：同名 RAW 优先
  photos.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  return photos;
}

/** 给每张照片补一个 `loadPath` = **出图时真正喂给引擎的那个文件**：同名 RAW 优先，没有才用 JPG。
 *  ★ 09-15 起 `rel` 是身份键（`<stem>.JPG`）而**不是**，所以这里必须靠**目录 + stem** 去找
 *    （以前纯 RAW 目录的 `path` 指向缓存，要读 `_src.txt` 才知道源目录在哪 —— 那条没了）。
 *
 *  ★★ 为什么必须优先 RAW（SV 09-15 原话：「工作台本来就要优先用 raw 啊」）：
 *    入口那一段（零点 `entry_zero_ev` + 成形 `ENTRY_GAMMA/KNEE/CEIL` + 趾部 `ENTRY_TOE` +
 *    高光护栏 `clip_guard`）**只写在 `io.load_raw` 里**，`io.load_std`（JPG 那条）根本不跑。
 *    而 `photosInDir` 只按 JPG 列图、RAW 只当"有 RAF"的角标 ⇒ 工作台一直喂 JPG ⇒
 *    「整张亮暗(总)」「暗部亮度」这两根滑杆**永远是死的**（实测同一张 DSCF0546：
 *     走 RAW 能带动 −18.9 ~ +31.0 个 L*，走 JPG 是 0.00），而且看到的画面也不是引擎真正出图那条路。
 *
 *  ⚠ `name` / `rel` **不许改** —— 它们是身份键（星级、成片归档、缩略图、EXIF、TopBar 同步
 *    全按 `主题名||文件名` 索引），改成 RAF 名字会把这些整条链打歪。所以**另开一个字段**，
 *    只在"喂引擎"这一处用它（Viewer 的 `/load`）。
 *  ⚠ 原图放主题根目录 ⇒ 先按**根目录**的同名 RAW 找；纯 JPG 的主题（"效果测试"那几个）
 *    没有 RAW，自然回落到 JPG（此时入口那两根滑杆仍然是死的，面板上会标 `JPG 出图`）。
 */
function attachLoadPath(sessionPath, photos) {
  /* ★ 所有照片的原始文件都在 `sessionPath`（= 主题的源目录，09-15 起永远如此）。
     桶里的成片也在它下面的子目录里 ⇒ 先在**根**找同名 RAW，找不到再用文件自己。 */
  const rawByName = new Map();          // UPPER(stem) -> 真实文件名
  try {
    for (const f of fs.readdirSync(sessionPath)) {
      if (RAW_EXT.test(f)) rawByName.set(f.replace(/\.[^.]+$/, '').toUpperCase(), f);
    }
  } catch (err) { /* 目录读不到就当没有 RAW */ }
  for (const p of photos) {
    const base = String(p.rel || p.name || '').split(/[\\/]/).pop();
    const stem = base.replace(/\.[^.]+$/, '').toUpperCase();
    const raw = rawByName.get(stem);
    if (raw) {
      p.loadPath = path.join(sessionPath, raw);
      p.loadIsRaw = true;
    } else {
      const inRoot = path.join(sessionPath, base);
      p.loadPath = fs.existsSync(inRoot) ? inRoot : path.join(p.dir || sessionPath, base);
      p.loadIsRaw = false;
    }
  }
}

/** 扫单个目录里的**片**（口径见 `photoFilesIn`：一张片 = 一个 RAW；孤 JPG 也算）。
    返回未排序数组，调用方负责排序与打标记。
    ⚠ `rel` 是**身份键**（`<stem>.JPG`），磁盘上**不一定有**这个名字 —— 它只用来索引
      （星级 / 桶合并 / 缩略图缓存 / 配方），**不许**拿去当路径，更不许当出图源。 */
function photosInDir(dir) {
  let files = [];
  try {
    files = fs.readdirSync(dir);
  } catch (e) {
    return [];
  }
  const own = photoFilesIn(files);
  return own.keys.map((key) => {
    const rawName = own.rawOf.get(key) || null;
    return { name: key, rel: key, hasRaw: !!rawName, rawName, dir };
  });
}

/* ★★ 界面代码改过、却没有重建 ⇒ 台子画的是「上一次构建的那一套」
   09-15 SV 报的正是这个：左栏**看不到**新做的「加入目录」，其实代码当天下午就写好了。
   症状特别阴 —— 台子照常开、照常能用、**一点报错都没有**，只是画的是旧界面。
   （比崩溃难查得多：崩溃至少指得出地方。）
   ⇒ 开窗**之前**先看一眼：`src/` + `vite.config.ts` + `renderer/index.html` 里
     最新的改动时间，比 `renderer/dist/index.js` 新，就先重建（实测约 2 秒）。

   ⚠ 三条纪律：
     ① 只管**开发目录**（打包版里没有 `src/` ⇒ 直接跳过，什么都不做）。
     ② **绝不允许它挡住启动**：任何异常都吞掉打一行字 —— 旧界面总比打不开强。
     ③ 不假设 PATH 上有 node（本机持久 PATH 里**没有** node，09-15 实测）——
        用 `process.execPath`（就是 electron 自己）+ `ELECTRON_RUN_AS_NODE=1`
        让它当 node 跑 vite（实测可行）。
   ★ 判定逻辑单独拆成一个**纯函数**（`needsUiRebuild`），好让静态自检拿真目录树
     真跑一遍（"改了 src 到底认不认得出要重建"光看正则看不出对错）。 */
function needsUiRebuild(root) {
  const srcDir = path.join(root, 'src');
  if (!fs.existsSync(srcDir)) return false; // 打包版
  let newest = 0;
  const walk = (d) => {
    let ents;
    try {
      ents = fs.readdirSync(d, { withFileTypes: true });
    } catch (e) {
      return; // 读不到就当没有（②）
    }
    for (const e of ents) {
      if (e.isDirectory()) {
        if (e.name === 'node_modules' || e.name.charAt(0) === '.') continue;
        walk(path.join(d, e.name));
      } else {
        try {
          const m = fs.statSync(path.join(d, e.name)).mtimeMs;
          if (m > newest) newest = m;
        } catch (e) {
          /* 忽略 */
        }
      }
    }
  };
  walk(srcDir);
  for (const f of ['vite.config.ts', path.join('renderer', 'index.html')]) {
    try {
      const m = fs.statSync(path.join(root, f)).mtimeMs;
      if (m > newest) newest = m;
    } catch (e) {
      /* 忽略 */
    }
  }
  try {
    return fs.statSync(path.join(root, 'renderer', 'dist', 'index.js')).mtimeMs < newest;
  } catch (e) {
    return true; // 还没构建过
  }
}

/* 重建这件事写进日志 —— 界面里看不到它（重建时窗口还没开），出问题得有个地方查。 */
function logUiRebuild(text) {
  try {
    const f = path.join(debugDir(), 'svstudio_rebuild.log');
    const ts = new Date().toISOString().replace('T', ' ').slice(0, 19);
    fs.appendFileSync(f, '[' + ts + '] ' + text + '\n');
  } catch (e) {
    /* 日志失败不影响启动 */
  }
}

function rebuildUiIfStale() {
  try {
    if (!needsUiRebuild(__dirname)) return;
    const vite = path.join(__dirname, 'node_modules', 'vite', 'bin', 'vite.js');
    if (!fs.existsSync(vite)) return;
    const { spawnSync } = require('child_process');
    const r = spawnSync(process.execPath, [vite, 'build'], {
      cwd: __dirname,
      /* ★★ 这里**不能**用 'inherit'：双击启动那个黑窗口 3 秒后就关了
         （`启动svStudio.bat` 末尾是 `timeout /t 3`），而重建要 1.5~2 秒 ——
         继承控制台的话控制台会先没掉，vite 往一个已经关掉的控制台写日志
         会报错、重建白做，而**表现又是"界面没变"**（= 这个功能本来的病）。
         ⇒ 输出自己收下来，写进日志文件。 */
      stdio: ['ignore', 'pipe', 'pipe'],
      env: Object.assign({}, process.env, { ELECTRON_RUN_AS_NODE: '1' })
    });
    const out = String((r && r.stdout) || '') + String((r && r.stderr) || '');
    const okr = r && r.status === 0;
    logUiRebuild((okr ? '重建成功' : '重建失败 status=' + (r ? r.status : '?')) + '\n' + out.slice(-2000));
  } catch (e) {
    /* ② 这条兜底是刻意的：重建出任何问题都**不许**挡住开台子 */
    logUiRebuild('重建出错（已跳过）：' + (e && e.message ? e.message : e));
  }
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

/* ★ 开窗前先把"界面是不是旧的"这件事解决掉 —— 见上面 rebuildUiIfStale 的说明。
   ⚠ 放在 createWindow **之前**：重建必须在窗口把 index.html 读进去之前做完。 */
app.whenReady().then(() => {
  rebuildUiIfStale();
  createWindow();
});

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

ipcMain.handle('scan-sessions', () => scanSessions());

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
  /* ★ 收口到 `ensureViewFile`：桶里的成片直接读，原图只有 RAW 就读缓存里那份机内 JPG，
     缓存缺了就**就地抠一张**。`rel` 是身份键，**不许**直接拼路径。 */
  const full = await ensureViewFile(sessionPath, rel);
  if (!full) return null;
  return enqueueThumb(() => makeThumb(full, width || 260));
});

/** 仅取原图尺寸（用于悬浮预览按比例自适应，无图，比 get-thumb 快）。
    内部缓存，命中即同步返回。 */
ipcMain.handle('get-thumb-meta', (e, sessionPath, rel) => {
  const v = viewFileOf(sessionPath, rel);
  if (!v) return { ow: 0, oh: 0 };
  try {
    if (!fs.existsSync(v.file)) return { ow: 0, oh: 0 };
  } catch (err) { return { ow: 0, oh: 0 }; }
  return getOriDim(v.file);
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
  const v = viewFileOf(sessionPath, rel);
  if (!v) return null;
  try {
    if (!fs.existsSync(v.file)) return null;
  } catch (err) { return null; }
  return 'file:///' + v.file.replace(/\\/g, '/');
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
/**
 * 造一份「相机直出 JPG」到 `outFile`（★ 09-15 SV 选 ③A）。
 *   ① `themePath/rel` 真在（同名 JPG）⇒ 直接复制；
 *   ② 否则从 `themePath` 里的同名 RAW 抠**全尺寸**机内 JPG（脚本 `--one --max-side 0`）。
 * ⚠ 为什么非改不可：原图只有 RAW 时那儿**什么都没有** —— 以前会直接报"root 原图不存在"；
 *   而纯 RAW 目录更糟：那时 `path` 指向预览缓存，拷进桶里的是 **1600 的缩略图**，
 *   拿它当"直出件"是错的（桶是要给 LR 精选用的）。
 * ⚠ 机内 JPG **就是相机直出** ⇒ 语义天然对得上，不用另找素材。
 */
async function makeStraightOut(themePath, rel, outFile) {
  const base = String(rel || '').split(/[\\/]/).pop();
  const direct = path.join(themePath, base);
  try {
    if (fs.existsSync(direct) && fs.statSync(direct).isFile()) {
      fs.mkdirSync(path.dirname(outFile), { recursive: true });
      fs.copyFileSync(direct, outFile);
      return { ok: true, from: 'jpg' };
    }
  } catch (e) { /* 往下走：当它没有同名 JPG */ }
  let files = [];
  try { files = fs.readdirSync(themePath); } catch (e) { files = []; }
  const stem = base.replace(/\.[^.]+$/, '').toUpperCase();
  let rawName = '';
  for (const f of files) {
    if (RAW_EXT.test(f) && f.replace(/\.[^.]+$/, '').toUpperCase() === stem) { rawName = f; break; }
  }
  if (!rawName) return { ok: false, error: 'root 里既没有同名 JPG、也没有同名 RAW' };
  try {
    fs.mkdirSync(path.dirname(outFile), { recursive: true });
    const r = await spawnPy(extIndexPy(), extIndexScriptPath(),
      ['--one=' + path.join(themePath, rawName), '--out-file=' + outFile, '--max-side=0'], () => { });
    if (fs.existsSync(outFile)) return { ok: true, from: 'raw' };
    const tail = String((r && r.text) || '').trim().split('\n').pop() || (r && r.error) || '原因没返回';
    return { ok: false, error: '从 RAW 抠机内 JPG 失败：' + tail };
  } catch (e) {
    return { ok: false, error: '从 RAW 抠机内 JPG 失败：' + String(e) };
  }
}

ipcMain.handle('archive-photos', async (e, opts) => {
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
          /* ★ 直出件（09-15 SV 选 ③A）：有同名 JPG 就复制，否则从同名 RAW 抠**全尺寸**机内 JPG。
             ⚠ 以前是 `root/rel` —— 原图只有 RAW 时会报"root 原图不存在"，
             纯 RAW 目录更糟（拷进去的是 1600 缩略图）。见 `makeStraightOut`。 */
          const made = await makeStraightOut(themePath, rel, s1);
          if (!made.ok) { failed.push({ file: rel, error: made.error }); continue; }
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

/** 读取一张片的 EXIF 并转为人类可读参数对象（带内存缓存）。
 *  ⚠ 读的是 **`viewFileOf` 解析出来的那个文件**：原图只有 RAW 时读的是缓存里那份
 *    从 RAW 抠出来的机内 JPG（它**带着完整的 EXIF**，相机参数不会丢）。
 *    `rel` 是身份键，直接 `path.join(sessionPath, rel)` 会读到一个不存在的文件。 */
function readExif(sessionPath, rel) {
  const v = viewFileOf(sessionPath, rel);
  const full = v && v.file;
  if (!full) return null;
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

/* ★★★ 09-15 深夜（SV 选「A」）：把「出图 / 导出」这类**要等**的请求，进出各记一行。
 *
 *   起因：SV 报「点出图中没反应，我看后台也没动静」，事后却**一点痕迹都查不到** ——
 *   因为 `engineGet` 一行日志都不写：请求到底发出去没有、超时没有、引擎回了什么，全看不见。
 *   ⇒ 查那个故障时只能靠"猜 + 复现"，成本极高，所以补上这条链的痕迹。
 *
 *   ★ 故意记**两行**（发出 / 回来）：只记一行的话，「发出去了但永远没回来」（卡住/超时）
 *     跟「压根就没发出去」在日志上长得**一模一样** —— 而那正是最需要分辨的那件事：
 *       · 只有 [→] 没有 [←] ⇒ 卡在引擎里（就是这次的双引擎情形）
 *       · [→] 都没有        ⇒ 卡在台子这侧（例如导出那个「另存为」对话框还挂着 / 被取消）
 *   ★ 只记 /load /base /render /export 这几条**要等几秒到几分钟**的路；
 *     /health、/stocks、/params 那种几十毫秒的探询不记，否则拉一下滑杆日志就刷屏。
 *   ★ 和界面日志**同一个文件**（`svstudio_render.log`）：出事时只看一个地方。
 *   ⚠ 隐私：`/load?paths=…`、`/export?src=…&path=…` 里带**真实目录名**。
 *     日志落在 `debugDir`（不在仓库里），但仍然只记**文件名** ——
 *     查问题时要知道的是"哪张片"，不需要知道它放在哪个目录。
 */
const ENGINE_TRACE_RE = /^\/(load|base|render|export)$/;
const ENGINE_TRACE_PATH_KEYS = { paths: 1, src: 1, path: 1 };

function engineTrace(line) {
  try {
    const ts = new Date().toISOString().replace('T', ' ').slice(0, 19);
    fs.appendFileSync(path.join(debugDir(), 'svstudio_render.log'), '[' + ts + '] ' + line + '\n');
  } catch (e) {
    /* 日志失败不影响主流程 */
  }
}

/** 把 query 收成"关键信息一眼看得见"的一行：路径只剩文件名、太长就截断并说明截了多少。 */
function engineTraceQ(search) {
  try {
    const q = String(search || '').replace(/^\?/, '');
    if (!q) return '(无参数)';
    const parts = q.split('&').map((kv) => {
      const i = kv.indexOf('=');
      if (i < 0) return kv;
      const k = kv.slice(0, i);
      if (!ENGINE_TRACE_PATH_KEYS[k]) return kv;
      const v = decodeURIComponent(kv.slice(i + 1));
      return k + '=' + v.split(',').map((x) => x.split(/[\\/]/).pop()).join(',');
    });
    const s = parts.join('&');
    return s.length > 400 ? (s.slice(0, 400) + '…(共 ' + s.length + ' 字符)') : s;
  } catch (e) {
    return '(参数串解析失败)';
  }
}

function engineGet(pathname, timeoutMs) {
  /* ★ 包装：只加日志，**一行原逻辑都不动**（真正的实现在下面的 `engineGetRaw`）。
     这样写的理由：原函数里有 6 处 resolve，逐个改成"先记日志再 resolve"既有漏改的风险，
     超时那条路还会 resolve 两次（destroy 会再触发一次 error）⇒ 日志会重复。
     包一层 `.then` 则天然只触发一次。 */
  const t0 = Date.now();
  let tag = null;
  try {
    const u0 = new URL(pathname, ENGINE.base);
    if (ENGINE_TRACE_RE.test(u0.pathname)) tag = u0.pathname.slice(1) + ' ' + engineTraceQ(u0.search);
  } catch (e) {
    /* URL 非法：让下面的 engineGetRaw 去报那条错 */
  }
  if (tag) engineTrace('[→] ' + tag);
  return engineGetRaw(pathname, timeoutMs).then((r) => {
    if (tag) {
      engineTrace('[←] ' + tag + ' ' + ((Date.now() - t0) / 1000).toFixed(1) + 's ' +
        (r && r.ok ? 'ok' : '失败：' + ((r && r.error) || '未知')));
    }
    return r;
  });
}

function engineGetRaw(pathname, timeoutMs) {
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

/** 一键把引擎拉起来（本机、后台、不弹窗）。已在跑则直接返回健康状态。
 *
 * ★★★ 09-15（SV 报「点出图没反应，后台也没动静」）—— 这一条是**根因守卫**：
 *   进调色台时**两个地方会同时**调 `engine-start`（Viewer 装载那条 effect + 右栏拉参数那条），
 *   而下面第一句就是 `await engineGet('/health')`，引擎冷启动要 10~20 秒 ⇒
 *   **两个调用都看到"没人应答"** ⇒ 各自 spawn 一个 ⇒ 两个都绑上 8765。
 *   ⚠ Windows 上这**不报错**：`ThreadingHTTPServer.allow_reuse_address=1` 走的是 SO_REUSEADDR，
 *     语义是"可以抢"而不是"用完能重绑"（POSIX 只管 TIME_WAIT）⇒ 第二个 bind 照样成功。
 *   ⇒ 后果：请求被两个进程分掉。落到"刚起、什么都没载入"的那个空引擎上的请求**瞬间失败**，
 *     而真正在干活的那个 CPU 一动不动 —— 正是他描述的现象（`engine_start.log` 里
 *     spawn 一律成对出现、`netstat` 里两个 PID 同时 LISTENING 8765，都验过）。
 *   ⇒ 守卫放在**唯一的 spawn 点**（就是这里），别的调用点不必各写一遍。
 *   另一道闸在引擎那侧（`service.py::_port_taken`）：端口有人应答就响亮退出，绝不静默开第二个。 */
let engineStartInflight = null;

async function doEngineStart() {
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
}

ipcMain.handle('engine-start', () => {
  /* ★ 一句话守卫：同一时刻只许有**一次**"起引擎"在飞（后到的直接接上同一个 Promise，
     而不是各起一个进程）。跑完就放开 —— 引擎真挂了还能再拉。 */
  if (!engineStartInflight) {
    engineStartInflight = doEngineStart().finally(() => { engineStartInflight = null; });
  }
  return engineStartInflight;
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
/** ★ 相纸清单（09-15 SV 选「C」：印相纸要能选，别写死）。
 *  ⚠ **必须带 `stock`** —— 默认相纸是**跟着卷走的**，引擎会在"这一卷配套的那张"上
 *    标 `isDefault`；前端**只认这个**来定初值，不许自己挑一个
 *    （同「基准成色」那条规矩：默认值一律由引擎给）。 */
ipcMain.handle('engine-papers', async (e, stock) => {
  const r = await engineGet('/papers?stock=' + encodeURIComponent(stock || ''), 4000);
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
 *  布尔写成 1/0（整层开关）、字符串照原样透传（脚本风格的 A/B 调用 + 柔光型号那种下拉值），
 *  其余非数字/NaN 一律丢掉（别污染引擎的 float() 解析）。
 *  ★★ 09-15（B3）：不放行这两类的话，"勾选框 / 下拉"会跟老 bug 一样**静默发不出去**
 *    （界面上有控件、画面不动），而且**不报错** —— 那正是本项目最阴的一类坑。
 */
function paramStr(p) {
  if (!p) return '';
  if (typeof p === 'string') return p;
  if (typeof p !== 'object') return '';
  const out = [];
  for (const k of Object.keys(p)) {
    const v = p[k];
    if (typeof v === 'boolean') {
      out.push(k + ':' + (v ? '1' : '0'));
      continue;
    }
    if (typeof v === 'string') {
      // ⚠ 分隔符不许出现在值里（`KEY:VAL,KEY:VAL` 的语法靠它切段）⇒ 含 ':' / ',' 的直接丢
      if (v && v.indexOf(':') < 0 && v.indexOf(',') < 0) out.push(k + ':' + v);
      continue;
    }
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
    /* ★ 相纸（09-15 SV 选「C」）：空串 = 这一卷的配套纸（引擎给默认）。
       ⚠ 名字不认得时引擎会回落并在 /stats 里标出来，不会崩 —— 别在前端自己兜。 */
    '&paper=' + encodeURIComponent(o.paper || '') +
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
  /* ★ 这条是"和渲染同一把尺子的原图"⇒ 要给引擎**原件**：有同名 RAW 就给 RAW
     （引擎认得 RAW），没有才回落到那个 JPG / 缓存里的机内 JPG。 */
  const v = viewFileOf(sessionPath, rel);
  const full = v ? (v.raw || v.file) : '';
  if (!full || !fs.existsSync(full)) {
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


/** ★★ 导出**成片**（09-15 SV 选「A」第 ② 项）：把**渲染结果**写成真照片文件。
    为什么要引擎来写：`io.save` 已经处理好 EXIF / 4:4:4（无色度抽样）/ 质量；
    前端拿 base64 再写一遍 = 丢相机信息 + 多一次编解码。

    ⚠ **导出尺寸由引擎定**（不传 `side` ⇒ 引擎按**原图全尺寸**出，09-15 SV 选「A」定的）——
      前端不许写死一个数：这条链上"前端自己写死默认值"已经踩过三次（基准名/纸名/滑杆初值）。
      导出的真实尺寸由引擎**回来告**（返回 w/h），界面只负责把它显示出来。
    ⚠ 导出跟预览**不是一个尺寸**（预览固定 700）：颗粒是物理量，尺寸一变观感就会变 ——
      **而且是"越大颗粒越明显"**（实测同一块平坦区 2048/3000/原图 = 0.63/0.77/1.87）。
      这一点要如实告诉用户，别让他以为"导出跟屏幕上看到的一模一样"。
    ⚠ 原图尺寸要重新解码 + 重新跑链，**RAW 一张约 6 分半**（2048 那档只要 29 秒）
      ⇒ 超时放到 15 分钟（900000 ms）。 */
ipcMain.handle('export-image', async (e, payload) => {
  const p = payload || {};
  const src = String(p.src || '');
  if (!src) return { ok: false, error: '没有可导出的原图（这张没有出图源）' };
  const stem = path.basename(src).replace(/\.[^.]+$/, '');
  const dir = path.dirname(src);
  const res = await dialog.showSaveDialog(win, {
    title: '导出成片（原图尺寸重新渲染，要等几分钟）',
    defaultPath: path.join(dir, stem + '_svfilm.jpg'),
    filters: [{ name: 'JPEG', extensions: ['jpg', 'jpeg'] }]
  });
  if (res.canceled || !res.filePath) return { ok: false, canceled: true };
  const qs = '?src=' + encodeURIComponent(src) +
    '&path=' + encodeURIComponent(res.filePath) +
    '&stock=' + encodeURIComponent(p.stock || '') +
    '&base=' + encodeURIComponent(p.base || '') +
    '&paper=' + encodeURIComponent(p.paper || '') +
    /* ⚠⚠ `paramStr` 是**唯一**允许把参数对象变成字符串的地方 ——
       09-15 那次「23 根滑杆一根都没接上」就是把对象直接 encodeURIComponent 成 `[object Object]`。 */
    '&params=' + encodeURIComponent(paramStr(p.params));
  const r = await engineGet('/export' + qs, 900000);
  return r.ok ? Object.assign({ ok: true }, r.data || {}) : r;
});


/**
 * 起一个 python 脚本子进程，把 stdout+stderr **逐行**喂给 `onLine`。
 *
 * ★ 预览索引脚本（`tools/make_jpg_index.py`）用它起子进程、把 stdout 逐行转成进度事件。
 *   只此一处实现 —— "一个汉字被切在两个 chunk 之间"这类边界只有一处要修。
 * @returns {Promise<{ok:boolean, code?:number, text:string, error?:string}>}
 */
function spawnPy(py, script, args, onLine) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(py, [script].concat(args || []), { windowsHide: true });
    } catch (err) {
      return resolve({ ok: false, text: '', error: '起不了子进程：' + err.message });
    }
    const dec = new StringDecoder('utf8');
    let text = '';
    let pending = '';
    const emit = (s) => {
      text += s;
      if (!onLine) return;
      pending += s;
      const parts = pending.split('\n');
      pending = parts.pop();          // 尾巴可能只是半行，留着等下一个 chunk
      for (const raw of parts) {
        const line = raw.replace(/\r$/, '');
        if (line.trim()) onLine(line);
      }
    };
    if (child.stdout) child.stdout.on('data', (b) => emit(dec.write(b)));
    if (child.stderr) child.stderr.on('data', (b) => emit(dec.write(b)));
    child.on('error', (err) => resolve({ ok: false, text, error: err.message }));
    child.on('close', (code) => {
      emit(dec.end());
      if (pending.trim() && onLine) onLine(pending.replace(/\r$/, ''));
      resolve({ ok: code === 0, code, text });
    });
  });
}

/* =========================================================
   库外·纯 RAW 目录 → 预览索引（见前面 `extIndexRoot` 那段注释）
   ---------------------------------------------------------
   ★ 主进程起子进程、把脚本的 stdout 逐行**推成事件**（`ext-index-progress`），
     不是等 invoke 返回 —— 几百张要转几十秒，等返回才显示的话界面全程像死机
     （这正是「看不出哪里不对」的另一副面孔）。
   ★ 脚本在仓库里（`tools/make_jpg_index.py`），不用 SV 配路径；
     解释器走 环境变量 SVEXTINDEX_PY → 配置 extIndexPy → 引擎那份（要 rawpy + PIL）。
   ========================================================= */

function extIndexScriptPath() {
  return path.join(__dirname, 'tools', 'make_jpg_index.py');
}

/** 跑索引脚本用哪份 Python（要能 `import rawpy` + `PIL`） */
function extIndexPy() {
  let cfg = '';
  try {
    cfg = String(loadConfig().extIndexPy || '').trim();
  } catch (e) {
    /* app 还没 ready 之类 */
  }
  const env = String(process.env.SVEXTINDEX_PY || '').trim();
  for (const c of [env, cfg]) {
    if (c && fs.existsSync(c)) return c;
  }
  return enginePy();
}

function sendExtIndexProgress(line) {
  try {
    if (win && !win.isDestroyed()) win.webContents.send('ext-index-progress', line);
  } catch (e) {
    /* 窗口没了就算了 —— 转图本身还在跑，不该因此中断 */
  }
}

/** 从脚本输出里取结果行（`KS_EXT_INDEX_OK {...}`）。**格式在脚本里，别改那行。** */
function parseExtIndexOutput(text) {
  const m = String(text || '').match(/KS_EXT_INDEX_OK\s+(\{[^\n]*\})/);
  if (!m) {
    return { ok: false, n: 0, skip: 0, fail: 0, error: '索引脚本没给出结果行（看下面的输出）' };
  }
  try {
    return Object.assign({ ok: true }, JSON.parse(m[1]));
  } catch (e) {
    return { ok: false, n: 0, skip: 0, fail: 0, error: '结果行解析失败：' + e.message };
  }
}

/**
 * 给一个**纯 RAW 的库外目录**建/补它的预览索引。幂等：已经有的不会重转。
 *
 * 返回 `{ok, n, skip, fail, dir, text, error}`；`n` = 这次真转出来的张数。
 * ⚠ 目录读不到 / 脚本不在 / 解释器起不来 ⇒ `ok:false` **并把原因说出来**：
 *   此时那个目录在左栏里会是空的，而"空"和"坏了"在界面上长得一样，
 *   不报原因就等于没做（这是本轮反复踩的同一个坑）。
 */
ipcMain.handle('ext-index', async (e, srcDir) => {
  const dir = String(srcDir || '').trim();
  if (!dir || !fs.existsSync(dir)) {
    return { ok: false, n: 0, skip: 0, fail: 0, error: '目录不存在：' + (dir || '(空)') };
  }
  const st = extIndexNeeds(dir);
  if (!st.stems.length) {
    return { ok: true, n: 0, skip: 0, fail: 0, dir: st.dir, text: '', note: '这个目录没有"纯 RAW"的片' };
  }
  if (!st.need) {
    return { ok: true, n: 0, skip: st.want, fail: 0, dir: st.dir, text: '', note: '预览索引已经是最新的' };
  }
  const script = extIndexScriptPath();
  if (!fs.existsSync(script)) {
    return { ok: false, n: 0, skip: 0, fail: 0, error: '预览索引脚本不在：' + script };
  }
  sendExtIndexProgress(
    '要给 ' + st.want + ' 张纯 RAW 生成预览小图（缺 ' + st.miss + ' 张，源目录只读）…'
  );
  const r = await spawnPy(
    extIndexPy(),
    script,
    ['--src=' + dir, '--out=' + st.dir],
    (line) => sendExtIndexProgress(line)
  );
  const out = parseExtIndexOutput(r.text);
  return Object.assign({ text: r.text }, out, { error: out.error || r.error || '' });
});

