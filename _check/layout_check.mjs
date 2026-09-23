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
    /* ★ 照生产端抄：`extraRoots` = 左栏加进来的**文件夹**（绝对路径）。
       放在 window 上、方法里**现读** —— 测试中途改它才生效（同 __FAIL_HEALTH 的老办法）。
       ★★ 09-15 SV 选「B」：生产端**没有 `libRoot` 了** —— mock 也不许再给。给着的话
          自检是在测一个已经不存在的键，"左栏只列加过的文件夹"这条永远测不出来。 */
    getConfig: async () => ({ extraRoots: window.__extraRoots || [] }),
    /* ⚠ 列表要**能变**：`refreshSessions()` 重扫之后，新加进来的文件夹必须列出来
       （否则"加完立刻出现在列表里"这条根本测不到）。
       照生产端抄：列表是"扫出来的"，不是写死的常量。
       ★ 加进来的文件夹照 `main.js` 的 `extraSessions` 抄：带 `path`（真实路径）、
         `external`、`rootDir`；名字取路径最后一段。
         ⚠ 少一个字段，「点条目用的是它自己的路径吗」就测不到 ——
           而"路径拼错"正是这个功能最容易坏、又最像"没反应"的地方。 */
    scanSessions: async () => {
      const base = window.__sessions || (window.__sessions = [
        { name: '目录A', count: 12, path: 'D:\\lib\\目录A' },
        { name: '目录B', count: 34, path: 'D:\\lib\\目录B' },
      ]);
      const ex = window.__extraRoots || [];
      /* ★ 「一张 JPG 都没有、只有 RAW」的那种文件夹（照 `main.js` 的 `externalSessionEntry` 抄）：
         **`path` 就是源目录**（09-15 起不再指向预览缓存）—— 预览小图是另一条链
         （`viewFileOf` 去缓存取图），不影响列表/桶/星级。
         ⚠ 不照抄这一条，「拿哪个目录去建索引」和「建完重扫，待建标记要消失」都测不到。
         开关放 window 上、方法里现读。 */
      const rawDir = String(window.__rawDir || '').toLowerCase();
      return base.concat(ex.map((d) => {
        const e = {
          name: String(d).split(/[\\/]/).filter(Boolean).pop(),
          path: d,
          count: 40,
          external: true,
          rootDir: d,
        };
        if (rawDir && String(d).toLowerCase() === rawDir) {
          e.indexDir = 'C:\\cache\\extpreview\\idx@1';
          e.needsIndex = !window.__indexBuilt;
          e.rawCount = 3;
          e.count = 3;
        }
        return e;
      }));
    },
    /* ★★ 形状照生产端抄：`main.js` 的 `listPhotos()` 给每张片 `name` / `rel` / `loadPath`。
       ★ 照片名**按目录区分**（照生产端：不同目录是不同批照片）——
         两个目录发同一批名字的话，「切目录之后看的是另一张」根本测不出来。
       ★ 导入出来的新目录给第三段号段（3000+）：这样"到底进没进新目录"一眼看得出。 */
    listPhotos: async (sessionPath) => {
      /* ★ 记下**每次列图用的路径** —— 「点库外条目是不是用了它自己的真实路径」靠它断言。
         只看照片名不够：路径拼错时画面照样能出，很难判。 */
      (window.__listPaths || (window.__listPaths = [])).push(sessionPath);
      const theme = String(sessionPath || '').split(/[\\/]/).filter(Boolean).pop() || '目录A';
      const base = theme === '目录B' ? 2000 : /^\d{4}-/.test(theme) ? 3000 : 1000;
      /* 片 = RAW：`name` 是 RAW 的真实文件名，`rel` 是身份键（去扩展名、大写），
         `loadPath` 永远指向那张 RAW。 */
      return Array.from({ length: 40 }, (_, i) => {
        const stem = `DSCF${base + i}`;
        return {
          name: stem + '.RAF',
          rel: stem,
          loadPath: `D:\\lib\\${theme}\\${stem}.RAF`,
        };
      });
    },
    // ★ mock 必须跟真实返回一致：{url,ow,oh}（09-15 裂图就是把返回当字符串用）
    getThumb: async (a, b, c) => ({ url: mk(c), ow: 400, oh: 300 }),
    getExif: async () => ({ camera: 'X-T4', lens: 'XF35', iso: 400, fnum: 1.4, ss: '1/250', fl: '35mm' }),
    /* ★ 记下**落盘的那份星级** —— 「打星要存下来」这条靠它断言。
       只看界面上的星星是不够的：星星亮着、却没存，重启就没了（这类"看着对、其实丢了"最难发现）。 */
    saveRatings: async (r) => {
      window.__ratingsSaved = r;
      return true;
    },
    /* ⚠ 这个 mock 原来**漏了 `setConfig`** —— 而 `useStore` 里 `saveLast()` 每次翻图/切目录/
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
        { name: 'Portra400薄荷', label: 'Portra400 · 薄荷', desc: '暖调', engine: true },
        { name: 'C200青蓝', label: 'C200 · 青蓝', desc: '青绿', engine: true },
        { name: 'Ektar100浓彩', label: 'Ektar100 · 浓彩', desc: '浓', engine: true },
      ],
    }),
    /* ★★ 基准列表**照生产端数据**抄：`main.js` 转发引擎 `/bases`，引擎现在每条带
       `isDefault`（`config.BASE` 那条为 true，这里是 `BASE_FULL`）。
       前端只认这个来定初值 —— **不许自己写死基准名**
       （过去写死 `'all'`，而引擎的基准表里没有 `'all'` ⇒ `resolve_base` **静默**
        回落成 `BASE_NONE`「不套基准」⇒ 默认出图等于"什么都没套"，界面上还一支都选不中）。
       ⚠ 顺序也照 `config.BASE_TABLE` 抄：默认那支**故意不排第一个** ——
         只有这样才测得动"取的是 `isDefault` 那条"，而不是"腿短取 `list[0]`"。 */
    /* ★★ 曝光风格（09-23）：三条档，靶值从大师真片量出来（引擎 tone.STYLES 是唯一出处）。
       ★ 默认那档**故意不排第一个** —— 只有这样才测得动"取的是 isDefault 那条"，
         而不是"腿短取 list[0]"（同老基准那条检查的用意）。
       ⚠ 形状照 main.js 的 engine-styles → 引擎 /styles 抄：{ ok, items:[{name,desc,isDefault,L50,L5,L95}] } */
    engineStyles: async () => ({
      ok: true,
      items: [
        { name: '暗调', desc: '整张压下来、暗部厚，适合逆光和傍晚', L50: 40.5, L5: 8.7, L95: 90.9 },
        { name: '高长调', desc: '整体亮、从暗到亮铺得开，通透明快', L50: 69.9, L5: 14.1, L95: 95.5 },
        { name: '中性调', desc: '大师真片的中位水平，最稳的一条', L50: 58.8, L5: 10.8, L95: 93.7, isDefault: true },
      ],
    }),
    /* ★ 按目录存配方（`main.js` 的 `get-grade` / `set-grade`）。
       ⚠⚠ 这两个 mock **必须给**：`enterSession` 会调 `getGrade` 套回配方、
          「存到目录」会调 `setGrade` —— 漏一个就是**同步抛 TypeError**、
          `.catch` 接不到（`setConfig` 那次的老坑）。
          ⚠ 09-23 我改这份 mock 时**把它们连带删掉了**，症状是“存到目录”那条红着、
          而 diag 里 `typeof window.api.setGrade === 'undefined'` —— 是**检查的钉子**没了，
          不是功能坏了。改 mock 时先数一遍"前端会调哪几个"。
       形状照 `main.js` 的 IPC 处理器抄：`get-grade` 返回 **grade 对象本身或 null**
       （外面没有 `{ok}` 包壳），`set-grade` 返回 boolean。 */
    getGrade: async (name) => (window.__grades || {})[name] || null,
    setGrade: async (name, g) => {
      /* 深拷一份 —— 存的是"那一刻"的值，不能跟着 store 后续改动一起变 */
      window.__grades = { ...(window.__grades || {}), [name]: JSON.parse(JSON.stringify(g)) };
      window.__gradeSaves = (window.__gradeSaves || 0) + 1;
      return true;
    },
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
        /* ★★ 09-23：调色台只剩两个选择器 ⇒ 只记这两样。
           断言的是"**界面选了，请求里就真的是它**"——
           只验界面上亮没亮是不够的：亮对了、请求里却没带，画面就是没变
           （"看着对、其实对不上"）。 */
        stock: opts && opts.stock,
        style: opts && opts.style,
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
      return { ok: true, path: 'D:\\lib\\目录A\\DSCF1000_svfilm.jpg',
               w: 2048, h: 1365, bytes: 838860, ms: 29100 };
    },

    /* ⚠ 这个 mock 原来**没有** `pickDirectory` —— 而左栏「加入目录」会调它（「换图库」已删）。
       方法不存在 ⇒ 点那一刻**同步抛 TypeError**，`.catch` 接不到（"漏 setConfig"那次的翻版）。
       ★ 默认返回一个**库外目录**（用来测「加入目录」）；测试可以现改 `window.__pickDir`。 */
    pickDirectory: async () => window.__pickDir || 'D:\\拍摄素材\\厦门_外拍',

    /* ---- 库外·纯 RAW 目录的**预览索引** ----
       ⚠ 又一处「mock 必须实现前端会调的每一个 IPC」：漏了 `extIndex`，
       「加入目录」点下去会连续抛未捕获 TypeError（`addExtraRootDir` 里那次），
       而且「到底拿哪个目录去建索引」永远测不到。
       形状照 `main.js` 的 `ext-index` 抄：`{ ok, n, skip, fail, dir, text, error, note? }`。
       ★ 故意**先推一行进度再等 400 ms 才返回** —— 进度是**主进程推的事件**，
         不是 invoke 的返回值；不等一下的话「进度到了界面」这条没法断言。 */
    extIndex: async (srcDir) => {
      (window.__extIndexed = window.__extIndexed || []).push(srcDir);
      if (window.__extIdxCb) window.__extIdxCb('  3/3  已生成 DSCF0003.JPG  (300 KB)');
      await new Promise((r) => setTimeout(r, 400));
      if (window.__extIndexFail) {
        return { ok: false, n: 0, skip: 0, fail: 0, error: '自检假装建不了（缺 rawpy）' };
      }
      window.__indexBuilt = true;      // 照生产端：建完之后重扫就该不再标「待生成」
      return { ok: true, n: 3, skip: 0, fail: 0, dir: 'C:\\cache\\extpreview\\idx@1' };
    },
    onExtIndexProgress: (cb) => {
      window.__extIdxCb = cb;
      return () => {
        window.__extIdxCb = null;
      };
    },
    /* ★ 批量出片：形状照 `main.js` 的 `export-batch` / `export-batch-progress` 抄。
       ⚠ 少了 `onExportBatchProgress`，App 挂载那条 effect 就**同步抛 TypeError**
       ⇒ 整页白屏、后面每一项都测不到（而且报错只在控制台，界面看着就是"没反应"）。 */
    exportBatch: async (payload) => {
      window.__batchCalls = (window.__batchCalls || []).concat([payload]);
      const items = (payload && payload.items) || [];
      const n = items.length;
      if (window.__batchCb) {
        window.__batchCb({ phase: 'start', i: 1, n, name: 'DSCF1000.RAF' });
      }
      for (let i = 0; i < n; i++) {
        await new Promise((r) => setTimeout(r, 5));
        if (window.__batchCb) {
          window.__batchCb({ phase: 'one', i: i + 1, n, name: 'DSCF1000.RAF', ok: true, done: i + 1, failed: 0 });
        }
      }
      if (window.__batchCb) {
        window.__batchCb({ phase: 'end', i: n, n, done: n, failed: 0, out: 'D:\\lib\\目录A\\调色待验收' });
      }
      return { ok: true, dir: 'D:\\lib\\目录A\\调色待验收', done: n, failed: [] };
    },
    exportBatchCancel: async () => true,
    onExportBatchProgress: (cb) => {
      window.__batchCb = cb;
      return () => {
        window.__batchCb = null;
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

/* ---------- 2. 进目录，量各段位置 ---------- */
console.log('\n[2] 布局（进入目录后）');
// 点第一个目录
const firstBtn = page.locator('button', { hasText: '目录A' }).first();
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

/* ---------- 3. 调色台 ---------- */
console.log('\n[3] 调色台');
const gradeTab = page.locator('button', { hasText: '调色台' }).first();
if (await gradeTab.count()) {
  await gradeTab.click();
  await page.waitForTimeout(600);
  const txt = await page.evaluate(() => document.body.innerText);
  check('切到调色台后出现「胶片风格」', txt.includes('胶片风格'));
  check('出现「曝光风格」', txt.includes('曝光风格'));
  /* ★★ 回归：以前只查"标题在不在"—— 标题当然在，里面的列表是空的。
     必须查**列表里真的有东西**（拿 mock 里那几条的文案当探针）。 */
  check('★ 胶片风格列表真有内容（拿 mock 里那句描述当探针）',
    txt.includes('暖调') || txt.includes('青绿'),
    '', '风格列表是空的 ⇒ 前端读的字段名跟 main.js 对不上');
  check('★ 曝光风格列表真有内容', txt.includes('暗调') || txt.includes('高长调'),
    '', '曝光风格列表是空的');
  /* ★ 调色台**不该再有任何滑杆**（曝光 + 影调全归引擎）。 */
  const nSlider3 = await page.locator('[role="slider"]').count();
  check('★★ 右栏一根滑杆都没有（滑杆全删了）', nSlider3 === 0, `${nSlider3} 根`,
    '又出现滑杆 ⇒ 有人把老控件捡回来了');
  /* ★ 渲染按钮在右栏（手停的地方）。 */
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

  /* ★★ 出图源必须是 RAW（片 = RAW，出图只喂原件）。
     入口那一段（零点/成形/趾部/护栏）只在 `io.load_raw` 里跑，喂缓存里那张 1600 预览图
     会**悄悄掉画质**。 */
  const loads = await page.evaluate(() => window.__engineLoads || []);
  check('★ 调色台喂给引擎的是 RAW',
    loads.length > 0 && loads.every((x) => /\.raf$/i.test(x)),
    loads.map((x) => x.split(/[\\/]/).pop()).join(', ') || '(一次都没 load)',
    '喂的不是 RAW ⇒ 出图源指错了（缓存里是 1600 预览图）');
  check('★ 标题栏标出了这张的出图源（文件名）', /\.RAF/i.test(txt),
    '', '标题栏没显示出图源');

  /* 翻到下一张：
     ① 喂的还是那张的 RAW ② 点一次「渲染」能真出图
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
  check('★ 翻过去那张喂的也是 RAW', /\.raf$/i.test(last),
    last.split(/[\\/]/).pop() || '(没记到)');
  check('★ 翻图后标题栏跟着换成新那张的文件名', /\.RAF/i.test(txt2), '',
    '标题栏没跟着换 ⇒ 出图源没重新算');
  /* ★★ 契约：自动出图只有 ① 进/切进调色台 ② 点「渲染」。
     「换图」不在里面 ⇒ 翻过来之后**渲染次数不许涨**，右栏留白等那一发。
     这条盯的是**机制**（引擎被叫了几次），不是界面文案 —— 文案是间接证据：
     装载链断了也会留白，那时候这条会"绿得莫名其妙"（下面那条才管装载链）。 */
  const renders1 = await renderCount();
  check('★ 换图不自动出图（引擎渲染次数不涨）', renders1 === renders0,
    `${renders0} → ${renders1} 发`,
    `翻图自动出图了（多发 ${renders1 - renders0} 发）—— 违反"只有两个触发点"的契约`);
  check('★ 换图后右栏确实留白等「渲染」', (await phCount()) === 1,
    `占位 ${await phCount()} 个`, '换图后右栏没留白 —— 装载链可能断了');
  /* 再点一次「渲染」：验的是**翻图之后那一发能不能真出图**。 */
  await rBtn.click();
  await page.waitForTimeout(900);
  check('★ 翻图后点「渲染」能出图（占位应为 0 个）',
    (await phCount()) === 0, `占位 ${await phCount()} 个`,
    '翻过一张就出不了图 ⇒ 出图源/装载那条链断了');
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
console.log('\n[6] 自动出图：只有两个触发点（换风格 / 换图都不出）');
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
  await noRender('换胶片风格', async () => {
    const b = page.locator('[data-stock]').nth(1);
    if (await b.count()) await b.click();
  });
  await noRender('换曝光风格', async () => {
    const b = page.locator('[data-style]').first();
    if (await b.count()) await b.click();
  });
  /* ★★ 「导出成片」（09-15 SV 选「A」第 ② 项）：按钮 → 请求里带的东西对不对。
     钉三件：① 真的发出去了；② 带的是**出图源**（loadPath，绝对路径），不是身份键 rel；
             ③ 带的是**两个选择器**（胶片风格 + 曝光风格），不自己写死尺寸。 */
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
    check('★ 导出带的是**两个选择器**（不自己拼参数串、也不自己写死尺寸）',
      !!e.stock && !!e.style && !('side' in e) && !('params' in e),
      JSON.stringify(e),
      '少一个 ⇒ 导出的和屏幕上那张不是同一张；写死尺寸 = 引擎改了工作分辨率前端不跟');
  }
}

/* 共用小工具：这两个按钮在左栏/顶栏上，[7.5] 那一组来回切目录要用 */
const goGrade = async () => {
  const t = page.locator('button', { hasText: '调色台' }).first();
  if (await t.count()) { await t.click(); await page.waitForTimeout(900); }
};
const goTheme = async (n) => {
  const b = page.locator('button', { hasText: n }).first();
  if (await b.count()) { await b.click(); await page.waitForTimeout(900); }
};
const nTh = () => page.locator('[data-idx]').count();

/* --- A. 「底栏没了」的真相：空着**不出声**，和"坏了"长得一模一样 --- */
/* 现场：调色台 + 一个一张星都没打过的目录 ⇒ `visiblePhotos(...,forGrade=true)` 返回空
   ⇒ 整条底栏一片空白、一格缩略图都没有，SV 报的就是「底部栏没了」。
   要钉两条：① 有星时**不许**出空态（别把判定写反）；② 空的时候**必须说出空因**。 */
{
  await goGrade();
  const nA = await nTh();
  check('★ 目录A 有 1 张 3★ ⇒ 底栏照常列缩略图、不出空态',
    nA >= 1 && (await page.locator('[data-dock-empty]').count()) === 0,
    `${nA} 张 / 空态 ${await page.locator('[data-dock-empty]').count()} 个`,
    '有星却报空 ⇒ 判定写反了');
  await goTheme('目录B');            // 目录B 一张星都没打过
  await goGrade();
  const emp = page.locator('[data-dock-empty]');
  check('★★ 一张星都没有 ⇒ 底栏出现**空态**（不是一片空白）', (await emp.count()) === 1,
    `${await emp.count()} 个`,
    '空着不出声 ⇒ 和"底栏坏了"分不清，正是 SV 09-15 报的那个');
  const eTxt = (await emp.count()) ? await emp.first().innerText().catch(() => '') : '';
  check('★ 空态说的是**空的原因**（不是空白、也不是泛泛一句）', /打星/.test(eTxt),
    eTxt.replace(/\n/g, ' ').slice(0, 70) || '(空态没字)',
    '空态没有文字 ⇒ 用户无从判断"为什么没有"');
  check('★ 空态标明了是**哪一种**空（调色台"只列已打星" ≠ 选片台"筛选后没有"）',
    (await emp.count()) > 0 && (await emp.first().getAttribute('data-dock-empty')) === 'grade-no-star',
    String((await emp.count()) ? await emp.first().getAttribute('data-dock-empty') : '(没出现)'));
  check('★ 空的时候确实**一张缩略图都没画**（否则上面几条是空转）',
    (await nTh()) === 0, `${await nTh()} 张`);
  const eBox = (await emp.count()) ? await emp.first().boundingBox() : null;
  check('★ 空态那块地方**还在**（不是塌成 0 高、也不是被挤出视口）',
    !!eBox && eBox.height > 40 && eBox.y + eBox.height <= (await page.evaluate(() => innerHeight)) + 1,
    eBox ? JSON.stringify({ h: Math.round(eBox.height), y: Math.round(eBox.y) }) : '(量不到)',
    '高 0 或被挤出视口 ⇒ 那是真的"没了"');
  check('★ 空态不挡点击（`pointer-events: none`，底下那条缩略图列表还要能滚）',
    (await page.evaluate(() =>
      getComputedStyle(document.querySelector('[data-dock-empty]')).pointerEvents)) === 'none');
  await goTheme('目录A');
  await goGrade();
  check('★ 切回有星的目录 ⇒ 空态消失、缩略图回来',
    (await page.locator('[data-dock-empty]').count()) === 0 && (await nTh()) >= 1,
    `${await nTh()} 张`);
}

console.log('\n[8] 选片台行为');
const dockThumbs = () => page.locator('[data-idx]').count();
/* 底栏上方那行显示"当前是哪张"（`Stars.tsx` 的 ExifBar，居中的纯文本 div）。
   ⚠⚠ 踩过：`Dock.tsx` 的缩略图格子底下也有一行文件名（`Thumb` 没打星时显示 `p.name`），
   形状一模一样（纯文本 div + 居中）⇒ 原来这个 helper 取"最后一个匹配"，
   取到的**永远是底栏最后一张缩略图的文件名**，于是
   「点缩略图换图了没」「切目录从第一张开始吗」这些断言全在看同一张缩略图 ⇒ 恒假/恒真。
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
  check('★ 打星真的落盘了，键名是「目录名||文件名」',
    !!saved && saved['目录A||DSCF1000'] === 3, JSON.stringify(saved),
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

/* ---------- 9. 状态记忆（切目录 / 切台 再回来） ---------- */
console.log('\n[9] 状态记忆');
{
  /* ⚠★ 切目录**必须走左栏「图库目录」那条路**（`SessionPane` 直接 `enterSession`），
     不能走「目录列表」—— 目录列表会先 `goHome()`，而 `goHome` 里就把 `cur` 归 0 了
     ⇒ 再进目录时"下标归零"是 goHome 干的，**验证不到 `enterSession` 自己有没有归零**。
     （09-15 破法验证时发现的：破法⑩把 `enterSession` 的 `cur:0` 删掉，这一组居然全绿 ——
       就是被 goHome 兜住了。改成走左栏之后破法立刻命中。） */
  const themeList = page.locator('button', { hasText: '目录列表' }).first();
  const openTheme = async (n) => {
    const b = page.locator('button', { hasText: n }).first();
    if (await b.count()) {
      await b.click();
      await page.waitForTimeout(900);
    }
  };
  await openTheme('目录B');
  const nb = await curName();
  check('★ 切到目录B ⇒ 看的是 B 的照片、而且从第一张开始', /DSCF2000/.test(nb), nb || '(没读到)');
  /* 先在 B 里翻两下（把下标推到 2），再切回 A —— 这样才测得动「切目录会不会把下标带过去」 */
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(500);
  const nb2 = await curName();
  check('在目录B里翻两张（否则下一条是空转）', /DSCF2002/.test(nb2), nb2 || '(没读到)');
  await openTheme('目录A');
  const na = await curName();
  check('★ 切回目录A ⇒ 从第一张开始（下标没被带到另一个目录）', /DSCF1000/.test(na),
    na || '(没读到)', `切目录时下标没归零 ⇒ 切到短目录会出现"打开就白屏"，实际 ${na || '空'}`);
  /* ★ 状态记忆要**真落盘**（`useStore.saveLast` → `api.setConfig`），不是只存在内存里。
     只测"切来切去还在"是不够的 —— 那只证明内存里没丢，关掉程序就没了。 */
  const cfgSaved = await page.evaluate(() => window.__setConfig || {});
  check('★ 「上次看到哪」真写进配置了（下次启动才能回到原地）',
    cfgSaved.lastSession === '目录A' && typeof cfgSaved.lastCur === 'number',
    JSON.stringify(cfgSaved),
    '没写进配置 ⇒ 下次启动回不到原来那张');
  /* 「目录列表」（回首页）这条路也得还在 —— 顺便量一下首页真列出了两个目录 */
  if (await themeList.count()) {
    await themeList.click();
    await page.waitForTimeout(700);
    const th = await page.evaluate(() => document.body.innerText);
    check('★ 从调色流程回「目录列表」还列得出两个目录（没把库弄丢）',
      /目录A/.test(th) && /目录B/.test(th), '', '首页列不出目录了');
    await openTheme('目录A');
  }
  const txt = await page.evaluate(() => document.body.innerText);
  check('★ 切一圈回来，星级还在（3★ 计数没变）', /3★\s*1/.test(txt), '',
    '切目录把星级弄丢了');
  /* 切台再切回来：选的两个风格要还在（不是偷偷回默认）
     ★ 09-23：滑杆删了 ⇒ 这条改成盯**两个选择器**的选中状态。
       同一条契约（状态记忆 / 单一状态源），只是量什么变了。 */
  const gradeTab2 = page.locator('button', { hasText: '调色台' }).first();
  if (await gradeTab2.count()) {
    await gradeTab2.click();
    await page.waitForTimeout(900);
  }
  const styleNow = () => page.locator('[data-style][data-style-on="1"]')
    .first().getAttribute('data-style').catch(() => null);
  const vA = await styleNow();
  await pickTab.click();
  await page.waitForTimeout(500);
  await gradeTab2.click();
  await page.waitForTimeout(900);
  const vB = await styleNow();
  check('★ 切到选片台再回调色台，选的曝光风格还在', !!vA && vA === vB, `${vA} → ${vB}`,
    '切一圈回来曝光风格偷偷回默认了');
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
     ⚠ 重开之后**先进一个目录**（`loadSessions` 没配 lastSession ⇒ 停在目录列表，
       这时候根本没有调色台分屏，量"引擎未启动"会量到空气）。 */
  await page.addInitScript(() => {
    window.__FAIL_HEALTH = true;
  });
  await page.reload();
  await page.waitForTimeout(1500);
  const themeA = page.locator('button', { hasText: '目录A' }).first();
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
   在长目录里翻到第 30 张 → 切到一个只有 12 张的目录（`enterSession` 把 cur 归 0，
   但落盘的 lastCur 还是 30）→ 关掉再打开 ⇒ 恢复成"第 30 张"= 不存在
   ⇒ 中间显示「没有照片」，在用户眼里就是**打开工作台白屏**，还说不出为什么。
   所以这里专门塞一个**超范围的下标**进去，量它会不会被夹回来。 */
console.log('\n[11] 恢复上次状态');
{
  await page.addInitScript(() => {
    const orig = window.api.getConfig;
    window.api.getConfig = async () => ({
      ...(await orig()),
      lastSession: '目录A',
      lastCur: 999,        // ← 故意超范围（目录A 只有 40 张）
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

/* ---------- 13. 右栏：曝光风格的默认 / 换风格 / 恢复默认 / 存到目录 ---------- */
/* ★ 09-23 起右栏只有两个选择器（胶片风格网格 + 曝光风格 chips），滑杆/相纸/基准全删。
   这一组对着三件事：
   ① **默认档必须由引擎给**：前端初值故意留空，`/styles` 里带 `isDefault` 的那条才是默认。
      过去在这儿栽过同款（前端写死 `'all'`，引擎表里没有 ⇒ 静默回落、四支一支都不亮）。
   ② 「恢复默认」「存到目录」两个按钮**必须真接上线**（曾经没有 onClick，点了什么都不发生）。
   ③ 「存到目录」要是**只写不读**就是存了个寂寞 ⇒ 连"切走再回来，配方真套回来了"一起钉。 */
console.log('\n[13] 右栏：曝光风格的默认 / 两个按钮 / 按目录存配方');
{
  /* ⚠ [10] 为了测"引擎没起"注入过 `__FAIL_HEALTH = true`（`addInitScript` 会**一直生效**）
     ⇒ 这里必须显式关掉**并整页重来**，否则右栏只剩一句「引擎未启动」，
       这一组每条都测到空气（本组最容易写出"假绿"的地方）。
     ★ 重开之后会按 `lastSession` 直接落进目录A（[11] 注入的那个配置）——
       所以 `sessionName` 是有值的，「存到目录」才有地方存。 */
  await page.addInitScript(() => { window.__FAIL_HEALTH = false; });
  await page.reload();
  await page.waitForTimeout(1800);
  /* ★ 走**正规路径**进目录：`sessionName` 只有 `enterSession` 会写，
     而「存到目录」在 `!sessionName` 时是**直接 return** 的（连 toast 都不弹）
     ⇒ 不显式进一次目录，那条检查会红得莫名其妙。 */
  const home13 = page.locator('button', { hasText: /目录列表/ }).first();
  if (await home13.count()) { await home13.click(); await page.waitForTimeout(700); }
  await goTheme('目录A');
  await page.waitForTimeout(400);
  await goGrade();
  await page.waitForTimeout(400);
  const chips = page.locator('[data-style]');
  const nChip = await chips.count();
  check('★ 右栏有曝光风格 chips（否则下面几条全是空转）', nChip >= 3, `${nChip} 档`);
  /* ★★ 默认档**由引擎给**（mock 里 isDefault 那档故意排在最后）—— 这条同时证明
     "读的是 isDefault"而不是"腿短取第一个"。 */
  const onIdx = [];
  for (let i = 0; i < nChip; i++) {
    if ((await chips.nth(i).getAttribute('data-style-on')) === '1') onIdx.push(i);
  }
  check('★★ 恰好一档是选中的（不是 0 档、也不是多档同时亮）',
    onIdx.length === 1, `选中的下标 ${JSON.stringify(onIdx)} / 共 ${nChip} 档`,
    '0 档 ⇒ 界面上一个都不亮（画面已经在用了、还看不出来）；多档 ⇒ 状态写错');
  check('★★ 选中的是**引擎标了 isDefault 的那一档**（不是列表第一档）',
    onIdx.length === 1 && onIdx[0] === nChip - 1,
    `选中第 ${onIdx[0] + 1} / ${nChip} 档`,
    '腿短取 list[0] ⇒ 引擎换默认档后静默错位（mock 里默认那档故意排在最后）');

  /* ★★ 换一档 ⇒ 请求里带的就是它（少这一条，界面选了、画面不动） */
  const otherIdx = onIdx.length === 1 ? (onIdx[0] + 1) % nChip : 0;
  const wantName = await chips.nth(otherIdx).getAttribute('data-style');
  await chips.nth(otherIdx).click();
  await page.waitForTimeout(250);
  const rBtn13 = page.locator('button', { hasText: /^渲染$/ }).first();
  if (await rBtn13.count()) { await rBtn13.click(); await page.waitForTimeout(1200); }
  const a13 = await page.evaluate(() => (window.__renderArgs || []).slice());
  const last13 = a13[a13.length - 1] || {};
  check('★★ 渲染请求里带的曝光风格**就是刚选的那一档**',
    last13.style === wantName, `请求 ${JSON.stringify(last13.style)} / 界面选的 ${wantName}`,
    '界面选了、请求里还是旧的 ⇒ 画面不动，看着像"这一档没效果"');
  check('★ 渲染请求里同时带着胶片风格（两个选择器都要进请求）',
    !!last13.stock, JSON.stringify({ stock: last13.stock, style: last13.style }));

  /* ★ 「恢复默认」：把曝光风格回引擎默认档，**胶片风格不动** */
  const curStock13 = last13.stock;
  const resetBtn13 = page.locator('button', { hasText: '恢复默认' }).first();
  check('右下角「恢复默认」在（否则下面这条是空转）', (await resetBtn13.count()) > 0);
  if (await resetBtn13.count()) {
    await resetBtn13.click();
    await page.waitForTimeout(400);
    const onIdx2 = [];
    for (let i = 0; i < nChip; i++) {
      if ((await chips.nth(i).getAttribute('data-style-on')) === '1') onIdx2.push(i);
    }
    check('★ 点「恢复默认」⇒ 曝光风格回引擎默认那档（回到选中最后那一档）',
      onIdx2.length === 1 && onIdx2[0] === nChip - 1, `选中的下标 ${JSON.stringify(onIdx2)}`,
      '回默认没生效 ⇒ 用户以为回默认了，其实还在自己选的那一档');
    const stockOn = await page.locator('[data-stock][data-stock-on="1"]').first()
      .getAttribute('data-stock').catch(() => null);
    check('★★ 「恢复默认」**不动胶片风格**（只把曝光风格回默认）',
      stockOn === curStock13, `胶片风格 ${stockOn} / 之前 ${curStock13}`,
      '顺手把胶片风格也抹了 ⇒ 用户莫名其妙换了个卷（那是"拍什么"，不是调出来的）');
  }

  /* ★★ 「存到目录」：存了要**读回来** —— 切走再切回来，两档都得套回 */
  const saveBtn = page.locator('button', { hasText: '存到目录' }).first();
  check('右下角「存到目录」在', (await saveBtn.count()) > 0);
  if (await saveBtn.count()) {
    /* ⚠ 上一段点过「恢复默认」⇒ 它弹的那条 toast 要 2 秒才消，而 toast 会**盖住**右下角
       那两个按钮 ⇒ 直接点会打在 toast 上（点了没反应、还找不到原因）。等它消掉再点。 */
    await page.waitForTimeout(2200);
    await chips.nth(otherIdx).click({ force: true });   // 先挑一个**非默认**档，才测得动
    await page.waitForTimeout(300);
    const beforeSave = await page.evaluate(() => window.__gradeSaves || 0);
    await saveBtn.click({ force: true });
    await page.waitForTimeout(700);
    const saved = await page.evaluate(() => window.__grades || {});
    const savedOne = Object.values(saved)[0] || {};
    const toast13 = await page.evaluate(() => {
      const t = document.body.innerText || '';
      const i = t.indexOf('配方已存到');
      return i >= 0 ? t.slice(i, i + 40).replace(/\n/g, ' ')
        : (/还没进目录/.test(t) ? '（还没进目录，没地方存）' : '(没有提示)');
    });
    const afterSave = await page.evaluate(() => window.__gradeSaves || 0);
    const diag = await page.evaluate(() => {
      const btns = [...document.querySelectorAll('button')];
      const b = btns.filter((x) => (x.textContent || '').trim() === '存到目录');
      return {
        apiSetGrade: typeof (window.api || {}).setGrade,
        n: b.length,
        vis: b.map((x) => x.offsetParent !== null),
      };
    });
    check('★ 点「存到目录」⇒ 真落盘（走 setGrade 通道）',
      afterSave > beforeSave,
      `提示=${toast13}`,
      `按钮没接上线 / 没进目录（sessionName 空）⇒ 存了个寂寞。`
      + `saves ${beforeSave}→${afterSave}；提示=${toast13}；__grades=${JSON.stringify(saved)}；diag=${JSON.stringify(diag)}`);
    check('★ 存的是**两个选择器**（不是一堆滑杆值）',
      !!savedOne.stock && !!savedOne.style && !savedOne.params,
      JSON.stringify(savedOne),
      '还在存 params ⇒ 老滑杆那套又回来了');
    /* 切到另一个目录再切回来：进目录时 `enterSession` 会套回配方 */
    const goTheme13 = async (n) => {
      const b = page.locator('button', { hasText: n }).first();
      if (await b.count()) { await b.click(); await page.waitForTimeout(900); }
    };
    const homeBtn = page.locator('button', { hasText: /目录列表/ }).first();
    if (await homeBtn.count()) { await homeBtn.click(); await page.waitForTimeout(600); }
    await goTheme13('目录B');
    if (await homeBtn.count()) { await homeBtn.click(); await page.waitForTimeout(600); }
    await goTheme13('目录A');
    const t2 = page.locator('button', { hasText: '调色台' }).first();
    if (await t2.count()) { await t2.click(); await page.waitForTimeout(900); }
    const onIdx3 = [];
    for (let i = 0; i < nChip; i++) {
      if ((await chips.nth(i).getAttribute('data-style-on')) === '1') onIdx3.push(i);
    }
    check('★★ 切走再回来 ⇒ 存的那一档**真套回来了**（只写不读 = 存了个寂寞）',
      onIdx3.length === 1 && onIdx3[0] === otherIdx,
      `回来选中的下标 ${JSON.stringify(onIdx3)} / 存的是 ${otherIdx}`,
      '存了不读 ⇒ 每次进目录都回默认，用户以为"没存上"');
  }
}

console.log('\n[14] 视图三档（A / A|B / B）');
{
  /* 两栏靠 `img` 的 alt 认（Pane 的 title 就是 alt，`layout_check` 一直这么认） */
  const paneImgs = () =>
    page.evaluate(() =>
      [...document.querySelectorAll('img')]
        .map((i) => i.getAttribute('alt'))
        .filter((a) => a === '原图' || a === '调色后')
    );
  const viewMode = () =>
    page.evaluate(() => {
      const el = document.querySelector('[data-view-mode]');
      return el ? el.getAttribute('data-view-mode') : null;
    });
  const vBtn = (id) => page.locator(`[data-view-btn="${id}"]`).first();

  /* 先确保停在调色台（上面几段可能翻去选片台过） */
  const gTab = page.locator('button', { hasText: '调色台' }).first();
  if (await gTab.count()) {
    await gTab.click();
    await page.waitForTimeout(800);
  }

  const nBtn = await page.locator('[data-view-btn]').count();
  check('★ 三档按钮都在（A / A|B / B，照 Lightroom）', nBtn === 3, `${nBtn} 个`,
    '按钮不全 ⇒ 要么没画，要么档位串了');
  check('★★★ 默认就是 A|B（一进来是左右对比，不是单张）—— `data-view-mode="ab"`',
    (await viewMode()) === 'ab'
      && (await page.locator('[data-view-btn="ab"][data-view-active="1"]').count()) === 1,
    `mode=${await viewMode()}`,
    '默认档不是 ab ⇒ 一进调色台看到的是单张（他要的是"默认 A|B 即对比原片"）');
  check('★ 默认两栏都在（原图 + 调色后，顺序也对）',
    JSON.stringify(await paneImgs()) === '["原图","调色后"]',
    JSON.stringify(await paneImgs()));

  const r0 = await renders();
  await vBtn('b').click();
  await page.waitForTimeout(400);
  const bImgs = await paneImgs();
  check('★★ 点 B ⇒ **只剩「调色后」一张**（单张铺满）',
    bImgs.length === 1 && bImgs[0] === '调色后', JSON.stringify(bImgs),
    '还是两张 / 剩下的是「原图」⇒ 档位接反了');
  const bHasPix = await page
    .locator('img[alt="调色后"]')
    .first()
    .evaluate((el) => el.naturalWidth > 0)
    .catch(() => false);
  check('★ 单张那张**当场就有内容**（用的是手上已出好的图，不是在等渲染）', bHasPix, '',
    '切档把图清空了 ⇒ 每次切档都要重出一张（"切个视图等 6 秒"）');
  check('★★ 切档**不出图**（换看法 ≠ 重新渲染一发）',
    (await renders()) === r0, `渲染 ${r0} → ${await renders()} 发`,
    '切档就重出一张 ⇒ 违反"只有两个触发点"（渲染只该由「渲染」按钮 / 切进调色台触发）');

  await vBtn('a').click();
  await page.waitForTimeout(400);
  const aImgs = await paneImgs();
  check('★★ 点 A ⇒ **只剩「原图」一张**', aImgs.length === 1 && aImgs[0] === '原图',
    JSON.stringify(aImgs), '还是两张 ⇒ 只画了按钮、档位没接上');
  check('★ 点 A 也不出图', (await renders()) === r0, `渲染 ${r0} → ${await renders()} 发`);

  await vBtn('ab').click();
  await page.waitForTimeout(400);
  check('★ 点回 A|B ⇒ 两栏回来（档位可来回切）',
    JSON.stringify(await paneImgs()) === '["原图","调色后"]', JSON.stringify(await paneImgs()));

  /* ★ 一张图都没有过 ⇒ A/B 两档不能把图**弄丢**（`after` 是 state，切档不该清它）。
     这条顺便盯住"原图那一栏在 A 档也不是空的"。 */
  const aHasPix = await page
    .locator('img[alt="原图"]')
    .first()
    .evaluate((el) => el.naturalWidth > 0)
    .catch(() => false);
  check('★ 回到 A|B 后，两栏的图都还是有像素的（切档没把已出的图弄丢）', aHasPix, '',
    '切来切去之后图变空了 ⇒ 档位把 state 清掉了');
}

/* ---------- 15. 相纸（09-15 SV 选「C」） ---------- */
/* ★ 为什么要这一组：一张真卷出图 = **(负片, 相纸)** 二元组。相纸是**最终成色的另一半** ——
   同一卷负片印在不同纸上，是两套不同的颜色（人像最经典的就是「柯达卷 + Portra Endura」
   和「富士卷 + Crystal Archive」两套脸色）。原来只开放了负片那一半。
   ★★ 这一组**不测"有没有下拉框"**，测的是三件"看着对、其实对不上"的事：
     ① 换卷之后相纸**有没有自动跟到新卷的配套纸**（不是留在上一卷那张）
     ② 换纸之后**发出去的请求里带的是不是新纸**（下拉亮对了、请求没带 = 画面不变）
     ③ 中性卷下这一栏**是不是真不显示**（引擎对中性卷返回空表）
   ⚠ mock 里 `Portra400薄荷` 的配套纸**恰好是列表第一张**、`C200青蓝` 的配套却是**第三张**
     ⇒ 只有这样才区分得出「读了引擎的 isDefault」和「腿短取了 option[0]」。 */
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
    '入口不摆出来，用户永远找不到（"按钮在、功能不在"那次的翻版）');

  const extCount = await page.locator('[data-session-ext]').count();
  check('★ 加过的文件夹出现在「图库目录」里',
    extCount >= 1, `${extCount} 条`, '加进来的目录不出现 ⇒ 用户以为白加了');
  if (extCount === 0) {
    check('（后面几条依赖列表里有加过的文件夹）', false, '',
      '列表里没有加过的文件夹，后面全是空转');
  } else {
    /* ★ 09-15 SV 选「B」：每条都是"加过的文件夹"，「库外」徽标已经没有区分对象了
       ⇒ 这条换成盯**新**形态：条目上得看得出"这是哪个文件夹"（不然重名时点错都不知道）。 */
    const firstTitle =
      (await page.locator('[data-session-ext]').first().getAttribute('title')) || '';
    check('★ 条目上带完整路径（两个同名文件夹在栏里也能分清是哪个）',
      /D:\\/.test(firstTitle), firstTitle.slice(0, 60),
      '不写路径 ⇒ 重名的文件夹在栏里长得一模一样，点错了都看不出来');

    /* ---- ① 点它 ⇒ 列图用的必须是**它自己的路径** ---- */
    await page.evaluate(() => { window.__listPaths = []; });
    await page.locator('[data-session-ext]').first().click();
    await page.waitForTimeout(1200);
    const paths = await page.evaluate(() => window.__listPaths || []);
    const used = paths[paths.length - 1];
    check('★★★ 点库外条目 ⇒ 读的是**它自己的路径**（不是 `D:\\lib\\名字`）',
      used === 'D:\\拍摄素材\\厦门_外拍', `读到 ${JSON.stringify(used)}`,
      `读到 ${JSON.stringify(used)} ⇒ 拼成"库根\\名字"了：库外目录进去永远 0 张照片，` +
        '看着像空目录，看不出是路径拼错');

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

/* ---------- [17] 库外·纯 RAW 目录 ⇒ 预览小图（源目录只读） ---------- */
/* ★ 这一组盯的是「工作台只按 JPG 列图」这个真限制的**补救**：
   只拷了 RAF 的目录加进来后，在列表上必须看得见张数、点进去要能看到片子。
   三个最容易坏的地方（都很像"没反应"）：
     ① 拿**缓存目录**去建索引（应该拿源目录） ⇒ 建出来永远是空的；
     ② 建不了却不报原因 ⇒ 那个目录在左栏里就是**空的**，「空」和「坏了」长得一样；
     ③ 进去列图时读的是源目录（那儿一个 JPG 都没有）⇒ 界面空的，但不是"读缓存"。 */
console.log('\n[17] 加入目录：只有 RAW 的目录 ⇒ 预览小图（看图走缓存、出图走原件）');
{
  await page.addInitScript(() => {
    window.__extraRoots = ['D:\\示例库\\纯RAW'];
    window.__rawDir = 'D:\\示例库\\纯RAW';
    window.__indexBuilt = false;
  });
  await page.reload();
  await page.waitForTimeout(1800);

  const item = page.locator('[data-session-ext]').first();
  const before = await item.innerText();
  check('★ 纯 RAW 目录在列表里显示的是 **RAW 张数**（不是 0 张）',
    /3 张/.test(before), before.replace(/\n/g, ' '),
    `显示的是 ${before.replace(/\n/g, ' ')} ⇒ 一个 JPG 都没有的目录会被数成 0 张，` +
      '用户一看 0 张就以为白加了');
  check('★ 索引还没生成时，条目上写明「预览待生成」',
    /预览待生成/.test(before), before.replace(/\n/g, ' '),
    '不写 ⇒ 用户点进去看到空的，只能猜是目录空了还是台子坏了');

  /* ---- ① 点它 ⇒ 先拿**源目录**去建索引 ---- */
  await page.evaluate(() => { window.__extIndexed = []; });
  await item.click();
  /* ★ 不等 click 跑完就采样：进度是**边跑边推**的，等完了才看就看不见了 */
  await page.waitForTimeout(250);
  const mid = await page.locator('body').innerText();
  check('★ 建索引的进度**在跑的过程中**就到了界面（不是等返回值）',
    /已生成 DSCF0003\.JPG/.test(mid), '',
    '等 invoke 返回才显示 ⇒ 一次几百张要几十秒，界面全程像死机');

  await page.waitForTimeout(1500);
  const asked = await page.evaluate(() => window.__extIndexed || []);
  check('★★★ 点纯 RAW 的条目 ⇒ 拿的是**源目录**去建索引（不是缓存目录）',
    asked.length === 1 && asked[0] === 'D:\\示例库\\纯RAW', JSON.stringify(asked),
    `传的是 ${JSON.stringify(asked)} ⇒ 拿缓存目录去扫，永远扫不出 RAW，` +
      '建出来是空的，而界面上一点错都看不到');

  const after = await page.locator('[data-session-ext]').first().innerText();
  check('★ 索引建完重扫 ⇒ 「预览待生成」没了（列表真的重扫了）',
    !/预览待生成/.test(after), after.replace(/\n/g, ' '),
    `还是 ${after.replace(/\n/g, ' ')} ⇒ 重扫没发生，用户以为没建成功，又点一遍`);

  const listed = await page.evaluate(() => window.__listPaths || []);
  check('★ 进这个目录去列图时读的是**源目录**（09-15 起 path 永远是源目录）',
    listed[listed.length - 1] === 'D:\\示例库\\纯RAW',
    JSON.stringify(listed[listed.length - 1]),
    `读的是 ${JSON.stringify(listed[listed.length - 1])} ⇒ 还指着预览缓存的话，` +
      '桶/星级也都跟着落在缓存上，缓存一删他的星级就"消失"了');
}

/* ---- ② 建不了的时候必须说出原因（不静默） ---- */
{
  await page.addInitScript(() => {
    window.__extraRoots = ['D:\\示例库\\纯RAW'];
    window.__rawDir = 'D:\\示例库\\纯RAW';
    window.__indexBuilt = false;
    window.__extIndexFail = true;
  });
  await page.reload();
  await page.waitForTimeout(1800);
  await page.locator('[data-session-ext]').first().click();
  await page.waitForTimeout(1200);
  const t = await page.locator('body').innerText();
  check('★★ 索引建不了 ⇒ 界面上要**说出原因**（否则那目录永远是空的，而"空"和"坏了"一样）',
    /预览小图没生成/.test(t), '',
    '不出声的话，用户看到的就是一个空目录，无法区分"目录里本来就没片"和"建索引坏了"');
}

/* ---------- 收尾：整轮跑下来有没有未捕获报错 ---------- */
/* ★ 为什么要放在**最后**再查一次：中间那些组会翻图/切目录/切台，
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
  check('★ 整轮跑下来没有未捕获报错（翻图/切目录那条链也没炸）', real.length === 0,
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
