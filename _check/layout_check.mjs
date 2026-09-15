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
  window.api = {
    logLine: async () => true,
    getConfig: async () => ({ libRoot: 'D:\\lib' }),
    scanSessions: async () => [
      { name: '主题A', count: 12 },
      { name: '主题B', count: 34 },
    ],
    /* ★★ 出图源也要照生产端抄：`main.js` 的 `attachLoadPath()` 会给每张算出 `loadPath`
       （**同名 RAW 优先**，没有 RAW 的主题才回落 JPG）。这里故意混着给：
       i=1,5,9… 是"只有 JPG"的，用来测回落那一档。
       ★ 照片名**按主题区分**（照生产端：不同主题是不同批照片）——
         两个主题发同一批名字的话，「切主题之后看的是另一张」根本测不出来。 */
    listPhotos: async (sessionPath) => {
      const theme = String(sessionPath || '').split(/[\\/]/).filter(Boolean).pop() || '主题A';
      const base = theme === '主题B' ? 2000 : 1000;
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
    engineBases: async () => ({
      ok: true,
      items: [
        { name: 'BASE_NONE', label: '不套基准', desc: '什么都不做' },
        { name: 'BASE_FULL', label: '全对齐', desc: '全段对齐' },
      ],
    }),
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
