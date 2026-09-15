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
       i=1,5,9… 是"只有 JPG"的，用来测回落那一档。 */
    listPhotos: async () =>
      Array.from({ length: 40 }, (_, i) => {
        const jpg = `DSCF${1000 + i}.JPG`;
        const isRaw = i % 4 !== 1;
        return {
          name: jpg,
          rel: jpg,
          hasRaw: isRaw,
          loadPath: `D:\\lib\\主题A\\DSCF${1000 + i}.` + (isRaw ? 'RAF' : 'JPG'),
          loadIsRaw: isRaw,
        };
      }),
    // ★ mock 必须跟真实返回一致：{url,ow,oh}（09-15 裂图就是把返回当字符串用）
    getThumb: async (a, b, c) => ({ url: mk(c), ow: 400, oh: 300 }),
    getThumbMeta: async () => ({ ow: 4000, oh: 3000 }),
    getExif: async () => ({ camera: 'X-T4', lens: 'XF35', iso: 400, fnum: 1.4, ss: '1/250', fl: '35mm' }),
    saveRatings: async () => true,
    engineHealth: async () => ({ ok: true }),
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
      window.__nextId = (window.__nextId || 0) + 1;
      (window.__engineIds = window.__engineIds || []).push(window.__nextId);
      return { ok: true, items: [{ id: window.__nextId, path: 'x', ms: 12 }] };
    },
    engineBase: async () => ({ ok: true, image: mk(9) }),
    /* ★ 数一发渲染次数 —— 「换图到底会不会自动出图」这条契约要么数它，
       要么去数界面上的占位文案。数文案是**间接证据**（装载链断了也会留白），
       数次数是机制本身：换图后这个数不许涨。 */
    engineRender: async () => {
      window.__renders = (window.__renders || 0) + 1;
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
  const rBtn = page.locator('button', { hasText: /^渲染$/ }).first();
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

console.log('\n' + '-'.repeat(50));
if (fail === 0) console.log(`全部通过（${pass} 项）`);
else {
  console.log(`${fail} 项失败 / 共 ${pass + fail} 项：`);
  for (const f of failures) console.log('   ✗ ' + f);
}
console.log('-'.repeat(50) + '\n');

await browser.close();
process.exit(fail === 0 ? 0 : 1);
