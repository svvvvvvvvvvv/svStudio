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
import os from 'node:os';
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
/* ⚠ 下面这条只是**漂移探测器**（弱检查）：mock 的形状最终由布局自检里的真浏览器
   端到端检查兜底（"两栏都出图了"）。
   ⚠★ 09-15 踩了两次，都是**这条检查自己太脆**：
    ① 正则写死 `async () =>`。给 `engineRender` 加"记下收到的参数"（新 [7] 组要断言
       "拖过的滑杆真进了渲染请求"）后签名变成 `async (id, opts) =>` ⇒ 误报成红。
    ② `.{0,240}?image:` 的跨度是**字数预算**，而 mock 里那段解释性长注释是中文长句
       ⇒ 稍微多写两句注释就撑破 ⇒ 又误报。
   现在的写法：先把**块注释剥掉**（注释是给人看的，不该占字数），参数表用 `[^)]*` 放过，
   跨度放宽到 400。再要改宽就改 400，别改回写死的签名。 */
const mockSrcFlat = read('_check/layout_check.mjs')
  .replace(/\/\*[\s\S]*?\*\//g, ' ')      // ⚠ 只剥 `/* */`；`//` 不能剥（源码里有 file:/// 这类）
  .replace(/\s+/g, ' ');
check(
  '★ 布局自检的 mock 也返回 items/image（不再比前端还错）',
  /items: \[/.test(mockSrcFlat) &&
    /engineRender: async \([^)]*\) =>.{0,400}?image:/.test(mockSrcFlat),
  '',
  'mock 形状跟 main.js 不一致，会骗过自检'
);

/* ---------- [8] 滑杆：引擎的 PARAMS ↔ 布局自检的 mock ↔ 前端初值 ---------- */
/* ★★ 09-15 加这组的原因：布局自检**跑的是它自己那份假数据**。原来那份里还写着旧名字
   （「本张落点」「提亮」）和旧区间，而检查只断言"名字在不在" ⇒ **它绿着，真界面早就不一样了**。
   所以这里把"引擎的 PARAMS"和"mock"钉在一起，谁改歪都会被抓住。 */
console.log('\n[8] 滑杆（引擎 PARAMS ↔ 布局自检 mock ↔ 前端初值）');
const gradeSrc = read('src/components/GradePanel.tsx');
const svcSrc = exists('svFilm/svFilm/service.py') ? read('svFilm/svFilm/service.py') : '';
const pyRe = /k='([A-Z0-9_]+)',\s+name='([^']+)',\s+lo=(-?[\d.]+),\s*hi=(-?[\d.]+)/g;
const realParams = new Map();
for (const m of svcSrc.matchAll(pyRe)) {
  realParams.set(m[1], { name: m[2], lo: parseFloat(m[3]), hi: parseFloat(m[4]) });
}
check('★ 从引擎 service.py 读到了滑杆清单', realParams.size >= 20, `${realParams.size} 根`);
const mockSrc = read('_check/layout_check.mjs');
const mockRe = /k: '([A-Z0-9_]+)',\s*name: '([^']+)',\s*lo: (-?[\d.]+),\s*hi: (-?[\d.]+)/g;
const mockParams = [...mockSrc.matchAll(mockRe)].map((m) => ({
  k: m[1], name: m[2], lo: parseFloat(m[3]), hi: parseFloat(m[4]),
}));
check('布局自检的 mock 里有滑杆条目', mockParams.length >= 3, `${mockParams.length} 条`);
const drift = mockParams.filter((m) => {
  const r = realParams.get(m.k);
  return !r || r.name !== m.name || r.lo !== m.lo || r.hi !== m.hi;
});
check(
  '★ 布局自检的 mock 与引擎 PARAMS 一致（名字 / 区间都不许漂）',
  drift.length === 0,
  `${mockParams.length} 条全部对齐`,
  '漂了: ' + drift.map((m) => `${m.k}(${m.name} ${m.lo}~${m.hi})`).join(', ')
);
check(
  '★ mock 里带了 dv（否则测不出"显示的数对不对"）',
  mockParams.length > 0 && (mockSrc.match(/k: '[A-Z0-9_]+',[^}]*dv: /g) || []).length >= 3
);
check(
  '★ 前端滑杆初值用的是 dv（不是区间中点）',
  /params\[d\.k\] \?\? d\.dv/.test(gradeSrc),
  '',
  '又退回 (lo+hi)/2 ⇒ 显示的数跟引擎实际用的对不上'
);
check(
  '前端没有把「取区间中点」当成初值',
  !/const v = params\[d\.k\] \?\? \(d\.lo \+ d\.hi\) \/ 2/.test(gradeSrc),
  '',
  '老写法还在'
);

