/* eslint-disable no-console */
/**
 * ★★ 真实浏览器布局检测（无需 GUI，无需下载浏览器 —— 用本机 Edge）
 *
 * 用法：node _check/layout_check.mjs
 *
 * ★ 为什么需要它：静态检查测不出"底栏被挤出视口"这类**布局**问题
 *   （09-15 连着两次就是这个）。这里用真实 Chromium 打开 renderer/index.html，
 *   量各段的 getBoundingClientRect()，判断是不是都落在视口内。
 *
 * ⚠ 浏览器里没有 Electron 的 preload ⇒ window.api 不存在。
 *   所以进页面前先注入一个 **mock api**（返回假数据），让界面能正常渲染出来。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const PAGE = path.join(ROOT, 'renderer', 'index.html');

const EDGE =
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

let pass = 0, fail = 0;
const failures = [];
const ok = (n, d = '') => { pass++; console.log(`  ok   ${n}${d ? '   ' + d : ''}`); };
const bad = (n, d = '') => { fail++; failures.push(`${n} — ${d}`); console.log(`  FAIL ${n}${d ? '   ' + d : ''}`); };
const check = (n, c, dok = '', dbad = '') => (c ? ok(n, dok) : bad(n, dbad || dok));

/* 第 3 组跑完时挂上的"此刻占位文案有几个" —— 后面的组站在同一状态上继续测。
   （第 3 组跑完的状态 = 调色台 + 第一张图已出图 + 在根目录第一张上。） */
let afterFirstRender = null;
/* ⚠ 右栏「渲染」按钮的 locator 必须放在**模块级**。
   踩过：一开始在第 3 组的 `if {}` 块里 `const rBtn = ...`，第 [7] 组拿不到
   ⇒ `ReferenceError: rBtn is not defined`（块作用域，不是"没找到按钮"）。 */
let rBtn = null;

console.log('\n=== svStudio 布局检测（真实浏览器）===\n');

if (!fs.existsSync(EDGE)) {
  console.log('  跳过：本机没找到 Edge');
  process.exit(0);
}
if (!fs.existsSync(PAGE)) {
  console.log('  跳过：renderer/index.html 不存在');
  process.exit(0);
}

/* ★★ 这个检测跑的是**构建产物**（renderer/dist/index.js），不是 TS 源码。
   改了 src/ 却忘了 build ⇒ 它测的还是旧包 ⇒ 自检永远绿。
   （09-15 验证"这张网能不能抓 bug"时就栽在这 —— 故意把 bug 放回去、跑检测，居然全绿。）
   `npm run verify` 里是先 build 再测的，单独跑本脚本时靠这道守卫拦住。 */
const BUNDLE = path.join(ROOT, 'renderer', 'dist', 'index.js');
if (!fs.existsSync(BUNDLE)) {
  console.log('  ✗ 没有构建产物 renderer/dist/index.js —— 先 npm run ui:build');
  process.exit(1);
}
const newestInput = (() => {
  let t = 0;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const f = path.join(d, e.name);
      if (e.isDirectory()) walk(f);
      else t = Math.max(t, fs.statSync(f).mtimeMs);
    }
  };
  walk(path.join(ROOT, 'src'));
  for (const f of ['main.js', 'preload.js', 'vite.config.ts', 'renderer/index.html']) {
    const p = path.join(ROOT, f);
    if (fs.existsSync(p)) t = Math.max(t, fs.statSync(p).mtimeMs);
  }
  return t;
})();
if (newestInput > fs.statSync(BUNDLE).mtimeMs) {
  console.log('  ✗ 构建产物比源码旧（src/ 更新过）⇒ 先 npm run ui:build，否则测的是旧包');
  process.exit(1);
}

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

/** 收集渲染进程里的报错（相当于 Electron 的 log） */
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push('console: ' + m.text()); });

