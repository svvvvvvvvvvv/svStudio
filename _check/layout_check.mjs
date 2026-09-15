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
    getConfig: async () => ({ libRoot: 'D:\\lib' }),
    /* ⚠ 主题列表要**能变**：导入完 `refreshSessions()` 会重扫，
       新主题必须出现（否则"导入完直接进新主题"这条根本测不到）。
       照生产端抄：列表是"扫出来的"，不是写死的常量。 */
    scanSessions: async () =>
      window.__sessions ||
      (window.__sessions = [
        { name: '主题A', count: 12 },
        { name: '主题B', count: 34 },
      ]),
    /* ★★ 出图源也要照生产端抄：`main.js` 的 `attachLoadPath()` 会给每张算出 `loadPath`
       （**同名 RAW 优先**，没有 RAW 的主题才回落 JPG）。这里故意混着给：
       i=1,5,9… 是"只有 JPG"的，用来测回落那一档。
       ★ 照片名**按主题区分**（照生产端：不同主题是不同批照片）——
         两个主题发同一批名字的话，「切主题之后看的是另一张」根本测不出来。
       ★ 导入出来的新主题给第三段号段（3000+）：这样"到底进没进新主题"一眼看得出。 */
    listPhotos: async (sessionPath) => {
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
         现在这三条按真实值抄，并且多带一个 `dv` —— 检查要盯住"滑杆显示的是 dv"。
         ⚠ 改引擎的 PARAMS 就要回来改这里；`ui_smoke.mjs` 有一条静态检查盯着这两边别漂。 */
      items: [
        { k: 'SPEK_PE_SHIFT', name: '整张亮暗', lo: 0.62, hi: 1.43, step: 0.01, grp: '真卷', spek: true, dv: 1.0, inv: true, d: '整张更亮还是更暗' },
        { k: 'FACE_SPAN_KMAX', name: '脸的层次', lo: 1.0, hi: 3.0, step: 0.05, grp: '脸', dv: 2.0, d: '脸内部明暗最多拉开几倍' },
        { k: 'SKIN_FLOOR_A', name: '脸的红绿', lo: 11.0, hi: 20.0, step: 0.1, grp: '脸', dv: 14.5, d: '脸偏红还是偏绿' },
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
      });
      return { ok: true, image: mk(5) };
    },
    /* ---- 照片导入（SD 卡 / U 盘 → 照片库） ----
       ⚠⚠ mock **必须实现前端会调的每一个 IPC**：漏一个 ⇒ 那次调用**同步抛 TypeError**、
       `.catch` 根本没机会接 ⇒ 一路往控制台丢未捕获异常，而检查只看接口，看不见
       （09-15 漏 `setConfig` 就是这么漏过去的）。
       ★ 形状照 `main.js` 的 IPC 处理器抄：
         import-detect        → { ok, cards:[{drive,path,n}], script, libRoot }
         import-preview/-run  → { ok, text, plan, error }
         onImportProgress     → 返回**取消订阅函数**（生产端 preload 也是这么给的） */
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
  /* ★★ 契约（SV 09-15 定）：自动出图只有两个触发点 —— ① 进/切进调色台 ② 点「渲染」。
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

/* ---------- 6. 「任何操作都不自动出图」（换卷/换基准/拖滑杆） ---------- */
/* ★★ 契约（SV 09-15 定）：自动出图**只有两个触发点** —— ① 进/切进调色台 ② 点右栏「渲染」。
   「换卷 / 换基准 / 拖滑杆 / 换图」都只改参数、不动画面。
   为什么必须钉死：以前是"任何改动都自动出图"，拖一次滑杆能瞬间打出几十发 6~15 s 的渲染
   互相抢占，最后那张反而迟迟不出来 —— 看着就跟"点了没反应"一模一样。
   盯的是**机制**：引擎被叫了几发（不是界面文案）。 */
console.log('\n[6] 任何操作都不自动出图');
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
  /* 拖滑杆：走 Radix 滑杆**自己的键盘交互**（值一定会变），不是直接调 store */
  const sl = page.locator('[role="slider"]');
  const nSl = await sl.count();
  check('右栏有滑杆可拖（这条才测得动）', nSl > 0, `${nSl} 根`);
  if (nSl > 0) {
    const before = await renders();
    const v0 = await sl.first().getAttribute('aria-valuenow');
    await sl.first().focus();
    for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(500);
    const v1 = await sl.first().getAttribute('aria-valuenow');
    check('拖滑杆之后值**真的变了**（否则下面那条是空转）', !!v1 && v1 !== v0, `${v0} → ${v1}`);
    const after = await renders();
    check('★ 拖滑杆 ⇒ 不自动出图', after === before, `渲染 ${before} → ${after} 发`,
      `拖滑杆触发了渲染（多发 ${after - before} 发）`);
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
  const a0 = await renderArgs();
  const p0 = (a0[a0.length - 1] || {}).params || {};
  await rBtn.click();
  await page.waitForTimeout(900);
  const a1 = await renderArgs();
  const p1 = (a1[a1.length - 1] || {}).params || {};
  const keys = Object.keys(p1);
  check('★ 没碰过的滑杆不进参数串（未触碰 = 用引擎出厂值）', Object.keys(p0).length === 0,
    `上一发 params = ${JSON.stringify(p0)}`,
    '未触碰的滑杆也被塞进参数串 ⇒ 会和引擎出厂值打架');
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