/* ---------- ★ 09-15 参数串：前端到底发得出去吗（"点渲染没反应"的根因守卫） ----------
   为什么单开一段：前端 `grade.params` 是**对象** `{KEY: 数}`，而引擎那口子收的是
   `KEY:VAL,KEY:VAL` 字符串。过去 main.js 直接 `encodeURIComponent(对象)` ⇒ 发出去变成
   `%5Bobject%20Object%5D` ⇒ 引擎按冒号切、切不出来就**静默丢掉**（契约就是"不合法不报错"）
   ⇒ 解出空字典 ⇒ **23 根滑杆一根都没接上**，出图永远是"出厂值"那张。
   ⚠ 上一轮的门禁只验到"引擎收得下参数"，没验"前端发得出参数" —— 闸挡住了 ≠ 没脸。
   ⚠ 所以这一段**真把 main.js 里那段源码取出来在 Node 里跑**，不是拿正则看"有没有写
     paramStr"（那种检查挡不住"函数体写错"，等于没查）。                        */
// ⚠ 必须先剥掉注释再查（那条老写法就写在上面的说明注释里，会被自己的注释误判 —— 09-15 踩过）
const mainJsCode = mainJsSrc.replace(/\/\*[\s\S]*?\*\//g, '');
const psM = mainJsCode.match(/function paramStr\(p\)\s*\{[\s\S]*?\n\}/);
check('main.js 里有 paramStr（参数串的收口点）', !!psM, '', '函数没了？');
let ps = null;
if (psM) {
  try {
    ps = new Function(psM[0] + '; return paramStr;')();
  } catch (e) {
    ps = null;
  }
}
check('paramStr 能在 Node 里独立跑起来（无依赖、可单测）', typeof ps === 'function',
  '', '取出来的那段源码跑不了');
if (typeof ps === 'function') {
  const got = ps({ SPEK_PE_SHIFT: 0.91, ENTRY_SETTLE_SHIFT_EV: -0.25 });
  check('★ paramStr 把对象转成 KEY:VAL,...',
    got === 'SPEK_PE_SHIFT:0.91,ENTRY_SETTLE_SHIFT_EV:-0.25', got);
  check('★ paramStr 的结果里没有 object（老 bug 不会复发）',
    !/object/i.test(got), got, '又变成 [object Object] 了');
  check('paramStr 对字符串原样透传（脚本风格的 A/B 调用）',
    ps('TONE_TOE:0.5') === 'TONE_TOE:0.5', '', String(ps('TONE_TOE:0.5')));
  check('paramStr 丢掉 NaN / 非数字（不污染引擎的 float() 解析）',
    ps({ A: 1, B: NaN, C: 'x', D: 2 }) === 'A:1,D:2',
    '', String(ps({ A: 1, B: NaN, C: 'x', D: 2 })));
  check('paramStr 空输入给空串', ps(null) === '' && ps({}) === '',
    '', `${JSON.stringify(ps(null))} / ${JSON.stringify(ps({}))}`);
}
check('★ engine-render 真的走了 paramStr（没被绕过）',
  /&params=' \+ encodeURIComponent\(paramStr\(o\.params\)\)/.test(mainJsCode),
  '', '还在直接 encodeURIComponent(o.params)');
check('engine-render 里没留下"直接编码对象"的老写法',
  !/encodeURIComponent\(o\.params(?!Str)/.test(mainJsCode), '', '老写法还在');

/* ---------- ★★ 09-15 出图源：必须 RAW 优先（SV 原话：「工作台本来就要优先用 raw」） ----------
   `main.js` 的 `attachLoadPath()` 给每张照片算「喂引擎时用哪个文件」：同名 RAW 优先，
   没有才回落到 JPG；`Viewer` 只认这一个字段，不再自己拼 `rel`。
   **为什么这是硬要求**：入口那一段（零点/成形/趾部/高光护栏）**只在 `io.load_raw` 里跑**，
   喂 JPG 的话「整张亮暗(总)」「暗部亮度」这两根滑杆永远是死的（实测同一张 DSCF0546：
   走 RAW 能带动 −18.9 ~ +31.0 个 L*，走 JPG 是 0.00）。
   ⚠ 这一段同样**不是正则看"有没有写"**，而是把 main.js 里那段源码取出来在 Node 里真跑。 */
const afM = mainJsCode.match(/function attachLoadPath\(sessionPath, photos\)\s*\{[\s\S]*?\n\}/);
check('main.js 里有 attachLoadPath（出图源的唯一出处）', !!afM, '', '函数没了？');
const rexM = mainJsCode.match(/const RAW_EXT = (\/[^\n]*?\/i);/);
check('从 main.js 读到了 RAW_EXT 的定义（自检里不许另写一份）', !!rexM,
  rexM ? rexM[1] : '', '找不着 RAW_EXT');
let af = null;
let tmpDir = null;
if (afM && rexM) {
  try {
    const RAWE = new Function('return ' + rexM[1])();
    af = new Function('fs', 'path', 'RAW_EXT', afM[0] + '; return attachLoadPath;')(fs, path, RAWE);
  } catch (e) {
    af = null;
  }
}
check('attachLoadPath 能在 Node 里独立跑起来（可单测）', typeof af === 'function',
  '', '取出来那段源码跑不了');
if (typeof af === 'function') {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'svstudio-load-'));
  const bucket = path.join(tmpDir, '初筛1星');
  fs.mkdirSync(bucket);
  for (const f of ['A.JPG', 'A.RAF', 'B.JPG', 'C.JPG', 'C.RAF']) {
    fs.writeFileSync(path.join(tmpDir, f), '');
  }
  fs.writeFileSync(path.join(bucket, 'D.JPG'), '');   // 桶里独有：根目录没有 D.JPG
  const ps = [
    { name: 'A.JPG', rel: 'A.JPG' },
    { name: 'B.JPG', rel: 'B.JPG' },
    { name: 'B.JPG', rel: 'B.JPG', dir: bucket },      // 桶里的 B：根目录有原图 ⇒ 该用根目录那份
    { name: 'D.JPG', rel: 'D.JPG', dir: bucket },      // 根目录没有 ⇒ 用桶里这份
    { name: 'C.JPG', rel: 'sub\\C.JPG' },              // rel 带分隔符也要只取文件名
  ];
  af(tmpDir, ps);
  check('★ 有同名 RAW ⇒ 出图源用 RAW',
    ps[0].loadPath === path.join(tmpDir, 'A.RAF') && ps[0].loadIsRaw === true,
    ps[0].loadPath, String(ps[0].loadPath));
  check('★ 没有同名 RAW ⇒ 回落 JPG（不许空栏）',
    ps[1].loadPath === path.join(tmpDir, 'B.JPG') && ps[1].loadIsRaw === false,
    ps[1].loadPath, String(ps[1].loadPath));
  check('桶里的照片（根目录有同名原图）⇒ 出图源指向根目录原图',
    ps[2].loadPath === path.join(tmpDir, 'B.JPG'), ps[2].loadPath, String(ps[2].loadPath));
  check('桶里独有的照片（根目录没有）⇒ 出图源指向它自己',
    ps[3].loadPath === path.join(bucket, 'D.JPG'), ps[3].loadPath, String(ps[3].loadPath));
  check('rel 带目录分隔符也认（只取文件名配 RAW）',
    ps[4].loadPath === path.join(tmpDir, 'C.RAF'), ps[4].loadPath, String(ps[4].loadPath));
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* 留着也无害 */ }
}
check('★ 调色台加载用的是 loadPath（RAW 优先），不是自己拼 rel',
  /const full = p\.loadPath \|\| sessionPath/.test(viewerSrc), '',
  '还在自己拼 rel ⇒ 又会喂 JPG');
check('★ 出图源的标记进的是 note（没污染 img 的 alt）',
  /note=\{p \? \(p\.loadIsRaw/.test(viewerSrc) && /title="调色后"/.test(viewerSrc), '',
  'alt 被改掉的话，布局自检靠 alt 认栏位会一起失效');

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