/* 注入 mock api —— 让界面在没有 Electron 时也能渲染出来 */
await page.addInitScript(() => {
  /* ★ 故意做成**竖构图**（3:4）+ 尺寸够大：真片子多是竖的，而"小窗被裁"这个 bug
     只有在「按宽度缩放后的高度 > 面板可用高度」时才会暴露 ⇒ 横图测不出来。 */
  const mk = (n) => `data:image/svg+xml;utf8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="#${((n * 37) % 900 + 100).toString(16)}44"/></svg>`
  )}`;
  /* ★ 导入计划的假数据 —— **照 `main.js` 的 `parseImportOutput()` 解出来的形状**抄
     （字段名、类型都要一致）。这是 mock 第二次踩"自己发明字段"的地方：
     09-15 引擎那次的教训是"mock 跟着前端一起错，自检全绿"。 */
  const IMPORT_PLAN = {
    src: 'J:\\DCIM\\100_FUJI',
    files: 524,
    total: '12.34 GB',
    types: 'JPG×300, RAF×224',
    dates: '2026-04-24 ~ 2026-04-25',
    dest: 'D:\\lib\\2026-04-24_旅行_长洲岛',
    folder: '2026-04-24_旅行_长洲岛',
    destRoot: 'D:\\lib',
    copied: 524,
    skipped: 0,
    failed: 0,
    elapsed: '45.2',
    verified: true,
    dryRun: true,
  };
  window.api = {
    logLine: async () => true,
    /* ★ 照生产端抄：`extraRoots` = 左栏「加入目录…」加进来的**库外目录**（绝对路径）。
       放在 window 上、方法里**现读** —— 测试中途改它才生效（同 __FAIL_HEALTH 的老办法）。 */
    getConfig: async () => ({ libRoot: 'D:\\lib', extraRoots: window.__extraRoots || [] }),
    /* ⚠ 主题列表要**能变**：导入完 `refreshSessions()` 会重扫，
       新主题必须出现（否则"导入完直接进新主题"这条根本测不到）。
       照生产端抄：列表是"扫出来的"，不是写死的常量。
       ★ 库外目录跟着**并排**列出来（照 `main.js` 的 `scanSessions`）：带 `path`（真实路径）、
         `external`、`rootDir`；名字取路径最后一段。
         ⚠ 少一个字段，「点库外条目用的是它自己的路径吗」就测不到 ——
           而"路径拼错"正是这个功能最容易坏、又最像"没反应"的地方。 */
    scanSessions: async () => {
      const base = window.__sessions || (window.__sessions = [
        { name: '主题A', count: 12, path: 'D:\\lib\\主题A' },
        { name: '主题B', count: 34, path: 'D:\\lib\\主题B' },
      ]);
      const ex = window.__extraRoots || [];
      return base.concat(ex.map((d) => ({
        name: String(d).split(/[\\/]/).filter(Boolean).pop(),
        path: d,
        count: 40,
        external: true,
        rootDir: d,
      })));
    },
    /* ★★ 出图源也要照生产端抄：`main.js` 的 `attachLoadPath()` 会给每张算出 `loadPath`
       （**同名 RAW 优先**，没有 RAW 的主题才回落 JPG）。这里故意混着给：
       i=1,5,9… 是"只有 JPG"的，用来测回落那一档。
       ★ 照片名**按主题区分**（照生产端：不同主题是不同批照片）——
         两个主题发同一批名字的话，「切主题之后看的是另一张」根本测不出来。
       ★ 导入出来的新主题给第三段号段（3000+）：这样"到底进没进新主题"一眼看得出。 */
    listPhotos: async (sessionPath) => {
      /* ★ 记下**每次列图用的路径** —— 「点库外条目是不是用了它自己的真实路径」靠它断言。
         只看照片名不够：路径拼错时画面照样能出，很难判。 */
      (window.__listPaths || (window.__listPaths = [])).push(sessionPath);
      const theme = String(sessionPath || '').split(/[\\/]/).filter(Boolean).pop() || '主题A';
      const base = theme === '主题B' ? 2000 : /^\d{4}-/.test(theme) ? 3000 : 1000;
      return Array.from({ length: 40 }, (_, i) => {
        const jpg = `DSCF${base + i}.JPG`;
        const isRaw = i % 4 !== 1;
        return {
          name: jpg,
          rel: jpg,
          hasRaw: isRaw,
          loadPath: `D:\\lib\\${theme}\\DSCF${base + i}.` + (isRaw ? 'RAF' : 'JPG'),
          loadIsRaw: isRaw,
        };
      });
    },
    // ★ mock 必须跟真实返回一致：{url,ow,oh}（09-15 裂图就是把返回当字符串用）
    getThumb: async (a, b, c) => ({ url: mk(c), ow: 400, oh: 300 }),
    getThumbMeta: async () => ({ ow: 4000, oh: 3000 }),
    getExif: async () => ({ camera: 'X-T4', lens: 'XF35', iso: 400, fnum: 1.4, ss: '1/250', fl: '35mm' }),
    /* ★ 记下**落盘的那份星级** —— 「打星要存下来」这条靠它断言。
       只看界面上的星星是不够的：星星亮着、却没存，重启就没了（这类"看着对、其实丢了"最难发现）。 */
    saveRatings: async (r) => {
      window.__ratingsSaved = r;
      return true;
    },
    /* ⚠ 这个 mock 原来**漏了 `setConfig`** —— 而 `useStore` 里 `saveLast()` 每次翻图/切主题/
       切台都会调它（`.catch(()=>{})` 本意是"存不上就算了"）。
       方法不存在 ⇒ 调用那一刻**同步抛 TypeError**，`.catch` 根本没机会接
       ⇒ 键盘翻图一路在往控制台丢未捕获异常，而检查只看接口，看不见。
       现在补上并**记下最后一次写进去的东西** —— 「上次看到哪张」有没有真落盘靠它断言。 */
    setConfig: async (patch) => {
      window.__setConfig = { ...(window.__setConfig || {}), ...(patch || {}) };
      /* ★ 照生产端：写进配置之后**重扫就该看见它** —— 这里把 `extraRoots` 也记到 window 上，
         否则「加完目录立刻出现在列表里」这条在自检里永远测不到（mock 写死 ⇒ 白测一场）。 */
      if (patch && Array.isArray(patch.extraRoots)) window.__extraRoots = patch.extraRoots;
      return true;
    },
    /* ⚠ 这个 mock 原来也漏了 `engineStart`（`ensureEngine()` 会调）。
       补上；`__FAIL_HEALTH` 时**返回"起不来"** —— 别返回 ok:true，
       否则 `ensureEngine` 会老老实实轮询 30 次 × 1 秒，自检要多等半分钟。 */
    engineStart: async () => {
      window.__engineStarts = (window.__engineStarts || 0) + 1;
      if (window.__FAIL_HEALTH) return { ok: false, error: '自检假装引擎起不来' };
      return { ok: true };
    },
    /* ⚠ 错误路径用：`window.__FAIL_HEALTH = true` ⇒ 假装引擎没起。
       开关放在 api 外面（window 上）、方法里现读 —— 这样测试**中途**改开关也生效。 */
    engineHealth: async () => (window.__FAIL_HEALTH ? { ok: false } : { ok: true }),
    /* ⚠⚠ 这里的形状**必须照 main.js 的 IPC 处理器抄**。
       09-15 就是 mock 跟着前端一起错（前端读 `{stocks}` / `r.id` / `r.bytes`，
       而 main.js 实际给 `{items}` / `items[0].id` / `image`）⇒ 卷基准列表全空、
       一张图都渲染不出来，**自检却全绿**。改 main.js 的返回，必须同步改这里。 */
    engineStocks: async () => ({
      ok: true,
      items: [
        { name: 'portra400', label: 'Portra 400', desc: '暖调', spek: true },
        { name: 'fuji_c200', label: 'C200', desc: '青绿', spek: true },
        { name: 'neutral', label: '中性', desc: '原样', spek: false },
      ],
    }),
    /* ★★ 基准列表**照生产端数据**抄：`main.js` 转发引擎 `/bases`，引擎现在每条带
       `isDefault`（`config.BASE` 那条为 true，这里是 `BASE_FULL`）。
       前端只认这个来定初值 —— **不许自己写死基准名**
       （过去写死 `'all'`，而引擎的基准表里没有 `'all'` ⇒ `resolve_base` **静默**
        回落成 `BASE_NONE`「不套基准」⇒ 默认出图等于"什么都没套"，界面上还一支都选不中）。
       ⚠ 顺序也照 `config.BASE_TABLE` 抄：默认那支**故意不排第一个** ——
         只有这样才测得动"取的是 `isDefault` 那条"，而不是"腿短取 `list[0]`"。 */
    engineBases: async () => ({
      ok: true,
      items: [
        { name: 'BASE_NONE', label: '不套基准', desc: '什么都不做' },
        { name: 'BASE_FOG', label: '只加雾', desc: '黑位抬起来' },
        { name: 'BASE_DEYELLOW', label: '退黄+加雾', desc: '没那么黄' },
        { name: 'BASE_FULL', label: '全对齐', desc: '全段对齐', isDefault: true },
      ],
    }),
    /* ★★ 相纸表（09-15 SV 选「C」）。形状照 `main.js` 的 `engine-papers` → 引擎 `/papers` 抄：
       `{ ok, items: [{name,label,desc,isDefault}] }`。
       ★★ 顺序**照引擎 `spektra.PAPER_ORDER` 抄**（第一张是柯达 Portra Endura）——
          而 `portra400` 的配套纸**恰好就是第一张**、`fuji_c200` 的配套却是第三张。
          只有这样，[15] 里"换卷跟着换配套纸"那条才能区分
          「读了引擎的 isDefault」和「腿短取了列表第一个」。
       ⚠ 中性卷 / 名字不认得 ⇒ **空表**（真卷之外没有"相纸"这回事）——
         引擎侧 `spektra.papers()` 就是这么定的，mock 跟着抄；
         `ui_smoke.mjs` 有一条静态检查把这份名单和 `spektra.py` 钉在一起。 */
    enginePapers: async (stock) => {
      const OWN = {
        portra400: 'kodak_portra_endura',
        fuji_c200: 'fujifilm_crystal_archive_typeii',
        ektar100: 'kodak_endura_premier',
        neutral: null,
      };
      const own = OWN[stock];
      if (!own) return { ok: true, items: [] };
      const ALL = [
        ['kodak_portra_endura', '柯达 Portra Endura', '人像纸：肤色最讨喜、暖而柔'],
        ['kodak_endura_premier', '柯达 Endura Premier', '全能纸：中性偏暖，最"标准"'],
        ['fujifilm_crystal_archive_typeii', '富士 Crystal Archive II', '清透偏冷'],
        ['kodak_supra_endura', '柯达 Supra Endura', '饱和度更高，口味更浓'],
        ['kodak_ektacolor_edge', '柯达 Ektacolor Edge', '更硬更艳'],
        ['kodak_2383', '柯达 2383 印片', '电影正片印片：暗部厚'],
        ['kodak_2393', '柯达 2393 印片', '2383 后继，稍柔和'],
        ['kodak_ultra_endura', '柯达 Ultra Endura（上游标注：数据有问题）', '仅作对照'],
      ];
      return {
        ok: true,
        items: ALL.map(([name, label, desc]) => ({
          name, label, desc, isDefault: name === own,
        })),
      };
    },
    /* ★ 按主题存配方（`main.js` 的 `get-grade` / `set-grade`，09-15 起前端才真调）。
       ⚠⚠ 这个 mock **必须给**：`enterSession` 现在会调 `getGrade` 套回配方，
         漏一个就是**同步抛 TypeError**、`.catch` 接不到（`setConfig` 那次的老坑）。
       ★ 形状照 `main.js` 的 IPC 处理器抄：`get-grade` 返回 **grade 对象本身或 null**
         （外面没有 `{ok}` 包壳），`set-grade` 返回 boolean。 */
    getGrade: async (name) => (window.__grades || {})[name] || null,
    setGrade: async (name, g) => {
      /* 深拷一份 —— 存的是"那一刻"的值，不能跟着 store 后续改动一起变 */
      window.__grades = { ...(window.__grades || {}), [name]: JSON.parse(JSON.stringify(g)) };
      window.__gradeSaves = (window.__gradeSaves || 0) + 1;
      return true;
    },
    engineParams: async () => ({
      ok: true,
      /* ★★ 这份假数据**必须照生产端抄**（`svFilm/service.py` 的 `PARAMS`）——
         09-15 踩过：这里原来还写着旧名字（「本张落点」/「提亮」）和旧区间，
         而检查只断言"名字在不在"，所以**它绿着、真界面早就不一样了**。
         现在这几条按真实值抄，并且多带一个 `dv` —— 检查要盯住"滑杆显示的是 dv"。
         ⚠ 改引擎的 PARAMS 就要回来改这里；`ui_smoke.mjs` 有一条静态检查盯着这两边别漂。
         ⚠ 09-15「脸太亮收回」是个**默认 0** 的滑杆：它就是这里 `dv: 0.0` 的那条 ——
            `dv` 不是区间中点（中点 0.5 会把每张片子的脸都往下收），必须跟引擎的出厂值一致。 */
      items: [
        { k: 'SPEK_PE_SHIFT', name: '整张亮暗', lo: 0.62, hi: 1.43, step: 0.01, grp: '真卷', spek: true, dv: 1.0, inv: true, d: '整张更亮还是更暗' },
        { k: 'FACE_SPAN_KMAX', name: '脸的层次', lo: 1.0, hi: 3.0, step: 0.05, grp: '脸', dv: 2.0, d: '脸内部明暗最多拉开几倍' },
        { k: 'SKIN_FLOOR_A', name: '脸的红绿', lo: 11.0, hi: 20.0, step: 0.1, grp: '脸', dv: 14.5, d: '脸偏红还是偏绿' },
        { k: 'ANCHOR_DOWN_GAIN', name: '脸太亮收回', lo: 0.0, hi: 1.0, step: 0.02, grp: '脸', dv: 0.0, d: '脸比该有的亮度还亮时，往靶收多少（0 = 不动）' },
        { k: 'TONE_LIFT', name: '中高调抬起', lo: 0, hi: 14, step: 0.5, grp: '影调', spek: false, dv: 9.0, d: '整张变亮' },
      ],
    }),
    /* ★ 记下每一次 /load 的路径 —— 下面要断言「喂给引擎的是 RAW」 */
    /* ⚠⚠ `id` 必须**每张都不一样**（照生产端抄：引擎每 load 一张给一个新 id）。
       09-15 踩过：这里原来写死 `id: 1` ⇒ "换图"在 React 眼里是 imgId 1→null→1、
       **最终值没变** ⇒ 渲染那条 effect 被 React 直接跳过（bail-out）⇒
       所有"翻图之后……"的检查全在空转，破法怎么改都红不了（检查是假的）。 */
    engineLoad: async (paths) => {
      (window.__engineLoads = window.__engineLoads || []).push(...(paths || []));
      /* ⚠ 错误路径用：`window.__FAIL_LOAD = true` ⇒ 假装装载失败 */
      if (window.__FAIL_LOAD) return { ok: false, error: '自检故意让它失败' };
      window.__nextId = (window.__nextId || 0) + 1;
      (window.__engineIds = window.__engineIds || []).push(window.__nextId);
      return { ok: true, items: [{ id: window.__nextId, path: 'x', ms: 12 }] };
    },
    engineBase: async () => ({ ok: true, image: mk(9) }),
    /* ★ 数一发渲染次数 —— 「换图到底会不会自动出图」这条契约要么数它，
       要么去数界面上的占位文案。数文案是**间接证据**（装载链断了也会留白），
       数次数是机制本身：换图后这个数不许涨。 */
    engineRender: async (id, opts) => {
      window.__renders = (window.__renders || 0) + 1;
      /* ★ 记下**每次渲染收到的参数** —— 「拖了滑杆之后，渲染带的是新值吗」靠它断言。
         ⚠ 这条链到 API 边界为止（前端 → main.js）。再往后那段（参数串拼装）由
           静态自检里的 paramStr 单测 + 端到端探针兜，别以为这里测到了全部。 */
      (window.__renderArgs = window.__renderArgs || []).push({
        id,
        params: JSON.parse(JSON.stringify((opts && opts.params) || {})),
        stock: opts && opts.stock,
        base: opts && opts.base,
        /* ★ 相纸也要记（09-15 SV 选「C」）—— 断言的是"**换纸之后发出去的真的是新纸**"。
           只验界面上那张纸有没有换是不够的：下拉亮对了、请求里却没带，画面就是没变
           （"看着对、其实对不上"）。 */
        paper: opts && opts.paper,
      });
      /* ★ 让 mock 渲染"真的花点时间" —— 只给「忙的时候发来的那一发会不会被丢掉」那条检查用：
         拖着滑杆时参数是**连续变化**的，而渲染要花时间；只有渲染真的花时间，
         "跑完补发最新那一发"那段逻辑才走得到（否则全被 60ms 防抖合并成 1 发，等于没测）。
         默认 0（其余检查要快，不想白等）。 `window.__RENDER_MS` 由那条检查自己设。 */
      const _ms = window.__RENDER_MS || 0;
      if (_ms > 0) await new Promise((r) => setTimeout(r, _ms));
      return { ok: true, image: mk(5) };
    },
    /* ★★ 导出成片（09-15 SV 选「A」）：回来的是**文件路径**（写盘由引擎做，为的是保住 EXIF），
       不是图片数据 —— 形状照 `main.js` 的 `export-image` 抄：`{ ok, path, w, h, bytes, ms }`。
       顺手记下发出去的请求，"带的是出图源吗 / 参数是原样交出去吗"靠它断言。 */
    exportImage: async (payload) => {
      (window.__exports = window.__exports || []).push(JSON.parse(JSON.stringify(payload || {})));
      return { ok: true, path: 'D:\\lib\\主题A\\DSCF1000_svfilm.jpg',
               w: 2048, h: 1365, bytes: 838860, ms: 29100 };
    },

    /* ---- 照片导入（SD 卡 / U 盘 → 照片库） ----
       ⚠⚠ mock **必须实现前端会调的每一个 IPC**：漏一个 ⇒ 那次调用**同步抛 TypeError**、
       `.catch` 根本没机会接 ⇒ 一路往控制台丢未捕获异常，而检查只看接口，看不见
       （09-15 漏 `setConfig` 就是这么漏过去的）。
       ★ 形状照 `main.js` 的 IPC 处理器抄：
         import-detect        → { ok, cards:[{drive,path,n}], script, libRoot }
         import-preview/-run  → { ok, text, plan, error }
         onImportProgress     → 返回**取消订阅函数**（生产端 preload 也是这么给的） */
    /* ⚠ 这个 mock 原来**没有** `pickDirectory` —— 而左栏「换图库」「加入目录」、
       导入弹窗的「手动选目录」都会调它。方法不存在 ⇒ 点那一刻**同步抛 TypeError**，
       `.catch` 接不到（"漏 setConfig"那次的翻版）。
       ★ 默认返回一个**库外目录**（用来测「加入目录」）；测试可以现改 `window.__pickDir`。 */
    pickDirectory: async () => window.__pickDir || 'D:\\拍摄素材\\厦门_外拍',
    pickFile: async () => 'D:\\tools\\import_photos.py',
    importDetect: async () => ({
      ok: true,
      cards: [{ drive: 'J', path: 'J:\\DCIM\\100_FUJI', n: 524 }],
      script: 'D:\\tools\\import_photos.py',
      libRoot: 'D:\\lib',
    }),
    importPreview: async () => {
      window.__previews = (window.__previews || 0) + 1;
      return { ok: true, text: '', plan: { ...IMPORT_PLAN, copied: null } };
    },
    importRun: async () => {
      window.__importRuns = (window.__importRuns || 0) + 1;
      /* ★ 照生产端：进度是**主进程逐行推过来的**（不是等进程结束给一个结果）。
         这里故意留 500 ms 再让 Promise 结掉 —— 真导入要几分钟，
         界面必须**在跑的过程中**就能看到这些行；自检靠这一点断言"进度真的到了界面"。 */
      const push = window.__importCb;
      if (push) {
        push('开始复制…（源卡只读，不会被动一个字节）');
        push('  100/524  已复制 2.30 GB  (85 MB/s)');
      }
      await new Promise((r) => setTimeout(r, 500));
      if (push) push('复制完成：新增 524，跳过(已存在) 0，失败 0');
      /* 导出来的新主题要出现在列表里（`refreshSessions()` 会重扫） */
      window.__sessions = (window.__sessions || []).concat([
        { name: '2026-04-24_旅行_长洲岛', count: 524 },
      ]);
      return { ok: true, text: '', plan: { ...IMPORT_PLAN, dryRun: false } };
    },
    onImportProgress: (cb) => {
      window.__importCb = cb;
      return () => {
        window.__importCb = null;
      };
    },
  };
});

await page.goto('file:///' + PAGE.replace(/\\/g, '/'));
await page.waitForTimeout(400);

/* ---------- 1. 界面渲染出来了没 ---------- */
console.log('[1] 渲染');
const rootHtml = await page.evaluate(() => document.getElementById('root')?.innerHTML || '');
check('#root 有内容（不是白屏）', rootHtml.length > 200, `${rootHtml.length} 字符`);
check('没有未捕获报错（除了缺 preload 的）',
  pageErrors.filter((e) => !/api|preload/i.test(e)).length === 0,
  '', pageErrors.slice(0, 3).join(' | '));

/* ---------- 2. 进主题，量各段位置 ---------- */
console.log('\n[2] 布局（进入主题后）');
// 点第一个主题
const firstBtn = page.locator('button', { hasText: '主题A' }).first();
if (await firstBtn.count()) {
  await firstBtn.click();
  await page.waitForTimeout(500);
}

const vh = 900;
const boxes = await page.evaluate(() => {
  const out = {};
  const push = (k, el) => {
    if (!el) return;
    const r = el.getBoundingClientRect();
    out[k] = { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) };
  };
  // 按特征找各段：顶栏(第一个 header/flex)、星级行(含 ★☆)、底栏(含 data-idx)
  push('topbar', document.querySelector('div[class*="rt-Flex"]'));
  const starBtn = [...document.querySelectorAll('button')].find((b) => /[★☆]/.test(b.textContent || ''));
  push('stars', starBtn?.parentElement);
  const idxEl = document.querySelector('[data-idx]');
  push('dock', idxEl?.closest('div[style*="height"]') || idxEl?.parentElement?.parentElement?.parentElement);
  return out;
});

check('顶栏在视口顶部', boxes.topbar && boxes.topbar.top < 60, JSON.stringify(boxes.topbar));
check(
  '★ 星级条在视口内',
  boxes.stars && boxes.stars.bottom <= vh && boxes.stars.top >= 0,
  JSON.stringify(boxes.stars),
  `跑到视口外了：${JSON.stringify(boxes.stars)}`
);
check(
  '★ 底栏在视口内（09-15 的坑）',
  boxes.dock && boxes.dock.bottom <= vh,
  JSON.stringify(boxes.dock),
  `底栏被挤出视口：${JSON.stringify(boxes.dock)}`
);
check('底栏有高度', boxes.dock && boxes.dock.h > 20, JSON.stringify(boxes.dock));

/* ★ SV 09-15：底部缩略图要能一眼看出「这张有没有 RAW」 */
const rawBadges = await page.evaluate(() => document.querySelectorAll('[data-raw]').length);
check('★ 底栏缩略图有 RAW 角标', rawBadges > 0, `${rawBadges} 个`);

/* ---------- 3. 调色台 ---------- */
console.log('\n[3] 调色台');
const gradeTab = page.locator('button', { hasText: '调色台' }).first();
if (await gradeTab.count()) {
  await gradeTab.click();
  await page.waitForTimeout(600);
  const txt = await page.evaluate(() => document.body.innerText);
  check('切到调色台后出现「胶片卷」', txt.includes('胶片卷'));
  check('出现「成色基准」', txt.includes('成色基准'));
  /* ★★ 09-15 回归：以前只查“标题在不在”—— 标题当然在，里面的列表是空的。
     必须查**列表里真的有东西**（拿 mock 里那几条的文案当探针）。 */
  check('★ 胶片卷列表真有内容（不是空列表）', txt.includes('暖调') || txt.includes('青绿'),
    '', '卷列表是空的 ⇒ 前端读的字段名跟 main.js 对不上');
  check('★ 成色基准列表真有内容', txt.includes('不套基准') || txt.includes('全对齐'),
    '', '基准列表是空的');
  check('★ 真卷下不列「中高调抬起」（那根在真卷下拧了没反应）', !txt.includes('中高调抬起'),
    '', '列了不该列的滑杆（spek=false 的必须隐藏）');
  check('列出了「整张亮暗」', txt.includes('整张亮暗'), '', '真卷下的滑杆一个都没画出来');
  check('列出了「脸的层次」', txt.includes('脸的层次'));
  /* ★★ 09-15 修的那个 bug 就靠这两条盯着：
     滑杆初值过去取「区间中点」，而引擎用的是 config 出厂值
     ⇒ 13 根里 12 根**显示的数字和实际生效的对不上**（画面没错，错的是那行字）。
     现在必须显示 `dv`。mock 里「脸的红绿」dv=14.5、区间 11~20（中点 15.5）
     ⇒ 拿它当探针：显示 14.5 才对，显示 15.5 就是又退回取中点了。 */
  check('★ 滑杆显示的是 dv（引擎此刻实际在用的值）', txt.includes('14.5'),
    '', '没看到 14.5 ⇒ 前端没在用 dv');
  check('★ 显示的不是区间中点', !txt.includes('15.5'),
    '', '显示了 15.5 = 又退回"取区间中点"那个老 bug');
  /* ★ SV 09-15：「渲染按钮放到胶片卷下」+ 任何操作都不自动出图，只靠这个按钮。 */
  rBtn = page.locator('button', { hasText: /^渲染$/ }).first();
  const rBox = (await rBtn.count()) ? await rBtn.boundingBox() : null;
  check(
    '★ 「渲染」按钮在右栏（胶片卷下面）',
    !!rBox && rBox.x > 1440 - 340,
    JSON.stringify(rBox),
    '按钮不在右栏 —— 应该在胶片卷下面'
  );
  /* ★★ 端到端：分屏两栏都该拿到图，占位文案应该消失。
     这条一下就能抓住“装载链断了”（engineLoad 读错字段 / engineBase 读错字段）。 */
  await page.waitForTimeout(800);
  const ph = await page.evaluate(
    () => (document.body.innerText.split('按「渲染」出图').length - 1)
  );
  check('★ 分屏两栏都出图了（占位文案应为 0 个）', ph === 0, `占位 ${ph} 个`,
    `还有 ${ph} 个占位 ⇒ 装载/渲染链断了（engineLoad / engineBase / engineRender 的返回形状）`);

  /* ★★ 出图源必须是 RAW（SV 09-15：「工作台本来就要优先用 raw」）。
     为什么这条是硬要求：入口那一段（零点/成形/趾部/护栏）**只在 `io.load_raw` 里跑**，
     喂 JPG 的话「整张亮暗(总)」「暗部亮度」这两根滑杆**永远是死的**
     （实测同一张：走 RAW 能带动 −18.9 ~ +31 L*，走 JPG 是 0.00）。 */
  const loads = await page.evaluate(() => window.__engineLoads || []);
  check('★ 调色台喂给引擎的是 RAW（不是 JPG）',
    loads.length > 0 && loads.every((x) => /\.raf$/i.test(x)),
    loads.map((x) => x.split(/[\\/]/).pop()).join(', ') || '(一次都没 load)',
    '喂的是 JPG ⇒ 入口那两根滑杆不生效（整张亮暗(总) / 暗部亮度）');
  check('★ 标题栏标出了这张的出图源', /RAW 出图/.test(txt),
    '', '看不出这张是用 RAW 还是 JPG 出的图');

  /* 翻到下一张："只有 JPG"的那张（mock 里 i=1），回落那一档也要对：
     ① 喂的是 JPG ② 标题栏改口成 JPG ③ 点一次「渲染」能真出图
     ⚠ 不能用底栏缩略图点 —— 调色台的底栏只列 ★≥1，这时候还是空的。
       用方向键翻图（`App.tsx` 的 keydown），顺便把"翻图也要重新算出图源"一起测了。 */
  const phCount = () =>
    page.evaluate(() => document.body.innerText.split('按「渲染」出图').length - 1);
  afterFirstRender = () => phCount();          // 给后面的组用（第一张图有图才算数）
  const renderCount = () => page.evaluate(() => window.__renders || 0);
  const renders0 = await renderCount();          // 进调色台那一发
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(900);
  const loads2 = await page.evaluate(() => window.__engineLoads || []);
  const last = loads2[loads2.length - 1] || '';
  const txt2 = await page.evaluate(() => document.body.innerText);
  /* ★ 自检环境自身的护栏：mock 每 load 一张必须给**新** id。
     写死 id 的话 imgId 是 1→null→1、最终值不变 ⇒ React 跳过重渲染（bail-out）⇒
     下面所有「翻图之后……」的断言全部空转，破法怎么改都红不了。 */
  const ids = await page.evaluate(() => window.__engineIds || []);
  check('★ 自检环境：mock 每次 load 给的是新 id（否则翻图断言会空转）',
    new Set(ids).size === ids.length, `id: ${ids.join(',')}`,
    'mock 里 load 返回的 id 重复 ⇒ 换图在 React 眼里没变，下面的断言等于没跑');
  check('★ 翻图后加载的是新那张（不是死守一张）', loads2.length >= 2, `${loads2.length} 次 load`);
  check('★ 没有 RAW 的照片回落到 JPG（不会空栏）',
    /\.jpe?g$/i.test(last), last.split(/[\\/]/).pop() || '(没记到)');
  check('★ 没有 RAW 时标题栏改口成 JPG', /JPG 出图/.test(txt2), '',
    '没有 RAW 却说 RAW，等于骗人');
  /* ★★ 契约（SV 09-15 定「两个触发点」→ 09-15 晚选「A」扩成三个）：
     自动出图 = ① 进/切进调色台 ② 点「渲染」 ③ 拖右栏滑杆（第 6 节单独钉那三条）。
     「换图」不在里面 ⇒ 翻过来之后**渲染次数不许涨**，右栏留白等那一发。
     这条盯的是**机制**（引擎被叫了几次），不是界面文案 —— 文案是间接证据：
     装载链断了也会留白，那时候这条会"绿得莫名其妙"（下面那条才管装载链）。 */
  const renders1 = await renderCount();
  check('★ 换图不自动出图（引擎渲染次数不涨）', renders1 === renders0,
    `${renders0} → ${renders1} 发`,
    `翻图自动出图了（多发 ${renders1 - renders0} 发）—— 违反"只有两个触发点"的契约`);
  check('★ 换图后右栏确实留白等「渲染」', (await phCount()) === 1,
    `占位 ${await phCount()} 个`, '换图后右栏没留白 —— 装载链可能断了');
  /* 再点一次「渲染」：验的是**"没有 RAW 的主题，回落 JPG 那一档能不能真出图"**。 */
  await rBtn.click();
  await page.waitForTimeout(900);
  check('★ 回落 JPG 后点「渲染」也能出图（占位应为 0 个）',
    (await phCount()) === 0, `占位 ${await phCount()} 个`,
    '没有 RAW 就出不了图 ⇒ 回落那一档卡住了');
  check('★ 「渲染」按钮点下去真的多出一发（不是空按钮）', (await renderCount()) > renders1,
    `${renders1} → ${await renderCount()} 发`);
  await page.keyboard.press('ArrowLeft');          // 翻回第一张
  await page.waitForTimeout(900);
  /* 翻回来同样不自动出图 ⇒ 补一发「渲染」，
     让后面 [5] 的检查仍然按"两栏都有图"来量（不然量的是留白）。 */
  const renders2 = await renderCount();
  await rBtn.click();
  await page.waitForTimeout(900);
  check('★ 翻回原图后点「渲染」照样出图（换图不自出 ≠ 换图后出不了）',
    (await phCount()) === 0, `占位 ${await phCount()} 个`);
  check('★ 翻回原图后也没白自出图（次数只多了点的那一发）',
    (await renderCount()) === renders2 + 1, `${renders2} → ${await renderCount()} 发`);
}

/* ---------- 4. 打星联动 ---------- */
console.log('\n[4] 打星联动（★ 状态单一数据源）');
const stars = page.locator('button').filter({ hasText: /^[★☆]$/ });
if ((await stars.count()) >= 3) {
  await stars.nth(2).click();
  await page.waitForTimeout(250);
  const t = await page.evaluate(() => document.body.innerText);
  check('打 3 星后顶栏计数变了', /3\s*\/\s*40|3 星/.test(t) || t.includes('★'), t.slice(0, 80).replace(/\n/g, ' '));
}

/* ---------- 5. 小窗下分屏不能被裁（SV 09-15 报「两张图被裁」） ---------- */
/* ★ 病因：Pane 里那个装图的内容容器是个 flex 子项，默认 min-height:auto ⇒ **不肯收缩**
   ⇒ 被图撑得比面板还高 ⇒ 图被外层 overflow:hidden 切掉。
   这里把窗口调小，逐个量：图必须**整个落在它那个 overflow:hidden 的祖先里**，且长宽比不变。 */
console.log('\n[5] 小窗下分屏不被裁');
const SMALL = { width: 1080, height: 620 };
await page.setViewportSize(SMALL);
await page.waitForTimeout(700);
const paneFit = await page.evaluate((vh) => {
  const clipperOf = (el) => {
    let n = el.parentElement;
    while (n) {
      if (getComputedStyle(n).overflow === 'hidden') return n;
      n = n.parentElement;
    }
    return null;
  };
  const out = [];
  for (const img of document.querySelectorAll('img')) {
    const alt = img.getAttribute('alt');
    if (alt !== '原图' && alt !== '调色后') continue;
    const r = img.getBoundingClientRect();
    const panel = clipperOf(img);
    const pr = panel ? panel.getBoundingClientRect() : null;
    out.push({
      alt,
      size: Math.round(r.width) + 'x' + Math.round(r.height),
      clipped: pr
        ? r.top < pr.top - 1 || r.bottom > pr.bottom + 1 || r.left < pr.left - 1 || r.right > pr.right + 1
        : true,
      objectFit: getComputedStyle(img).objectFit,
      fitsInPane: pr ? r.width <= pr.width + 1 && r.height <= pr.height + 1 : false,
      inView: r.bottom <= vh + 1 && r.top >= -1,
    });
  }
  return out;
}, SMALL.height);
check('小窗下分屏两栏都量到了图', paneFit.length === 2, JSON.stringify(paneFit.map((x) => x.alt)));
check(
  '★ 小窗下两张图都没被裁（整图落在面板里）',
  paneFit.length > 0 && paneFit.every((x) => !x.clipped),
  JSON.stringify(paneFit.map((x) => x.alt + ' ' + x.size)),
  '被裁了：' + JSON.stringify(paneFit)
);
/* ★ 09-15：不要再用「元素盒子的长宽比 == 原图长宽比」判变形 ——
   改成绝对定位 + object-fit:contain 之后，盒子是面板大小、比例由 object-fit 保证。
   真正要钉的是两条：① object-fit 必须是 contain ② 盒子不能比面板大。 */
check(
  '★ 小窗下两张图都是 contain（既不会被裁也不会被拉）',
  paneFit.length > 0 && paneFit.every((x) => x.objectFit === 'contain'),
  JSON.stringify(paneFit.map((x) => x.objectFit)),
  'object-fit 不是 contain ⇒ 会裁掉或拉变形'
);
check(
  '★ 小窗下两张图比面板小（确实跟着窗口缩了）',
  paneFit.length > 0 && paneFit.every((x) => x.fitsInPane),
  JSON.stringify(paneFit.map((x) => x.size)),
  '图比面板还大 ⇒ 会被 overflow 裁掉'
);

/* ---------- 6. 自动出图只在这三个触发点（换卷/换基准/换图 ⇒ 不出；拖滑杆 ⇒ 出） --- */
/* ★★ 契约（SV 09-15 定「只有两个触发点」→ 09-15 晚选「A」扩成三个）：
     ① 进 / 切进调色台   ② 点右栏「渲染」   ③ **拖右栏滑杆**（"滑动每个参数都能实时预览"）。
   「换卷 / 换基准 / 换相纸 / 换图 / 恢复默认」**仍然不出图** —— 只改参数、不动画面。
   为什么拖滑杆那一条要单独钉**三层**：以前是"任何改动都自动出图"，拖一次能瞬间打出几十发
   6~15 s 的渲染互相抢占，最后那张反而迟迟不出来（看着就像"点了没反应"）；
   而修的时候又容易走过头 —— 忙的时候收到的那一发被直接丢掉 ⇒ 松手后画面停在中间某一格
   （参数是新的、画面是旧的）。所以同时钉：**会出图**、**合并**、**最后一发是最新值**。
   盯的是**机制**：引擎被叫了几发、最后一发带的是什么值（不是界面文案）。 */
console.log('\n[6] 自动出图：换卷/换基准/换图不出，拖滑杆出（但合并、且最后一发是最新值）');
await page.setViewportSize({ width: 1440, height: 900 });
await page.waitForTimeout(400);
const renders = () => page.evaluate(() => window.__renders || 0);
if (afterFirstRender) {
  const noRender = async (label, act) => {
    const b = await renders();
    await act();
    await page.waitForTimeout(500);
    const a = await renders();
    check(`★ ${label} ⇒ 不自动出图`, a === b, `渲染 ${b} → ${a} 发`,
      `${label}触发了渲染（多发 ${a - b} 发）—— 违反"只有两个触发点"`);
  };
  await noRender('换卷', async () => {
    const b = page.locator('button').filter({ hasText: /^C200/ }).first();
    if (await b.count()) await b.click();
  });
  await noRender('换基准', async () => {
    const b = page.locator('button').filter({ hasText: /全对齐/ }).first();
    if (await b.count()) await b.click();
  });
  /* 拖滑杆：走 Radix 滑杆**自己的键盘交互**（值一定会变），不是直接调 store。
     ★ 先把 mock 渲染调慢（300ms）：不慢的话拖动那几发全被 60ms 防抖合成 1 发，
       "忙的时候收到的那一发会不会被丢掉"根本没被走到 ⇒ 那三条检查全是假的。 */
  const sl = page.locator('[role="slider"]');
  const nSl = await sl.count();
  check('右栏有滑杆可拖（这条才测得动）', nSl > 0, `${nSl} 根`);
  if (nSl > 0) {
    await page.evaluate(() => { window.__RENDER_MS = 300; });
    const before = await renders();
    const v0 = await sl.first().getAttribute('aria-valuenow');
    await sl.first().focus();
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(40);
    }
    await page.waitForTimeout(1500);          // 等"最后一发"落地
    const v1 = await sl.first().getAttribute('aria-valuenow');
    check('拖滑杆之后值**真的变了**（否则下面几条是空转）', !!v1 && v1 !== v0, `${v0} → ${v1}`);
    const after = await renders();
    check('★★ 拖滑杆 ⇒ **会**自动出图（09-15 SV 选「A」：实时预览）', after > before,
      `渲染 ${before} → ${after} 发`);
    check('★★ 但**合并**：6 次连续变化 ⇒ 至多发 4 发（不合并会发 6 发以上）',
      after - before <= 4, `渲染 ${before} → ${after} 发（多发 ${after - before}）`,
      '防抖/合并没生效 ⇒ 拖一次就打出一串 1~4 秒的渲染互相抢占');
    /* ★★ 真正要守的那条：**忙的时候发来的最后一发不许丢**。
       丢了 = 松手后画面停在中间某一格（参数是新的、画面是旧的）——
       这正是 09-15 修的那个 bug（老代码 `if (busyRef.current) return;` 把它扔了）。 */
    const args2 = await page.evaluate(() => (window.__renderArgs || []).slice());
    const last = (args2[args2.length - 1] || {}).params || {};
    const hit = Object.values(last).some((v) => Math.abs(Number(v) - Number(v1)) < 1e-9);
    check('★★ 最后一发渲染带的是**最新值**（不是拖动途中某一格）', hit,
      `最后一发的参数 ${JSON.stringify(last)} 里找不到 ${v1}`,
      '忙时那一发被丢掉 ⇒ 松手后画面停在中间格，看着像"拖了没反应"');
    await page.evaluate(() => { window.__RENDER_MS = 0; });
  }

  /* ★★ 「导出成片」（09-15 SV 选「A」第 ② 项）：按钮 → 请求里带的东西对不对。
     钉三件：① 真的发出去了；② 带的是**出图源**（loadPath，绝对路径），不是身份键 rel；
             ③ 参数是**原样交出去的对象**（拼串是 main.js 一家的事，前端不许自己拼）。 */
  const exBtn = page.locator('button', { hasText: /^导出成片/ }).first();
  check('右栏有「导出成片」按钮（否则下面几条全是空转）', (await exBtn.count()) > 0);
  if (await exBtn.count()) {
    const beforeEx = await page.evaluate(() => (window.__exports || []).length);
    await exBtn.click();
    await page.waitForTimeout(600);
    const ex = await page.evaluate(() => window.__exports || []);
    check('★ 点「导出成片」⇒ 真的发了导出请求', ex.length === beforeEx + 1,
      `导出请求 ${beforeEx} → ${ex.length} 条`);
    const e = ex[ex.length - 1] || {};
    check('★ 导出带的是**出图源**（绝对路径的 loadPath），不是身份键 rel',
      /[\\/]/.test(String(e.src || '')), String(e.src || '(空)'),
      '`rel` 只是文件名（星级/归档/缩略图都按它索引）—— 拿它当出图源就是喂错文件');
    check('★ 导出把**参数对象**原样交出去（不自己拼串、也不自己写死尺寸）',
      e.params && typeof e.params === 'object' && !Array.isArray(e.params) && !('side' in e),
      JSON.stringify(e),
      '前端自己拼串 = 09-15 那 23 根滑杆全没接上的老路；写死尺寸 = 引擎改了工作分辨率前端不跟');
  }
}

/* ---------- 7. 拖滑杆 → 渲染请求里带的是**新值**吗 ---------- */
/* ★ 这一条对着 09-15 那个真 bug：「23 根滑杆一根都没接上」——
   当时前端发出去的是 `[object Object]`，引擎静默解出 `{}`，点渲染**永远没反应**。
   ⚠ 这条链**只到 API 边界**（前端 → main.js）：参数的对象里有没有那根新值。
     再往后那段（对象 → `KEY:VAL` 串）由静态自检里的 `paramStr` 单测
     + `_debug/_probe_wire_e2e.py` 的端到端探针兜 —— 别以为这里测完了全部。 */
console.log('\n[7] 滑杆 → 渲染参数');
const renderArgs = () => page.evaluate(() => (window.__renderArgs || []).slice());
if (afterFirstRender && rBtn && (await page.locator('[role="slider"]').count()) > 0) {
  /* ★ 09-15 晚（SV 选「A」之后）改了这里的**前提**：拖滑杆现在会**自动出图**
     ⇒ 不能再假设"上一发渲染的 params 是空的"（第 6 节刚拖过）。
     改成：先「恢复默认」+ 渲染一发（此时一根都没碰过 ⇒ params 必须是空的），
     再**只拖一格、故意不点「渲染」** —— 量的正是"拖动自己发出的那一发"。
     ★ 这样反而更硬：它顺带证明了「拖滑杆带的是新值」这条路真的通。 */
  const resetBtn7 = page.locator('button', { hasText: '恢复默认' }).first();
  if (await resetBtn7.count()) await resetBtn7.click();
  await rBtn.click();
  await page.waitForTimeout(900);
  const a0 = await renderArgs();
  const p0 = (a0[a0.length - 1] || {}).params || {};
  check('★ 没碰过的滑杆不进参数串（未触碰 = 用引擎出厂值）', Object.keys(p0).length === 0,
    `上一发 params = ${JSON.stringify(p0)}`,
    '未触碰的滑杆也被塞进参数串 ⇒ 会和引擎出厂值打架');
  const sl7 = page.locator('[role="slider"]').first();
  await sl7.focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(1200);            // 拖动自己会发一发，等它落地
  const a1 = await renderArgs();
  const p1 = (a1[a1.length - 1] || {}).params || {};
  const keys = Object.keys(p1);
  check('★ 拖过的滑杆**真的进了**渲染请求（不是空对象）', keys.length >= 1,
    JSON.stringify(p1), '参数是空的 ⇒ 滑杆白拧（就是"点渲染没反应"那一类）');
  const changed = [...new Set([...Object.keys(p0), ...keys])].filter((k) => p0[k] !== p1[k]);
  check('★ 带的是**新值**：和上一发比，正好变了一根', changed.length === 1,
    `变了: ${changed.join(',') || '(没有)'}；当前 ${JSON.stringify(p1)}`,
    '参数没跟着滑杆走');
  check('参数名看起来是个真参数键（不是 undefined / [object Object] 之类）',
    keys.every((k) => /^[A-Z][A-Z0-9_]*$/.test(k)), keys.join(','),
    '键名不像参数名 ⇒ 前端把别的东西当参数发出去了');
}

/* ---------- 8. 选片台（筛选 / 点缩略图 / 打星落盘） ---------- */
console.log('\n[8] 选片台行为');
const dockThumbs = () => page.locator('[data-idx]').count();
/* 底栏上方那行显示"当前是哪张"（`Stars.tsx` 的 ExifBar，居中的纯文本 div）。
   ⚠⚠ 踩过：`Dock.tsx` 的缩略图格子底下也有一行文件名（`Thumb` 没打星时显示 `p.name`），
   形状一模一样（纯文本 div + 居中）⇒ 原来这个 helper 取"最后一个匹配"，
   取到的**永远是底栏最后一张缩略图的文件名**，于是
   「点缩略图换图了没」「切主题从第一张开始吗」这些断言全在看同一张缩略图 ⇒ 恒假/恒真。
   ⇒ 必须把"在缩略图格子里"的那些排除掉（格子上有 `data-idx`）。 */
const curName = () =>
  page.evaluate(() => {
    const hits = [...document.querySelectorAll('div')].filter(
      (d) =>
        d.children.length === 0 &&
        !d.closest('[data-idx]') &&
        /^[A-Za-z0-9_.\-]+\.(JPG|RAF|jpg|raf)$/.test(d.textContent.trim())
    );
    const mid = hits.filter((d) => getComputedStyle(d).textAlign === 'center');
    const pick = mid.length ? mid : hits;
    return pick.length ? pick[pick.length - 1].textContent.trim() : '';
  });
const pickTab = page.locator('button', { hasText: '选片台' }).first();
if (await pickTab.count()) {
  await pickTab.click();
  await page.waitForTimeout(600);
  const txt = await page.evaluate(() => document.body.innerText);
  /* ★ 筛选 chips 上的**数字**就是筛选逻辑的产物 —— 拿它当探针，别查"标题在不在"。
     [4] 已经给第一张打了 3 星 ⇒ 期望：全部 40 / 未评 39 / 3★ 1 */
  check('★ 筛选计数对得上（打过星之后）',
    /全部\s*40/.test(txt) && /未评\s*39/.test(txt) && /3★\s*1/.test(txt), '',
    `期望 全部40 / 未评39 / 3★1。实际：${txt.replace(/\n/g, ' ').slice(0, 110)}`);
  /* ★ 打星要**落盘**：只看界面星星亮不亮是不够的 —— 亮着却没存，重启就没了，
     这类"看着对、其实丢了"最难发现。 */
  const saved = await page.evaluate(() => window.__ratingsSaved || null);
  check('★ 打星真的落盘了，键名是「主题名||文件名」',
    !!saved && saved['主题A||DSCF1000.JPG'] === 3, JSON.stringify(saved),
    '界面上星星亮着、却没存下来');
  const nAll = await dockThumbs();
  check('底栏有缩略图可点', nAll >= 3, `${nAll} 张`);
  const chip3 = page.locator('button', { hasText: /^3★/ }).first();
  if (await chip3.count()) {
    await chip3.click();
    await page.waitForTimeout(400);
    const n3 = await dockThumbs();
    check('★ 按 3★ 筛 ⇒ 底栏只剩那一张（筛完既不是空的、也不是全留着）', n3 === 1,
      `${nAll} → ${n3} 张`);
  }
  const chipAll = page.locator('button', { hasText: /^全部/ }).first();
  if (await chipAll.count()) {
    await chipAll.click();
    await page.waitForTimeout(400);
    check('★ 回到「全部」⇒ 片单恢复', (await dockThumbs()) > 1, `${await dockThumbs()} 张`);
  }
  const before = await curName();
  const th = page.locator('[data-idx]');
  if (before && (await th.count()) >= 3) {
    await th.nth(2).click();
    await page.waitForTimeout(500);
    const now = await curName();
    check('★ 点底栏缩略图 ⇒ 当前这张真的换了', !!now && now !== before, `${before} → ${now}`);
  } else {
    check('★ 点底栏缩略图 ⇒ 当前这张真的换了', false, before || '(读不到当前文件名)');
  }
}

/* ---------- 9. 状态记忆（切主题 / 切台 再回来） ---------- */
console.log('\n[9] 状态记忆');
{
  /* ⚠★ 切主题**必须走左栏「图库目录」那条路**（`SessionPane` 直接 `enterSession`），
     不能走「主题列表」—— 主题列表会先 `goHome()`，而 `goHome` 里就把 `cur` 归 0 了
     ⇒ 再进主题时"下标归零"是 goHome 干的，**验证不到 `enterSession` 自己有没有归零**。
     （09-15 破法验证时发现的：破法⑩把 `enterSession` 的 `cur:0` 删掉，这一组居然全绿 ——
       就是被 goHome 兜住了。改成走左栏之后破法立刻命中。） */
  const themeList = page.locator('button', { hasText: '主题列表' }).first();
  const openTheme = async (n) => {
    const b = page.locator('button', { hasText: n }).first();
    if (await b.count()) {
      await b.click();
      await page.waitForTimeout(900);
    }
  };
  await openTheme('主题B');
  const nb = await curName();
  check('★ 切到主题B ⇒ 看的是 B 的照片、而且从第一张开始', /DSCF2000/.test(nb), nb || '(没读到)');
  /* 先在 B 里翻两下（把下标推到 2），再切回 A —— 这样才测得动「切主题会不会把下标带过去」 */
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(500);
  const nb2 = await curName();
  check('在主题B里翻两张（否则下一条是空转）', /DSCF2002/.test(nb2), nb2 || '(没读到)');
  await openTheme('主题A');
  const na = await curName();
  check('★ 切回主题A ⇒ 从第一张开始（下标没被带到另一个主题）', /DSCF1000/.test(na),
    na || '(没读到)', `切主题时下标没归零 ⇒ 切到短主题会出现"打开就白屏"，实际 ${na || '空'}`);
  /* ★ 状态记忆要**真落盘**（`useStore.saveLast` → `api.setConfig`），不是只存在内存里。
     只测"切来切去还在"是不够的 —— 那只证明内存里没丢，关掉程序就没了。 */
  const cfgSaved = await page.evaluate(() => window.__setConfig || {});
  check('★ 「上次看到哪」真写进配置了（下次启动才能回到原地）',
    cfgSaved.lastSession === '主题A' && typeof cfgSaved.lastCur === 'number',
    JSON.stringify(cfgSaved),
    '没写进配置 ⇒ 下次启动回不到原来那张');
  /* 「主题列表」（回首页）这条路也得还在 —— 顺便量一下首页真列出了两个主题 */
  if (await themeList.count()) {
    await themeList.click();
    await page.waitForTimeout(700);
    const th = await page.evaluate(() => document.body.innerText);
    check('★ 从调色流程回「主题列表」还列得出两个主题（没把库弄丢）',
      /主题A/.test(th) && /主题B/.test(th), '', '首页列不出主题了');
    await openTheme('主题A');
  }
  const txt = await page.evaluate(() => document.body.innerText);
  check('★ 切一圈回来，星级还在（3★ 计数没变）', /3★\s*1/.test(txt), '',
    '切主题把星级弄丢了');
  /* 切台再切回来：拖过的那个滑杆值要还在（不是偷偷回默认） */
  const gradeTab2 = page.locator('button', { hasText: '调色台' }).first();
  if (await gradeTab2.count()) {
    await gradeTab2.click();
    await page.waitForTimeout(900);
  }
  const vA = await page.locator('[role="slider"]').first().getAttribute('aria-valuenow');
  await pickTab.click();
  await page.waitForTimeout(500);
  await gradeTab2.click();
  await page.waitForTimeout(900);
  const vB = await page.locator('[role="slider"]').first().getAttribute('aria-valuenow');
  check('★ 切到选片台再回调色台，拖过的滑杆值还在', !!vA && vA === vB, `${vA} → ${vB}`,
    '切一圈回来滑杆偷偷回默认了');
}

/* ---------- 10. 错误路径（装载失败 / 引擎没起） ---------- */
/* ★ 为什么要测"失败"这条路：以前出过一次"点了没反应" —— 实际是**失败了但一声不吭**。
   失败必须看得出来，而且不许白屏。 */
console.log('\n[10] 错误路径');
{
  await page.evaluate(() => {
    window.__FAIL_LOAD = true;
  });
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(700);
  const t = await page.evaluate(() => document.body.innerText);
  check('★ 装载失败时界面**说出来**了（不是静默没反应）', /装载失败/.test(t),
    t.replace(/\n/g, ' ').slice(0, 110),
    '装不进来却一声不吭 ⇒ 在用户眼里就是"点了没反应"');
  const len1 = await page.evaluate(() => (document.getElementById('root')?.innerHTML || '').length);
  check('★ 装载失败也不白屏（界面还在）', len1 > 500, `${len1} 字符`);
  await page.evaluate(() => {
    window.__FAIL_LOAD = false;
  });
  await page.keyboard.press('ArrowLeft');
  await page.waitForTimeout(600);

  /* 引擎没起：整页重来一次，让 api.engineHealth 报"没起"。
     ⚠ 重开之后**先进一个主题**（`loadSessions` 没配 lastSession ⇒ 停在主题列表，
       这时候根本没有调色台分屏，量"引擎未启动"会量到空气）。 */
  await page.addInitScript(() => {
    window.__FAIL_HEALTH = true;
  });
  await page.reload();
  await page.waitForTimeout(1500);
  const themeA = page.locator('button', { hasText: '主题A' }).first();
  if (await themeA.count()) {
    await themeA.click();
    await page.waitForTimeout(800);
  }
  const gt = page.locator('button', { hasText: '调色台' }).first();
  if (await gt.count()) {
    await gt.click();
    await page.waitForTimeout(1500);
  }
  const t2 = await page.evaluate(() => document.body.innerText);
  check('★ 引擎没起时说得出来（不是白屏、不是静默）', /引擎未启动/.test(t2),
    t2.replace(/\n/g, ' ').slice(0, 110));
  /* ★ 还要说得出**为什么** —— 「引擎未启动」是通用兜底；
     真起不来时 `ensureEngine()` 会把引擎自己那句话带上来。只说"未启动"，
     用户不知道是没装、还是端口被占、还是崩了。 */
  check('★ 起不来时把引擎自己那句话带上来了（不是只说"未启动"）',
    /自检假装引擎起不来/.test(t2), '', '只给了通用兜底，没给具体原因');
  const len2 = await page.evaluate(() => (document.getElementById('root')?.innerHTML || '').length);
  check('★ 引擎没起也不白屏（界面还在）', len2 > 500, `${len2} 字符`);
}

/* ---------- 11. 「上次状态」恢复（下次启动能不能回到原地） ---------- */
/* ★ 这条对着一个**真能走到**的坑：
   在长主题里翻到第 30 张 → 切到一个只有 12 张的主题（`enterSession` 把 cur 归 0，
   但落盘的 lastCur 还是 30）→ 关掉再打开 ⇒ 恢复成"第 30 张"= 不存在
   ⇒ 中间显示「没有照片」，在用户眼里就是**打开工作台白屏**，还说不出为什么。
   所以这里专门塞一个**超范围的下标**进去，量它会不会被夹回来。 */
console.log('\n[11] 恢复上次状态');
{
  await page.addInitScript(() => {
    const orig = window.api.getConfig;
    window.api.getConfig = async () => ({
      ...(await orig()),
      lastSession: '主题A',
      lastCur: 999,        // ← 故意超范围（主题A 只有 40 张）
    });
  });
  await page.reload();
  await page.waitForTimeout(1600);
  const txt = await page.evaluate(() => document.body.innerText);
  check('★ 上次的下标超范围时不许显示「没有照片」', !/没有照片/.test(txt),
    txt.replace(/\n/g, ' ').slice(0, 110),
    '恢复成了不存在的第 N 张 ⇒ 用户看到的就是"打开就白屏"');
  const nm = await curName();
  check('★ 超范围的下标被夹到最后一张（不是第 0 张也不是空）', /DSCF1039/.test(nm),
    nm || '(没读到)', `期望夹到 DSCF1039（40 张里的最后一张），实际 ${nm || '空'}`);
}

/* ---------- 12. 导入照片（左栏入口 → 对话框 → 预演 → 真导入 → 落到新主题） ---------- */
/* ★ 这一组存在的理由：导入是**唯一一个会真写盘、真动几百个文件**的功能。
   它坏掉的方式不是"界面不好看"，是"片导错地方 / 用户以为没导进去"。所以四条都盯着行为：
     ① 入口在**没进主题**时也点得到（空库/新库恰恰是最需要导入的时候）
     ② 先看后拷（没预演过，开始导入是禁用的）
     ③ 进度**在跑的过程中**就到界面（看不到进度 = 用户以为死机 = 去强杀）
     ④ 跑完直接落在新主题、左栏也刷新了 */
console.log('\n[12] 导入照片');
{
  const back = page.locator('button', { hasText: '主题列表' }).first();
  if (await back.count()) {
    await back.click();
    await page.waitForTimeout(600);
  }
  const homeTxt = await page.evaluate(() => document.body.innerText);
  const openBtn = page.locator('[data-import-open]').first();
  check('★ 在「主题列表」页（**没进任何主题**）左栏也常显、也点得到导入',
    /共 2 个主题/.test(homeTxt) && (await openBtn.count()) > 0,
    '', '左栏还挂在 sessionName 上 ⇒ 空库/新库里"导入照片"永远点不到（而新库最需要它）');
  if (await openBtn.count()) await openBtn.click();
  await page.waitForTimeout(700);
  check('★ 点开有导入对话框', (await page.locator('[data-import-dialog]').count()) > 0, '',
    '点了没反应');
  const cardBtn = page.locator('[data-card]').first();
  const cardTxt = ((await cardBtn.innerText().catch(() => '')) || '').replace(/\n/g, ' ');
  check('★ 自动扫到卡、还把张数摆出来（不用用户手敲路径）',
    (await cardBtn.count()) > 0 && /100_FUJI/.test(cardTxt) && /524/.test(cardTxt),
    cardTxt, '没扫到卡 ⇒ 用户得自己填源目录');
  await page.locator('[data-import-field="主题"]').fill('旅行');
  await page.locator('[data-import-field="地点"]').fill('长洲岛');
  const runBtn = page.locator('[data-import-run]');
  check('★ 「开始导入」在没预演之前是禁用的（先看后拷）',
    (await runBtn.count()) > 0 && (await runBtn.isDisabled()), '',
    '没看计划就能直接开拷 ⇒ 几百张往盘上写之前连"拷到哪"都不知道');
  await page.locator('[data-import-preview]').click();
  await page.waitForTimeout(800);
  const planTxt = ((await page.locator('[data-import-plan]').innerText().catch(() => '')) || '')
    .replace(/\n/g, ' ');
  check('★ 预演把「要拷几个 / 多大 / 拷到哪」摆出来了',
    /524/.test(planTxt) && /12\.34 GB/.test(planTxt) && /2026-04-24_旅行_长洲岛/.test(planTxt),
    planTxt.slice(0, 120), '预览说不出"要拷多少 / 拷到哪"');
  check('★ 预演**没有**真拷（一次真导入都没发出去）',
    (await page.evaluate(() => window.__importRuns || 0)) === 0, '',
    '预演就真拷了 —— 那就不叫"先看后拷"了');
  check('预演之后「开始导入」可用了', !(await runBtn.isDisabled()));
  await runBtn.click();
  await page.waitForTimeout(320);
  const logTxt = ((await page.locator('[data-import-log]').innerText().catch(() => '')) || '')
    .replace(/\n/g, ' ');
  check('★ 复制过程中进度**真的到了界面**（不是等跑完才给）',
    /已复制 2\.30 GB/.test(logTxt), logTxt.slice(0, 120),
    '进度推不过来 ⇒ 用户看着像死机，会去强杀 —— 而这正是最不该中断的一步');
  /* ★ 进度条：从日志**派生**（`100/524` ⇒ 19%）。断言的是**算出来的宽度**，
     不是"那个 div 在不在" —— 后者挡不住"条永远停在 0%"这一类。 */
  const pct = await page.evaluate(() => {
    const f = document.querySelector('[data-import-fill]');
    return f ? Math.round(parseFloat(f.style.width) || 0) : -1;
  });
  check('★ 进度条跟着走（100/524 ⇒ 19%）', pct === 19, `${pct}%`,
    `期望 19%，实际 ${pct}% —— 条不动就等于没进度，用户还是不知道跑到哪了`);
  await page.waitForTimeout(1600);
  check('★ 导入跑完对话框自己关了', (await page.locator('[data-import-dialog]').count()) === 0);
  const nm = await curName();
  check('★ 导完**直接进新主题**（新主题的照片读出来了，没停在家页）',
    /DSCF3000/.test(nm), nm || '(没读到)',
    `期望新主题的照片 DSCF3000，实际 ${nm || '空'}`);
  const after = await page.evaluate(() => document.body.innerText);
  check('★ 新主题立刻出现在左栏「图库目录」里（列表真刷新了）',
    /2026-04-24_旅行_长洲岛/.test(after), '', '左栏没刷新 ⇒ 用户以为白导了');
  check('★ 导入没把界面弄炸（还在，不是白屏）',
    (await page.evaluate(() => (document.getElementById('root')?.innerHTML || '').length)) > 500);
}

/* ---------- 13. 右栏：基准默认 / 恢复默认 / 存到主题 ---------- */
/* ★ 这一组对着 09-15 修的三个真问题：
   ① **基准成色一支都没选中**：前端默认发 `'all'`，而引擎基准表（`config.BASE_TABLE`）
      里没有这一支 ⇒ `stocks.resolve_base` **静默**回落成 `BASE_NONE`（"不套基准"）
      ⇒ 默认出图等于"什么都没套"，界面上四支**一支都不亮**（画面错了还看不出来）。
      修法：默认值**由引擎给**（`/bases` 每条带 `isDefault`），前端不许写死基准名。
   ② 右下角「恢复默认」「存到主题」两个按钮**没有 onClick**（点了什么都不发生）——
      典型"死按钮"：界面在、功能不在，最难自己发现。
   ③ 「存到主题」要是**只写不读**（存了不套回来）就是存了个寂寞 ——
      所以要连"切走再回来，配方真套回来了"一起钉。 */
console.log('\n[13] 右栏：基准默认 / 恢复默认 / 存到主题');
{
  /* ⚠ 前面 [10] 为了测"引擎没起"注入过 `__FAIL_HEALTH = true`（initScript 会一直生效），
     这里必须显式关掉 —— 否则调色台只剩一句「引擎未启动」，右栏根本没滑杆和按钮可测。
     （这也是本组最容易写出"假绿"的地方：不关它，下面每条都测到空气。） */
  await page.addInitScript(() => {
    window.__FAIL_HEALTH = false;
  });
  await page.reload();
  await page.waitForTimeout(1800);
  const gradeTab3 = page.locator('button', { hasText: '调色台' }).first();
  if (await gradeTab3.count()) {
    await gradeTab3.click();
    await page.waitForTimeout(1500);
  }

  /* ---- ① 基准：有且只有一支选中，且是引擎标了 isDefault 的那支 ---- */
  const baseState = await page.evaluate(() => {
    const all = [...document.querySelectorAll('[data-base]')];
    return {
      n: all.length,
      names: all.map((x) => x.getAttribute('data-base')),
      on: all.filter((x) => x.getAttribute('data-base-on') === '1')
        .map((x) => x.getAttribute('data-base')),
    };
  });
  check('右栏列出了基准（这条不成立，下面全是空转）', baseState.n >= 3, `${baseState.n} 支`);
  check('★ 基准**有且只有一支**是选中的',
    baseState.on.length === 1, `选中 [${baseState.on.join(',')}] / 共 ${baseState.n} 支`,
    '一支都不选（或选了两支）⇒ 引擎收到无效名字、静默按"不套基准"出图，界面上还看不出来');
  /* ★ 关键：mock 里默认那支是**最后一个**（照 `config.BASE_TABLE` 的顺序抄）——
     所以这条能区分"读了引擎的 isDefault"和"腿短取了列表第一个"。 */
  check('★ 选中的是**引擎给的默认那支**（不是前端写死的名字、也不是列表第一个）',
    baseState.on[0] === 'BASE_FULL' && baseState.names[0] !== 'BASE_FULL',
    `选中 ${baseState.on[0]}；列表顺序 [${baseState.names.join(',')}]`,
    '前端自己写死基准名（过去写死「all」⇒ 引擎静默回落「不套基准」）');

  /* ---- ② 「恢复默认」：滑杆真回出厂，而且**不自动出图** ---- */
  const sl3 = page.locator('[role="slider"]');
  const nSl3 = await sl3.count();
  check('右栏有滑杆可拖（否则下面两条是空转）', nSl3 > 0, `${nSl3} 根`);
  const v0 = await sl3.first().getAttribute('aria-valuenow');
  await sl3.first().focus();
  for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(400);
  const v1 = await sl3.first().getAttribute('aria-valuenow');
  check('先把它拖离出厂值（否则"恢复默认"测不出东西）', !!v1 && v1 !== v0, `${v0} → ${v1}`);
  const rBefore = await page.evaluate(() => window.__renders || 0);
  await page.locator('button', { hasText: '恢复默认' }).first().click();
  await page.waitForTimeout(500);
  const v2 = await sl3.first().getAttribute('aria-valuenow');
  check('★ 「恢复默认」真把滑杆拉回出厂值', v2 === v0, `${v1} → ${v2}（出厂 ${v0}）`,
    '按钮没接线（09-15 之前它**没有 onClick**，点了什么都不发生）');
  check('★ 「恢复默认」不自动出图（沿用"只有两个触发点"）',
    (await page.evaluate(() => window.__renders || 0)) === rBefore, '',
    '恢复默认顺手出了一张 —— 违反"只有两个触发点"');

  /* ---- ③ 「存到主题」：真写进去；切走再回来真套回 ---- */
  await sl3.first().focus();
  for (let i = 0; i < 5; i++) await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(400);
  const vSaved = await sl3.first().getAttribute('aria-valuenow');
  check('先拖到一个"记得住"的值（否则下面断言没意义）', !!vSaved && vSaved !== v0, String(vSaved));
  await page.locator('button', { hasText: '存到主题' }).first().click();
  await page.waitForTimeout(500);
  const saved = await page.evaluate(() => window.__grades || {});
  const keyA = Object.keys(saved)[0];
  const savedVals = keyA ? Object.values(saved[keyA].params || {}) : [];
  check('★ 「存到主题」真写进去了（参数不是空对象）',
    !!keyA && savedVals.length >= 1,
    keyA ? `${keyA} ⇒ ${JSON.stringify(saved[keyA].params)}` : '(一条都没存)',
    '按钮没接线，或者存了个空参数（等于存了没用的东西）');
  check('★ 存下来的就是**刚才拧到的那个值**（不是出厂值、不是别的）',
    savedVals.map(String).includes(String(vSaved)),
    `存了 ${JSON.stringify(savedVals)}，刚拧到 ${vSaved}`,
    '存的不是当前值 ⇒ 下次套回来是错的');

  /* 切到主题B（**没存过**）⇒ 应该回出厂；再切回主题A ⇒ 套回刚才存的那份 */
  const openTheme3 = async (n) => {
    const b = page.locator('button', { hasText: n }).first();
    if (await b.count()) {
      await b.click();
      await page.waitForTimeout(1000);
    }
  };
  await openTheme3('主题B');
  const vB = await page.locator('[role="slider"]').first().getAttribute('aria-valuenow');
  check('★ 切到**没存过**的主题 ⇒ 滑杆回出厂（不把上一个主题的调整带过去）',
    vB === v0, `${vB}（出厂 ${v0}）`,
    '主题之间串味 ⇒ 在 A 里拧过的滑杆跟着进了 B');
  await openTheme3('主题A');
  const vBack = await page.locator('[role="slider"]').first().getAttribute('aria-valuenow');
  check('★ 切回主题A ⇒ **套回存过的那份配方**（存了就得用上）',
    vBack === vSaved, `${vBack}（存的是 ${vSaved}）`,
    '只写不读 = 存了个寂寞（本项目最忌的"看着对、其实对不上"）');
  check('★ 这一轮也没把界面弄炸（右栏还在）',
    (await page.locator('button', { hasText: '恢复默认' }).count()) > 0);

  /* ---- ④ 存过的旧配方里 base 是个**引擎不认的名字** ⇒ 进主题必须校验、回默认 ----
     ★ 为什么单列一条：配置是"人能手改、旧版本也写过"的东西（旧版前端就写死过 `'all'`）。
       引擎对不认得的名字是**静默**回落成「不套基准」的（`stocks.resolve_base`：
       既不在表里就取 `BASE_NONE`）⇒ 不校验的后果是"一进这个主题，四支基准一支都不亮、
       出图悄悄变成什么都没套"，而且**看不出哪里不对**。
       —— 跟 ①② 是同一个坑，只是入口从"初值"换成了"存过的旧值"。 */
  await page.addInitScript(() => {
    window.__grades = {
      '主题A': { stock: 'portra400', base: 'all', params: { SPEK_PE_SHIFT: 1.1 } },
    };
  });
  await page.reload();
  await page.waitForTimeout(1800);
  const gradeTab4 = page.locator('button', { hasText: '调色台' }).first();
  if (await gradeTab4.count()) {
    await gradeTab4.click();
    await page.waitForTimeout(1500);
  }
  await openTheme3('主题A');
  await page.waitForTimeout(800);
  const badBase = await page.evaluate(() => {
    const all = [...document.querySelectorAll('[data-base]')];
    return all.filter((x) => x.getAttribute('data-base-on') === '1')
      .map((x) => x.getAttribute('data-base'));
  });
  check('★ 存过的配方里 base 是引擎不认的名字 ⇒ 进主题时**回引擎默认**（不是原样照信）',
    badBase.length === 1 && badBase[0] === 'BASE_FULL', `选中 [${badBase.join(',')}]`,
    '原样信配置 ⇒ 界面上四支基准一支都不亮、出图静默变成"不套基准"');
  /* ★ 界面亮不亮只是**间接证据**（状态对、请求里照样可能是错的）——
     所以再真点一次渲染，看**发给引擎的 base**。 */
  const rBtn4 = page.locator('button', { hasText: '渲染' }).first();
  check('渲染条在（否则下面那条测的是空气）', (await rBtn4.count()) > 0);
  if (await rBtn4.count()) {
    await rBtn4.click();
    await page.waitForTimeout(1000);
    const a4 = await renderArgs();
    const last4 = a4[a4.length - 1] || {};
    check('★ 渲染请求里带的 base 是**合法名字**（不是那个存错的）',
      last4.base === 'BASE_FULL', `base=${JSON.stringify(last4.base)}`,
      '把引擎不认的名字原样发出去 ⇒ 引擎静默按"不套基准"出图，画面错了都不知道');
  }
}

/* ---------- 14. 大图缩放（09-15 SV 选「B」） ---------- */
/* ★ 为什么要这一组：以前**全仓库搜缩放零命中** ⇒ 大图永远只能"整张塞进窗口"。
   而验收皮肤**必须看 1:1** —— 颗粒粗细、磨皮够不够、对焦在不在眼睛上，
   在缩略图里根本看不出来。参照物是 spektrafilm 的 `100%/200%/400%/重置视图`。
   ⚠ 这里断言的**不是"有个按钮"**，而是那个徽标上的数：
     口径 = 「1 个屏幕像素对应几个图像像素」，点「1:1」之后**必须读作 100%**。
     用盒子宽高去算这个数**一定是错的** —— `object-fit:contain` 会留黑边。 */
console.log('\n[14] 大图缩放（适应 / 1:1 / 滚轮）');
{
  const wraps = page.locator('[data-zoom-pct]');
  const nWraps = await wraps.count();
  check('★ 分屏两栏各有缩放控件（原图 / 调色后，一份实现两个入口）', nWraps === 2,
    `${nWraps} 个`,
    '大图不能放大 ⇒ 验收皮肤只能看缩略图（颗粒/磨皮/对焦都看不出来）');

  const readZoom = () =>
    page.evaluate(() => {
      const all = [...document.querySelectorAll('[data-zoom-pct]')];
      const el = all[all.length - 1];
      if (!el) return null;
      return {
        pct: Number(el.getAttribute('data-zoom-pct')),
        state: el.getAttribute('data-zoom'),
      };
    });

  const fit = await readZoom();
  check('切进调色台时是「适应」（先看整张构图）', !!fit && fit.state === 'fit',
    JSON.stringify(fit), '一进来就是放大的 ⇒ 根本看不到构图');

  const oneBtn = page.locator('[data-zoom-btn="1to1"]').last();
  check('「调色后」栏有「1:1」按钮', (await oneBtn.count()) > 0);
  if ((await oneBtn.count()) > 0) {
    await oneBtn.click();
    await page.waitForTimeout(250);
    const z = await readZoom();
    check('★★ 点「1:1」⇒ 徽标读作 100%（口径：1 屏幕像素 = 1 图像像素）',
      !!z && z.pct === 100, `读到 ${z && z.pct}%`,
      '不是 100% ⇒ 要么拿盒子宽高当基准（contain 留黑边 ⇒ 必然偏），要么压根没算基准倍率');
    check('★ 「1:1」确实改动了显示（和「适应」不是同一个数）',
      !!z && !!fit && z.pct !== fit.pct, `${fit && fit.pct}% → ${z && z.pct}%`);

    /* ★★ 滚轮：两件事一起验 ——
       ① 真的放大了 ② **preventDefault 生效**（页面没跟着滚）。
       ②才是重点：React 的 `onWheel` 是 **passive** 的，在里面 preventDefault 无效，
       页面会跟着滚一下。所以必须用原生监听 + `{passive:false}`。
       断言 `ev.defaultPrevented` 直接测到了这一点，而不是"看着像没滚"。 */
    const prevented = await page.evaluate(() => {
      const all = [...document.querySelectorAll('[data-zoom-pct]')];
      const el = all[all.length - 1];
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const ev = new WheelEvent('wheel', {
        deltaY: -120,
        clientX: r.left + r.width / 2,
        clientY: r.top + r.height / 2,
        bubbles: true,
        cancelable: true,
      });
      el.dispatchEvent(ev);
      return ev.defaultPrevented;
    });
    /* ⚠ 必须**分两步读**：`defaultPrevented` 是同步的（当场能拿到），
       但徽标是 React state ⇒ 同一个 tick 里读还是旧值 —— 第一版就栽在这，
       报出「100% → 100%」，看着像"滚轮没生效"，其实只是没等重渲染。 */
    await page.waitForTimeout(250);
    const wPct = await page.evaluate(() => {
      const all = [...document.querySelectorAll('[data-zoom-pct]')];
      const el = all[all.length - 1];
      return el ? Number(el.getAttribute('data-zoom-pct')) : -1;
    });
    check('★ 滚轮真的放大了（徽标变大）', !!z && wPct > z.pct,
      z ? `${z.pct}% → ${wPct}%` : '(没测到)');
    check('★★ 滚轮被拦住了（页面没跟着滚）—— 原生监听 + passive:false',
      prevented === true, String(prevented),
      'React 的 onWheel 是 passive 的 ⇒ 在里面 preventDefault 无效，页面会跟着滚');

    /* ★★ 这条表面上是"复位"，实际盯的是**放大之后控件还点得动吗** ——
       第一版用 `setPointerCapture` 把指针捕获到外层容器，浮在里面的按钮就再也
       收不到 click（放大 → 按钮全失灵）。失败信息必须写清这一点，不然下一个人
       会以为"只是复位坏了"，改错地方。 */
    const fitBtn = page.locator('[data-zoom-btn="fit"]').last();
    await fitBtn.click();
    await page.waitForTimeout(250);
    const back = await readZoom();
    check('★ 点「适应」能复位（数值和刚进来时一模一样）',
      !!back && back.state === 'fit' && !!fit && back.pct === fit.pct,
      JSON.stringify(back),
      '放大之后就点不动了/不复位 ⇒ 多半是「拖动平移」把指针捕获到了外层容器（`setPointerCapture`），'
        + '把按钮的 click 吃掉了；或者复位没把倍率和位移一起归零');

    /* ★ 双击切换（缩小状态下双击 ⇒ 1:1）—— 顺手验一下它和按钮走的是同一套状态 */
    const dbl = page.locator('[data-zoom-pct]').last();
    await dbl.dblclick();
    await page.waitForTimeout(250);
    const z2 = await readZoom();
    check('★ 双击大图 ⇒ 进 1:1（和按钮走同一套状态）', !!z2 && z2.pct === 100,
      `读到 ${z2 && z2.pct}%`);
  }

  /* ★ 「原图」栏必须也是同一套（一份实现两条入口）—— 左栏要是没缩放，
     就没法"原图和成片同倍率对比"，那这个功能的一半价值就没了。 */
  const oneBtn0 = page.locator('[data-zoom-btn="1to1"]').first();
  if ((await oneBtn0.count()) > 0) {
    await oneBtn0.click();
    await page.waitForTimeout(250);
    const z0 = await page.evaluate(() => {
      const el = document.querySelectorAll('[data-zoom-pct]')[0];
      return el ? Number(el.getAttribute('data-zoom-pct')) : -1;
    });
    check('★ 「原图」栏也是同一套缩放（左栏也要能 1:1）', z0 === 100, `读到 ${z0}%`);
  }
}

/* ---------- 15. 相纸（09-15 SV 选「C」） ---------- */
/* ★ 为什么要这一组：一张真卷出图 = **(负片, 相纸)** 二元组。相纸是**最终成色的另一半** ——
   同一卷负片印在不同纸上，是两套不同的颜色（人像最经典的就是「柯达卷 + Portra Endura」
   和「富士卷 + Crystal Archive」两套脸色）。原来只开放了负片那一半。
   ★★ 这一组**不测"有没有下拉框"**，测的是三件"看着对、其实对不上"的事：
     ① 换卷之后相纸**有没有自动跟到新卷的配套纸**（不是留在上一卷那张）
     ② 换纸之后**发出去的请求里带的是不是新纸**（下拉亮对了、请求没带 = 画面不变）
     ③ 中性卷下这一栏**是不是真不显示**（引擎对中性卷返回空表）
   ⚠ mock 里 `portra400` 的配套纸**恰好是列表第一张**、`fuji_c200` 的配套却是**第三张**
     ⇒ 只有这样才区分得出「读了引擎的 isDefault」和「腿短取了 option[0]」。 */
console.log('\n[15] 相纸（换纸真的换画面吗）');
{
  /* ⚠ 前面 [10] 为测"引擎没起"注入过 `__FAIL_HEALTH = true`（initScript 会一直生效），
     [13] 关掉了 —— 但这里**再显式关一次**，别依赖前面某组的副作用（那是"假绿"的温床）。 */
  await page.addInitScript(() => {
    window.__FAIL_HEALTH = false;
  });
  await page.reload();
  await page.waitForTimeout(1800);
  const tab5 = page.locator('button', { hasText: '调色台' }).first();
  if (await tab5.count()) {
    await tab5.click();
    await page.waitForTimeout(1500);
  }

  const paperState = () =>
    page.evaluate(() => {
      const all = [...document.querySelectorAll('[data-paper]')];
      if (!all.length) return null;
      const el = all[0];
      return {
        n: Number(el.getAttribute('data-paper-n')),
        on: el.getAttribute('data-paper-on'),
        opts: [...el.querySelectorAll('option')].map((o) => o.value),
      };
    });
  const openStock5 = async (name) => {
    const b = page.locator(`[data-stock="${name}"]`).first();
    if (await b.count()) {
      await b.click();
      await page.waitForTimeout(900);
    }
  };

  const p0 = await paperState();
  check('★ 真卷下出现相纸下拉（这条不成立，下面全是空转）', !!p0,
    p0 ? `${p0.n} 张` : '(没有 [data-paper])',
    '相纸是成色的另一半，没有它就只能用卷表里写死的那张纸');
  if (!p0) {
    /* 没有下拉就别硬测 —— 报一条红收工，别让下面几条以"空气"通过 */
  } else {
    check('★ 相纸表照引擎来（真卷 8 张）', p0.n === 8, `${p0.n} 张`);
    check('★ 当前用的是**这一卷的配套纸**（Portra 400 ⇒ 柯达 Portra Endura）',
      p0.on === 'kodak_portra_endura', String(p0.on),
      '初值不是引擎标的 isDefault ⇒ 界面显示的纸和实际印的纸不是同一张');

    /* ---- ① 换卷 ⇒ 相纸要跟着换（配套纸跟着卷走） ---- */
    await openStock5('fuji_c200');
    const p1 = await paperState();
    check('★★ 换卷之后相纸**自动跟到新卷的配套纸**（C200 ⇒ 富士 Crystal Archive II）',
      !!p1 && p1.on === 'fujifilm_crystal_archive_typeii', p1 ? String(p1.on) : '(没了)',
      '留在上一卷那张 ⇒ 出现"C200 卷 + Portra 纸"这种不存在的组合');
    check('★ 而它**不是列表第一张**（证明确实读了引擎的 isDefault，不是腿短取 option[0]）',
      !!p1 && p1.opts[0] !== p1.on, p1 ? `第一张是 ${p1.opts[0]}、用的是 ${p1.on}` : '',
      '取 list[0] ⇒ 引擎换了配套纸就静默错位（mock 里故意把配套纸排在第三张）');

    /* ---- ② 换纸 ⇒ 请求里要带新纸；而且**不自动出图** ---- */
    const rBefore5 = await page.evaluate(() => window.__renders || 0);
    await page.locator('[data-paper]').first().selectOption('kodak_supra_endura');
    await page.waitForTimeout(400);
    const p2 = await paperState();
    check('★ 选了另一张纸 ⇒ 下拉显示跟着变（Supra Endura）',
      !!p2 && p2.on === 'kodak_supra_endura', p2 ? String(p2.on) : '');
    check('★ 换相纸**不自动出图**（沿用"只有两个触发点"：右栏「渲染」/ 切进调色台）',
      (await page.evaluate(() => window.__renders || 0)) === rBefore5, '',
      '换纸顺手出了一张 —— 违反"只有两个触发点"');

    const rBtn5 = page.locator('button', { hasText: '渲染' }).first();
    check('渲染按钮在（否则下面那条测的是空气）', (await rBtn5.count()) > 0);
    if (await rBtn5.count()) {
      await rBtn5.click();
      await page.waitForTimeout(1000);
      const a5 = await renderArgs();
      const last5 = a5[a5.length - 1] || {};
      check('★★ 渲染请求里带的相纸**就是刚选的那张**（不是配套纸、也不是空）',
        last5.paper === 'kodak_supra_endura', `paper=${JSON.stringify(last5.paper)}`,
        '下拉亮对了、请求却没带 ⇒ 画面不变，用户会以为"这张纸没效果"（其实是根本没发出去）');
    }

    /* ---- ③ 「恢复默认」⇒ 相纸回这一卷的配套纸（卷不动） ---- */
    await page.locator('button', { hasText: '恢复默认' }).first().click();
    await page.waitForTimeout(500);
    const p3 = await paperState();
    check('★ 「恢复默认」把相纸拉回**本卷配套纸**（C200 ⇒ 富士 Crystal Archive II）',
      !!p3 && p3.on === 'fujifilm_crystal_archive_typeii', p3 ? String(p3.on) : '',
      '只清滑杆、不管相纸 ⇒ 用户以为回出厂了，其实还印在上一张纸上');

    /* ---- ④ 中性卷 ⇒ 这一栏整块不显示（引擎返回空表） ---- */
    await openStock5('neutral');
    const nNeutral = await page.locator('[data-paper]').count();
    check('★ 中性卷下**没有相纸这一栏**（中性卷走 Lab 引擎，没有"负片 + 相纸"这回事）',
      nNeutral === 0, `${nNeutral} 个下拉`,
      '中性卷也列 8 张纸、一张都不亮 ⇒ 把拧不动的开关摆给用户');

    await openStock5('portra400');
    const nBack = await page.locator('[data-paper]').count();
    check('★ 切回真卷 ⇒ 相纸栏又出来（不是一次性渲染完就没了）', nBack === 1, `${nBack} 个`);
  }
}

/* ---------- 16. 加入目录（库外目录：原地读，不复制） ----------
   ★ SV 原话：*"如果一张照片已经在我的电脑中，我可以通过加这个目录让这个目录[出现在]
     图片库那一栏中"*。这是**原地读**，不是复制 —— 和「导入照片」（复制归档）两回事。
   ★ 这一组的靶心是**路径**：库外目录**不在** `libRoot` 底下。要是 `enterSession`
     还自己拼 `库根\名字`，点进去读的是一个**不存在**的目录 ⇒ 0 张照片，
     而且看着像"这个主题是空的"—— 本项目最像"点了没反应"的一类假象。
   ⇒ 所以探针取「列图时用的那条路径」（`window.__listPaths`），**不是**看照片名：
     名字对不对不足以说明路径对不对。 */
console.log('\n[16] 加入目录（库外目录：原地读）');
{
  await page.addInitScript(() => {
    window.__extraRoots = ['D:\\拍摄素材\\厦门_外拍'];
  });
  await page.reload();
  await page.waitForTimeout(1800);

  const addBtn = page.locator('[data-add-dir]').first();
  check('★ 左栏有「加入目录」入口（没有入口 = 这功能等于不存在）',
    (await addBtn.count()) > 0, '',
    '入口不摆出来，用户永远找不到（"导入入口点不到"那次的翻版）');

  const extCount = await page.locator('[data-session-ext]').count();
  check('★ 库外目录出现在「图库目录」里（和库内主题并排）',
    extCount >= 1, `${extCount} 条`, '加进来的目录不出现 ⇒ 用户以为白加了');
  if (extCount === 0) {
    check('（后面几条依赖列表里有库外条目）', false, '', '列表里没有库外条目，后面全是空转');
  } else {
    const badge = await page.locator('[data-session-badge]').first().innerText();
    check('★ 库外条目打了「库外」徽标（跟库里的主题一眼分得开）',
      /库外/.test(badge), badge, '不区分 ⇒ 用户以为那些片子已经被复制进库了');

    /* ---- ① 点它 ⇒ 列图用的必须是**它自己的路径** ---- */
    await page.evaluate(() => { window.__listPaths = []; });
    await page.locator('[data-session-ext]').first().click();
    await page.waitForTimeout(1200);
    const paths = await page.evaluate(() => window.__listPaths || []);
    const used = paths[paths.length - 1];
    check('★★★ 点库外条目 ⇒ 读的是**它自己的路径**（不是 `D:\\lib\\名字`）',
      used === 'D:\\拍摄素材\\厦门_外拍', `读到 ${JSON.stringify(used)}`,
      `读到 ${JSON.stringify(used)} ⇒ 拼成"库根\\名字"了：库外目录进去永远 0 张照片，` +
        '看着像空主题，看不出是路径拼错');

    /* ---- ② 「加入目录」真落盘（否则重启就没了） ---- */
    await page.evaluate(() => {
      window.__setConfig = null;
      window.__pickDir = 'D:\\另一个\\外拍';
    });
    await addBtn.click();
    await page.waitForTimeout(1200);
    const saved = await page.evaluate(() => window.__setConfig || {});
    check('★ 「加入目录」把目录写进了配置（下次开台子还在）',
      Array.isArray(saved.extraRoots) && saved.extraRoots.includes('D:\\另一个\\外拍'),
      JSON.stringify(saved.extraRoots || null), '没落盘 ⇒ 加了个寂寞，重启就没了');
    const ext2 = await page.locator('[data-session-ext]').count();
    check('★ 加完立刻出现在列表里（重扫真重扫了，不是只写配置）',
      ext2 >= 2, `${ext2} 条`, '只写配置不重扫 ⇒ 用户以为没加成，又去加一遍');

    /* ---- ③ 「×」只从列表去掉，**不删文件** ---- */
    const del = page.locator('[data-session-del]').first();
    check('★ 库外条目有「×」（加错了能拿掉）', (await del.count()) > 0);
    const delTitle = (await del.getAttribute('title')) || '';
    check('★★ 「×」的 tip 明说**不删任何文件**（用户看到 × 第一反应是怕删片子）',
      /不删任何文件/.test(delTitle), delTitle.slice(0, 40),
      '没说清 ⇒ 用户不敢点，或者点了以为片子被删了');
    await page.evaluate(() => { window.__setConfig = null; });
    await del.click();
    await page.waitForTimeout(1200);
    const saved2 = await page.evaluate(() => window.__setConfig || {});
    check('★ 移除真的改配置（那条从列表里去掉了）',
      Array.isArray(saved2.extraRoots) && saved2.extraRoots.length === 1,
      JSON.stringify(saved2.extraRoots || null), '不移除 ⇒ 加错了就永远赖在那儿');
  }
}

/* ---------- 收尾：整轮跑下来有没有未捕获报错 ---------- */
/* ★ 为什么要放在**最后**再查一次：中间那些组会翻图/切主题/切台，
   每次都会走 `saveLast()` 那条链。第一组只查了"刚打开时"有没有报错，
   那时候这条链还没跑过 —— 09-15 就是靠这个发现 mock 漏了 `setConfig`。
   （过滤掉 preload/api 相关：浏览器里跑本来就没有 Electron 那层。） */
{
  /* ⚠ 要滤掉"资源 404"这一类：这个自检跑在**浏览器**里、图库目录（`D:\lib`）
     是 mock 编出来的，选片台大图按 `file:///` 直读那两个 JPG 必然报
     `Failed to load resource: net::ERR_FILE_NOT_FOUND` —— 那是**环境**没有文件，
     不是代码错。真要盯的是 JS 自己的未捕获异常。 */
  const real = pageErrors.filter(
    (e) => !/api|preload/i.test(e) && !/Failed to load resource/i.test(e)
  );
  check('★ 整轮跑下来没有未捕获报错（翻图/切主题那条链也没炸）', real.length === 0,
    real.slice(0, 2).join(' | ').slice(0, 200),
    `${real.length} 条，例如：${real.slice(0, 2).join(' | ').slice(0, 200)}`);
}

console.log('\n' + '-'.repeat(50));
if (fail === 0) console.log(`全部通过（${pass} 项）`);
else {
  console.log(`${fail} 项失败 / 共 ${pass + fail} 项：`);
  for (const f of failures) console.log('   ✗ ' + f);
}
console.log('-'.repeat(50) + '\n');

await browser.close();
process.exit(fail === 0 ? 0 : 1);
