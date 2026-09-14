/* eslint-disable no-console */
/**
 * svStudio UI 自检（无需 GUI，助理自己跑）
 *
 * 用法：node _check/ui_smoke.mjs            （只跑静态检查）
 *      node _check/ui_smoke.mjs --browser   （额外跑真实浏览器布局检查，需 playwright）
 *
 * ★ 为什么要这个：本机起不了 Electron GUI（GPU process 起不来），
 *   之前每次改动都要 SV 双击 .bat 目测 —— 慢且容易漏。
 *   这里把"能静态验证的"全部自动化，包括**曾经踩过的坑的回归检测**。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');

let pass = 0;
let fail = 0;
const failures = [];

function ok(name, detail = '') {
  pass++;
  console.log(`  ok   ${name}${detail ? '   ' + detail : ''}`);
}
function bad(name, detail = '') {
  fail++;
  failures.push(`${name} — ${detail}`);
  console.log(`  FAIL ${name}${detail ? '   ' + detail : ''}`);
}
function check(name, cond, detailOK = '', detailBad = '') {
  if (cond) ok(name, detailOK);
  else bad(name, detailBad || detailOK);
}
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
const exists = (p) => fs.existsSync(path.join(ROOT, p));

console.log('\n=== svStudio UI 自检 ===\n');

/* ---------- 1. 产物 ---------- */
console.log('[1] 构建产物');
const DIST = 'renderer/dist';
check('dist/index.js 存在', exists(`${DIST}/index.js`));
check('dist/index.css 存在', exists(`${DIST}/index.css`));
if (exists(`${DIST}/index.js`)) {
  const js = read(`${DIST}/index.js`);
  const size = fs.statSync(path.join(ROOT, `${DIST}/index.js`)).size;
  check('产物不是空壳（>50KB）', size > 50_000, `${(size / 1024).toFixed(0)} KB`);

  // ★ 回归：09-15 黑屏就是这个 —— lib 模式不替换 process.env.NODE_ENV
  const nProc = (js.match(/process\.env/g) || []).length;
  check('★ 无 process.env 残留（黑屏元凶）', nProc === 0, '', `还有 ${nProc} 处`);

  // ★ 回归：file:// 下 ESM 会被 CORS 拦死
  const isEsm = /(^|\n)\s*(import|export)\s/.test(js.slice(0, 4000));
  check('★ 产物是 IIFE 不是 ESM（file:// 要求）', !isEsm);
  check('无 require( 残留', !/\brequire\(/.test(js));
}

/* ---------- 2. HTML 入口 ---------- */
console.log('\n[2] HTML 入口');
const htmlRaw = read('renderer/index.html');
// ⚠ 必须先剥掉注释再检查：注释里写了 "type=\"module\"" 字样会被误判（09-15 踩过）
const html = htmlRaw.replace(/<!--[\s\S]*?-->/g, '');
check('引用 ./dist/index.js', html.includes('./dist/index.js'));
check('引用 ./dist/index.css', html.includes('./dist/index.css'));
check(
  '★ 不是 type="module"（file:// 会 CORS）',
  !/type\s*=\s*["']module["']/.test(html)
);
check('有 #root 挂载点', html.includes('id="root"'));

/* ---------- 3. 布局契约（曾经的坑） ---------- */
console.log('\n[3] 布局契约回归');
const css = exists(`${DIST}/index.css`) ? read(`${DIST}/index.css`) : '';
// ★ 回归：Theme 包的那层 div 没高度 ⇒ 整树 height:100% 全失效 ⇒ 底栏被挤出视口
check(
  '★ .radix-themes 有 height:100%（底栏消失元凶）',
  /\.radix-themes\s*\{[^}]*height:\s*100%/.test(css),
  '',
  '缺失 —— 底栏/星级/EXIF 会被挤出视口'
);
const mainCol = read('src/components/App.tsx');
check(
  '★ 主区列有 minHeight:0（防大图撑爆）',
  /minHeight:\s*0/.test(mainCol),
  '',
  '缺失 —— 大图会把下面几条挤出视口'
);

/* ---------- 4. preload ↔ API 契约一致性 ---------- */
console.log('\n[4] preload ↔ API 契约');
const preload = read('preload.js');
const apiTs = read('src/api/index.ts');
// preload 里 exposeInMainWorld 的方法名
const preloadMethods = new Set();
for (const m of preload.matchAll(/^\s*(\w+):\s*\(/gm)) preloadMethods.add(m[1]);
// api/index.ts 里 API 对象的方法名
const apiMethods = new Set();
const apiBody = apiTs.slice(apiTs.indexOf('export const API'));
for (const m of apiBody.matchAll(/^\s{2}(\w+):/gm)) apiMethods.add(m[1]);

check('preload 通道数 > 25', preloadMethods.size > 25, `${preloadMethods.size} 个`);
const onlyPreload = [...preloadMethods].filter((x) => !apiMethods.has(x));
const onlyApi = [...apiMethods].filter((x) => !preloadMethods.has(x));
check(
  '★ preload 的每个通道 API 层都封了',
  onlyPreload.length === 0,
  '',
  `漏封装：${onlyPreload.join(', ')}`
);
check(
  '★ API 层没封不存在的通道',
  onlyApi.length === 0,
  '',
  `preload 里没有：${onlyApi.join(', ')}`
);

/* ---------- 5. 组件接线 ---------- */
console.log('\n[5] 组件接线');
const appTsx = read('src/components/App.tsx');
for (const c of ['TopBar', 'HomeView', 'Dock', 'GradePanel', 'Viewer']) {
  check(`App 里接了 ${c}`, new RegExp(`<${c}[\\s/>]`).test(appTsx));
}
check('Viewer 里有分屏（SplitView）', /SplitView/.test(read('src/components/Viewer.tsx')));
check('底栏有 data-idx（悬浮预览要用）', /data-idx/.test(read('src/components/Dock.tsx')));

/* ---------- 6. 调试日志可用 ---------- */
console.log('\n[6] 调试日志');
check('main.js 有 log-line 通道', /ipcMain\.handle\('log-line'/.test(read('main.js')));
check('preload 暴露 logLine', /logLine:/.test(preload));
check('React 侧有全局错误捕获', /addEventListener\('error'/.test(read('src/main.tsx')));
/* ★ 09-15 回归：调试产出的根由 main.js 的 debugDir() 决定（配置里配，仓库里没有），
   写日志前必须自己 mkdir —— 目录不存在就会 ENOENT ⇒ 整个 engine-start 抛掉 ⇒
   点「渲染」报「拉起服务失败」且没有日志。 */
check('★ main.js 写引擎日志前会先建目录（ensureEngineLog）', /function ensureEngineLog\(\)/.test(read('main.js')) && /ensureEngineLog\(\);/.test(read('main.js')));
check('★ 调试产出根由 debugDir() 决定（配置里配，默认不写死盘符）', /function debugDir\(\)/.test(read('main.js')) && /debugDir:\s*''/.test(read('main.js')));
/* ★ 同一类问题：所有相对 __dirname 的路径，目录不存在就得建 */
check('★ 引擎 cwd 用仓库内相对路径（不是写死 E:\\）', /ENGINE_CWD\s*=\s*path\.join\(__dirname/.test(read('main.js')));

/* ---------- 7. 引擎 IPC 返回形状的「两侧名字对得上」 ---------- */
/* ★★ 09-15 真实事故：main.js 给 `{ items:[{id}] }` / `{ image }`，
   而前端读 `{stocks}` / `r.id` / `r.bytes` ⇒ 卷/基准列表全空、一张图都渲染不出来，
   而且**布局自检里的 mock 也跟着前端一起错**，所以全绿。
   这里把两侧的 key 钉死，谁也改不歪。 */
console.log('\n[7] 引擎返回形状（main.js ↔ 前端）');
const mainJsSrc = read('main.js');
const storeSrc = read('src/store/useStore.ts');
const viewerSrc = read('src/components/Viewer.tsx');
const listChans = (mainJsSrc.match(/return r\.ok \? \{ ok: true, items:/g) || []).length;
check('★ main.js 列表类通道返回 items（stocks/bases/params/load）', listChans >= 4, `${listChans} 处`);
const imgChans = (mainJsSrc.match(/ok: true, image:/g) || []).length;
check('★ main.js 图片类通道返回 image（base/render/raw-url）', imgChans >= 3, `${imgChans} 处`);
check(
  '★ useStore 读的是 .items（不是自造的 .stocks/.bases/.params）',
  /s\?\.items/.test(storeSrc) && /b\?\.items/.test(storeSrc) && /p\?\.items/.test(storeSrc),
  '',
  '读错 key ⇒ 卷/基准/滑杆列表永远空'
);
check(
  '★ 前端没再读不存在的 .bytes / r?.id',
  !/\.bytes/.test(viewerSrc) && !/r\?\.id\b/.test(viewerSrc),
  '',
  '又读回了 main.js 不存在的字段'
);
check('★ Viewer 从 items[0] 拿 id', /items\?\.\[0\]/.test(viewerSrc));
check(
  '★ 布局自检的 mock 也返回 items/image（不再比前端还错）',
  /items: \[/.test(read('_check/layout_check.mjs')) &&
    /engineRender: async \(\) => \(\{ ok: true, image:/.test(read('_check/layout_check.mjs')),
  '',
  'mock 形状跟 main.js 不一致，会骗过自检'
);

/* ---------- 汇总 ---------- */
console.log('\n' + '-'.repeat(50));
if (fail === 0) {
  console.log(`全部通过（${pass} 项）`);
} else {
  console.log(`${fail} 项失败 / 共 ${pass + fail} 项：`);
  for (const f of failures) console.log('   ✗ ' + f);
}
console.log('-'.repeat(50) + '\n');
process.exit(fail === 0 ? 0 : 1);
