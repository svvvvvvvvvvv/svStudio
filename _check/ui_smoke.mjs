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
import crypto from 'node:crypto';
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
/* ⚠ 锚点必须是**声明本身**（`\nexport const API = {`），不能只是 `indexOf('export const API')` ——
   09-15 踩过：文件顶部的说明注释里写了一模一样的这几个字，于是 `apiBody` 变成"从注释开始到文件尾"
   ⇒ 把 `Photo`/`Thumb`/`ParamDef` 这些接口的字段名（`name`/`rel`/`ow`/`url`/`k`/`lo`…）
     全当成"API 层封了但 preload 里没有的通道" ⇒ 误报一大串。**检查自己被自己的注释骗了。** */
const apiDecl = apiTs.indexOf('\nexport const API = {');
const apiBody = apiDecl >= 0 ? apiTs.slice(apiDecl) : '';
check('找到 API 对象的声明（找不到就说明锚点又漂了）', apiDecl >= 0, `@${apiDecl}`);
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

/* ★★ 导出成片（09-15 SV 选「A」第 ② 项）——`export-image` 是**唯一**一个
   "回来的是文件路径、不是图片数据"的通道（写盘由引擎做，为的是保住 EXIF）。
   两边的字段名必须对得上，而且**前端不许自己拼参数串**（要走唯一的 `paramStr`）。 */
const preloadSrc = read('preload.js');
const apiSrc = read('src/api/index.ts');
const svcSrcForExport = read('svFilm/svFilm/service.py');
check('★ main.js 有 export-image 通道，且回来的是**文件路径**（不是 image 数据）',
  /ipcMain\.handle\('export-image'[\s\S]{0,3000}?Object\.assign\(\{ ok: true \}, r\.data/.test(mainJsSrc),
  '', '导出拿不到路径 ⇒ 界面只能说"失败了"，用户不知道文件去哪了');
check('★ 导出的请求也走 paramStr（不许前端自己 encodeURIComponent 参数对象）',
  /export-image[\s\S]{0,3000}?'&params=' \+ encodeURIComponent\(paramStr\(p\.params\)\)/.test(mainJsSrc),
  '', '把参数**对象**直接编码 ⇒ `[object Object]` ⇒ 引擎静默解出空对象（09-15 那 23 根滑杆的坑）');
check('★ preload 暴露了 exportImage',
  /exportImage: \(payload\) => ipcRenderer\.invoke\('export-image'/.test(preloadSrc),
  '', '少了它 ⇒ 右栏那个按钮点了报 undefined');
check('★ api 层也封了 exportImage（和别的通道一样三件齐：类型 + Window + 封装）',
  /exportImage: \(payload: any\) => Promise<any>/.test(apiSrc) &&
    /exportImage: \(payload: any\) => api\(\)\.exportImage\(payload\)/.test(apiSrc),
  '', 'preload 有、api 没封 ⇒ 前端调不到（"接了半截"那一类）');
check('★ 导出用 p.loadPath（同名 RAW 优先），不是身份键 rel',
  /exportImage[\s\S]{0,1500}?src: p\.loadPath/.test(storeSrc),
  '', '拿 `rel` 当出图源 ⇒ 喂错文件（星级/归档也按 rel 索引，动它会连坐）');
check('★ 导出尺寸**由引擎定**（前端一个写死的 side 都没有）',
  !/side: \d+/.test(storeSrc), '',
  '前端写死导出尺寸 ⇒ 引擎改了工作分辨率，前端还按老数字要图');
check('★★ 引擎 /export 有**尺寸上限**（原图全尺寸会当场 OOM）',
  /EXPORT_MAX_SIDE/.test(svcSrcForExport) && /side_clamped/.test(svcSrcForExport),
  '', '不设上限 ⇒ 导出 40MP 时 spektrafilm 要一次分配 24.2 GiB，引擎进程直接死');
check('★★ 被夹住要说出来（`side_clamped` 一路回到提示里）',
  /side_clamped/.test(storeSrc) && /side_clamped/.test(svcSrcForExport), '',
  '静默降级 ⇒ 用户以为导出的是原尺寸');

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
/* ⚠ `attachLoadPath` 现在会调 `sessionSrcDir(sessionPath)`（预览索引目录 ⇒ 出图源在源目录里）。
   不把它一起抽出来的话，下面那段抽出来在 Node 里一跑就 ReferenceError
   —— 检查直接抛出去、整个自检断在那儿（这是**好事**：说明这条钩子真的在跑）。 */
const ssdM = mainJsCode.match(/function sessionSrcDir\(sessionPath\)\s*\{[\s\S]*?\n\}/);
check('main.js 里有 sessionSrcDir（“缓存目录 → 它的源目录”的唯一出处）', !!ssdM, '',
  '函数没了 ⇒ attachLoadPath 取不到源目录，渲染会拿 1600 的预览小图当原图用');
const rexM = mainJsCode.match(/const RAW_EXT = (\/[^\n]*?\/i);/);
check('从 main.js 读到了 RAW_EXT 的定义（自检里不许另写一份）', !!rexM,
  rexM ? rexM[1] : '', '找不着 RAW_EXT');
let af = null;
let tmpDir = null;
if (afM && rexM) {
  try {
    const RAWE = new Function('return ' + rexM[1])();
    af = new Function('fs', 'path', 'RAW_EXT', (ssdM ? ssdM[0] : '') + '\n' + afM[0] +
      '; return attachLoadPath;')(fs, path, RAWE);
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

  /* ★★ 预览索引目录（库外·纯 RAW）：出图源必须指回**源目录**的 RAW。
     缓存里只有长边 1600 的预览小图；不指回去 ⇒ 引擎拿它当原图渲染
     —— 能出图、界面不报错，画质悰悰掉了（这条是这个功能最容易漏的破洞）。 */
  const idxDir = path.join(tmpDir, '_idx');
  const realSrc = path.join(tmpDir, '_realsrc');
  fs.mkdirSync(idxDir);
  fs.mkdirSync(realSrc);
  fs.writeFileSync(path.join(realSrc, 'E.RAF'), '');
  fs.writeFileSync(path.join(idxDir, 'E.JPG'), '');              // 缓存里只有小图，没 RAW
  fs.writeFileSync(path.join(idxDir, '_src.txt'), realSrc + '\n');
  const ps2 = [{ name: 'E.JPG', rel: 'E.JPG' }];
  af(idxDir, ps2);
  check('★★ 预览索引目录 ⇒ 出图源指回**源目录**的 RAW（不是拿缩略图渲染）',
    ps2[0].loadPath === path.join(realSrc, 'E.RAF') && ps2[0].loadIsRaw === true,
    ps2[0].loadPath,
    `${ps2[0].loadPath} ⇒ 渲染会把预览小图当原图，画质悰悰掉了、界面一点看不出来`);
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* 留着也无害 */ }
}
check('★ 调色台加载用的是 loadPath（RAW 优先），不是自己拼 rel',
  /const full = p\.loadPath \|\| sessionPath/.test(viewerSrc), '',
  '还在自己拼 rel ⇒ 又会喂 JPG');
check('★ 出图源的标记进的是 note（没污染 img 的 alt）',
  /note=\{p \? \(p\.loadIsRaw/.test(viewerSrc) && /title="调色后"/.test(viewerSrc), '',
  'alt 被改掉的话，布局自检靠 alt 认栏位会一起失效');

/* ---------- [9] 照片导入（SD 卡 → 照片库） ----------
   ★★ 这一组为什么值得单独写：导入是**会真写盘、真动几百个文件、十几分钟**的操作。
      它出错的方式和别的功能不一样 —— 不是"界面不好看"，是**把卡里的东西搞丢**。
      所以这一段只做三件事，全都冲着"别动源卡"和"别导错地方"：
        ① 参数拼装（纯函数）：必填项、空值不传、`--key=value` 形式
        ② 输出解析（纯函数）：拿**脚本的真实输出**当样本，断言解析得出来
        ③ 接线守卫：左栏常显、跑完进新主题、**主进程这段一行文件操作都没有**
   ⚠ 这里**不是**拿正则看"有没有写某个函数名"——那挡不住"函数体写错"，等于没查。
     前两项是把 main.js 里那两段源码抽出来在 Node 里**真跑**。 */
console.log('\n[9] 照片导入（纯函数真跑 + 接线守卫）');
const biM = mainJsCode.match(/function buildImportArgs\(o, dryRun\)\s*\{[\s\S]*?\n\}/);
const piM = mainJsCode.match(/function parseImportOutput\(text\)\s*\{[\s\S]*?\n\}/);
check('main.js 里有 buildImportArgs（表单 → 命令行，纯函数）', !!biM, '', '函数没了？');
check('main.js 里有 parseImportOutput（脚本输出 → 结构化，纯函数）', !!piM, '', '函数没了？');
let bi = null;
if (biM) {
  try {
    bi = new Function(biM[0] + '; return buildImportArgs;')();
  } catch (e) {
    bi = null;
  }
}
check('buildImportArgs 能在 Node 里独立跑起来', typeof bi === 'function', '', '取出来那段跑不了');
if (typeof bi === 'function') {
  const A = (o, d) => bi(o, d).join(' ');
  check(
    '★ 参数用 --key=value 形式（空格分隔会被 argparse 串位）',
    A({ topic: '互勉约拍', place: '园岭新村', destRoot: 'D:\\照片库' }, true) ===
      '--topic=互勉约拍 --place=园岭新村 --dest-root=D:\\照片库 --dry-run',
    A({ topic: '互勉约拍', place: '园岭新村', destRoot: 'D:\\照片库' }, true),
    '拼出来的串不对'
  );
  check(
    '★ 日期留空 / auto 都不传（脚本自己会从 EXIF 推断）',
    bi({ topic: 't', place: 'p', date: '' }, false).includes('--date=') === false &&
      bi({ topic: 't', place: 'p', date: 'auto' }, false).includes('--date=') === false &&
      bi({ topic: 't', place: 'p', date: '2026-04-24' }, false).includes('--date=2026-04-24'),
    '',
    '把 auto 当"用户填了日期"传过去了 ⇒ 日期推断死了'
  );
  check(
    '★ 空的可选项一律不传（不是传空串 —— 传空串会把脚本的默认值覆盖掉）',
    bi({ topic: 't', place: 'p', src: '', destRoot: '', folder: '' }, false).join(' ') ===
      '--topic=t --place=p',
    bi({ topic: 't', place: 'p', src: '', destRoot: '', folder: '' }, false).join(' '),
    '空值也传了 ⇒ 预演和实跑的结论会跟真跑不一致'
  );
  check('不预演时没有 --dry-run', !bi({ topic: 't', place: 'p' }, false).includes('--dry-run'));
  let threw = '';
  try {
    bi({ place: 'p' }, true);
  } catch (e) {
    threw = e.message;
  }
  check('★ 缺主题时**当场抛错**（不是让子进程跑到一半报 argparse usage）',
    /主题/.test(threw), threw, '没抛 ⇒ 用户会看到一屏英文');
  /* 值里带换行/回车：不能让一行参数变成两行（日志与解析都会被带歪） */
  check('参数值里的换行被清掉',
    bi({ topic: 'a\nb', place: 'p' }, false).join(' ').includes('\n') === false);
}
let pi = null;
if (piM) {
  try {
    pi = new Function('path', piM[0] + '; return parseImportOutput;')(path);
  } catch (e) {
    pi = null;
  }
}
check('parseImportOutput 能在 Node 里独立跑起来', typeof pi === 'function', '', '取出来那段跑不了');
if (typeof pi === 'function') {
  /* ⚠⚠ 这份样本**是从脚本的真实输出抄下来的**（09-15 实跑 `--dry-run` 拿的），
     不是我自己编的句型。编的句型会"两边一起错、检查全绿"。
     ⚠ 反斜杠要写两个 —— JS 字符串里 `'\D'` 会变成 `'D'`（反斜杠被吃掉）。 */
  const DRY_OUT = [
    '源目录 : D:\\DCIM\\100_FUJI',
    '文件数 : 524 个   总大小 12.34 GB',
    '  类型 : JPG×300, RAF×224',
    '  日期 : 2026-04-24 ~ 2026-04-25（跨 2 天）',
    '候选盘 : E: 剩 362.89 GB',
    '目标盘 : E（剩 362.89 GB）',
    '目标目录: E:\\照片库\\2026-04-24_旅行_长洲岛',
    '',
    '[dry-run] 未执行复制。去掉 --dry-run 正式导入。',
  ].join('\n');
  const RUN_OUT = DRY_OUT.slice(0, DRY_OUT.indexOf('\n[dry-run]')) + [
    '',
    '开始复制…',
    '  100/524  已复制 2.30 GB  (85 MB/s)',
    '复制完成：新增 524，跳过(已存在) 0，失败 0',
    '校验    ：源 524 个 / 12.34 GB  →  目标 524 个 / 12.34 GB',
    '校验通过：文件数与总字节数一致',
    '',
    '耗时 45.2 秒',
    '导入位置：E:\\照片库\\2026-04-24_旅行_长洲岛',
  ].join('\n');

  const d = pi(DRY_OUT);
  check('★ 解析出「要拷几个 / 多大」（预演的核心信息）',
    d.files === 524 && d.total === '12.34 GB', `${d.files} / ${d.total}`,
    '解析不出来 ⇒ 界面显示"要拷 — 个文件"');
  check('★ 解析出目标文件夹名（跑完要靠它进新主题的选片台）',
    d.folder === '2026-04-24_旅行_长洲岛', d.folder, `期望 2026-04-24_旅行_长洲岛，实际 ${d.folder}`);
  check('★ 认出这是一次预演（不是真拷了）', d.dryRun === true && d.copied == null);
  const r2 = pi(RUN_OUT);
  check('★ 真跑的样本解析出「新增/跳过/失败」三个数',
    r2.copied === 524 && r2.skipped === 0 && r2.failed === 0,
    `${r2.copied}/${r2.skipped}/${r2.failed}`, '解析不出来 ⇒ 界面说不清到底拷进去没有');
  check('★ 认出「校验通过」（这是"导入成功"唯一的凭据）', r2.verified === true, '',
    '校验那条没认出来 ⇒ 用户不知道到底成没成');
  check('★ 解析出耗时', r2.elapsed === '45.2', r2.elapsed);
  const e1 = pi('[错误] 找不到源目录。请插好卡/U盘，或用 --src 指定。');
  check('★ 脚本报错时把它的原话带出来（不是只说"失败了"）',
    /找不到源目录/.test(e1.error), e1.error, '用户看不到为什么失败');
  check('空输入不炸（第一次打开对话框就是空）',
    pi('').files == null && pi('').folder === '');
  /* ★★ 台词漂移守卫：解析锚的是脚本里那几句话，脚本改了台词这里就静静失效。
     脚本在仓库外（用户自己的 skills 目录），所以按"找得到就查、找不到就跳过"处理。 */
  const impScript = [
    process.env.SVIMPORT_SCRIPT || '',
    path.join(os.homedir(), '.workbuddy', 'skills', 'photo-import', 'scripts', 'import_photos.py'),
  ].find((p) => p && fs.existsSync(p));
  if (impScript) {
    const isrc = fs.readFileSync(impScript, 'utf8');
    const anchors = ['源目录 :', '文件数 :', '目标目录:', '[dry-run] 未执行复制', '耗时', '导入位置：'];
    const missing = anchors.filter((a) => !isrc.includes(a));
    check('★ 导入脚本的台词没变（解析锚的就是这几句）', missing.length === 0,
      `${anchors.length} 句全在`, `脚本里没了：${missing.join(' , ')} —— 改脚本就要改 main.js 的 parseImportOutput`);
    /* ★ 只复制、绝不动源：脚本自己的契约，也在这一起钉住 */
    check('★ 导入脚本自己不删/不移动源文件（"只复制"的契约没被破坏）',
      !/os\.remove|os\.unlink|shutil\.move/.test(isrc), '',
      '脚本里出现了删除/移动 —— 源卡安全性不再有保证');
  } else {
    ok('导入脚本不在本机（跳过台词漂移检查）');
  }
}

/* 接线：入口在哪、跑完去哪、主进程有没有自己去动文件 */
const storeCode = storeSrc.replace(/\/\*[\s\S]*?\*\//g, '');
const runM = storeCode.match(/runImport: async \(\) => \{[\s\S]*?\n  \},/);
check('★ 左栏**常显**（不挂在 sessionName 上）—— 空库/新库也得点得到导入',
  !/\{sessionName && <SessionPane/.test(appTsx) && /<SessionPane \/>/.test(appTsx),
  '', '左栏还在条件渲染 ⇒ 新库里"导入照片"永远点不到（而新库最需要它）');
check('★ App 挂了导入对话框', /<ImportDialog \/>/.test(appTsx));
const sessTsx = read('src/components/SessionPane.tsx');
check('★ 左栏顶部有导入入口 + 换图库', /data-import-open/.test(sessTsx) && /换图库/.test(sessTsx));
check('★ 导入跑完用 refreshSessions（不是 loadSessions）',
  !!runM && /refreshSessions\(\)/.test(runM[0]) && !/loadSessions\(\)/.test(runM[0]),
  '', 'loadSessions 会顺带"恢复到上次的主题"，把刚导进来的又换掉 ⇒ 跑完看到的还是旧主题');
check('★ 导入跑完直接进新主题 + 切到选片台',
  !!runM && /enterSession\(folder\)/.test(runM[0]) && /setMode\('pick'\)/.test(runM[0]),
  '', '导完停在原地 ⇒ 用户以为白导了');
check('★ 主进程的导入那段**一行文件操作都没有**（复制全交给脚本）',
  (() => {
    const sec = mainJsCode.slice(mainJsCode.indexOf('function importScriptPath()'));
    return sec.length > 1000 &&
      !/rmSync|unlinkSync|renameSync|copyFileSync|createWriteStream|writeFileSync/.test(sec);
  })(),
  '', '主进程自己动文件了 ⇒ "只复制、绝不动源卡"那份契约就不再有保证');
check('★ 导入脚本路径不写死个人路径（走配置 / 环境变量）',
  /SVIMPORT_SCRIPT/.test(mainJsCode) && /SVIMPORT_PY/.test(mainJsCode) && !/Users\\psw99/.test(mainJsCode),
  '', '写死了本机路径 ⇒ 仓库不能开源、换台机器就跑不了');
check('★ 进度事件的通道名两端一致',
  /webContents\.send\('import-progress'/.test(mainJsSrc) &&
    /ipcRenderer\.on\('import-progress'/.test(preload),
  '', '主进程发一个名、preload 听另一个名 ⇒ 进度永远是空的（而且不报错）');
check('★ 对话框收尾**取消订阅**（否则切主题几次就多路重复推送）',
  /typeof off === 'function'/.test(read('src/components/ImportDialog.tsx')),
  '', '没退订 ⇒ 日志行会重复出现');
/* ★ 实测出来的一个反直觉行为（`_probe_import_e2e.mjs` 真跑出来的）：
   目标文件夹**同名已存在**时，脚本不合并、而是新建 `_2`；而且 dry-run 阶段**不做**改名检查
   ⇒ 预演里显示的目录名会和实跑不一样。不是 bug（"不许偷偷合进旧文件夹"的代价），
   但界面必须把这句 `[提示] 已存在，改用 …` 显出来，否则用户会莫名多出一个 `_2` 主题。 */
check('★ 「同名已存在 ⇒ 改用 _2」这句提示会显示给用户',
  /plan\.renamed/.test(read('src/components/ImportDialog.tsx')) && /renamed/.test(apiTs),
  '', '不显示的话，用户会莫名多出一个 _2 主题、而且看不出为什么');
/* ★ 进度条：**必须从日志派生**、不许在 store 里再存一份"百分比"。
   存两份的下场是它们不同步 —— 而这个项目里"看着对、其实对不上"这类 bug 最难发现
   （滑杆的 dv、出图源的 rel、星级的三处同步，都是同一类）。 */
const diaSrc = read('src/components/ImportDialog.tsx');
check('★ 进度条从日志派生（没有第二份"百分比"状态）',
  /data-import-fill/.test(diaSrc) && /已复制/.test(diaSrc) && /const prog = useMemo/.test(diaSrc),
  '', '条没接上日志 ⇒ 永远停在 0%');
check('★ store 里没偷偷存一份 importPercent 之类的重复状态',
  !/importPercent|importPct/.test(storeSrc), '', '又存了两份会不同步的状态');
check('★ 布局自检的 mock 也实现了导入通道（否则那条真浏览器检查是空转）',
  /importDetect: async/.test(mockSrcFlat) &&
    /importPreview: async/.test(mockSrcFlat) &&
    /importRun: async/.test(mockSrcFlat) &&
    /onImportProgress:/.test(mockSrcFlat),
  '', 'mock 少一个 ⇒ 点下去静默抛错，检查看不见');

/* ---------- [10] 基准默认 / 右栏两个按钮 / 按主题存配方（09-15） ---------- */
/* ★ 这一组对着三个真问题：
   ① 「成色基准」默认值是前端写死的 `'all'`，而引擎基准表（`config.BASE_TABLE`）里
      没有这一支 ⇒ 引擎 `stocks.resolve_base` **静默**回落成 `BASE_NONE`（"不套基准"），
      界面上四支**一支都不亮** —— 画面错了、还看不出来。
      ⇒ 规矩：**默认值由引擎给**（`/bases` 每条带 `isDefault`），**前端不许出现基准名**。
   ② 右下角「恢复默认」「存到主题」**没有 onClick**（死按钮：界面在、功能不在）。
   ③ 「存到主题」的四个接口（main.js / preload / 类型 / 封装）早就写好了，
      前端从来没调过 —— 不是缺功能，是**接了半截**。 */
console.log('\n[10] 基准默认 / 右栏两个按钮 / 按主题存配方');

check('★ 引擎 /bases 标出了"哪一支是默认"（前端据此定初值）',
  /isDefault=bool\(n == getattr\(C, 'BASE', None\)\)/.test(svcSrc), '',
  '引擎不给默认标志 ⇒ 前端只能自己猜，迟早又写出一个写死的名字');
/* ★ 取默认基准有**三处**，每一处都必须读引擎的 `isDefault`：
     ① `loadEngine`（引擎元数据回来时定初值）
     ② `enterSession`（进主题、而该主题没存过配方时回出厂）
     ③ `resetGrade`（点「恢复默认」时回默认）
   ⚠★ 为什么必须"三处一起盯"：**只改其中一处会全绿** —— 09-15 破法验证时发现的：
        只把 ① 改成"取列表第一支"，检查竟然一条都不红，因为 ② 紧接着又把 base 设对了。
        （同款"假绿"见过三次了：React bail-out、被 goHome 兜住的归零、这次是路径互相兜。）
   ⇒ 所以这条用的是**计数**，不是"文件里出现过"。 */
check('★ 三处取默认基准都读的是引擎的 isDefault（加载时 / 进主题时 / 恢复默认时）',
  (storeCode.match(/\.find\(\(x\) => x\.isDefault\)/g) || []).length >= 3,
  '', '有一处没读 isDefault ⇒ 那条路径会给出一个"前端猜的名字"；而且往往被别的路径兜住、看不出来');
check('★ 前端不再写死基准名（store 里一个 BASE_* 都不许有）',
  !/BASE_[A-Z]+/.test(storeCode), '',
  '前端写死基准名 ⇒ 引擎改配置就静默错位（过去写死「all」就是这么错的）');
check('★ 基准初值是空串（等引擎回来填），不是某个猜出来的名字',
  /base: ''/.test(storeSrc), '',
  '初值又写成某个名字 ⇒ 引擎表里没有就静默回落成"不套基准"');
/* ⚠ 必须**剥掉注释再查**：上面那段说明注释里本来就写着 `base: 'all'`，
   不剥注释的话这条会被**自己的注释**判红（09-15 踩过同款：注释里出现 `export const API`
   ⇒ 锚点落到注释上、误报一整串）。 */
const gpCode = gradeSrc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
check('★ 「恢复默认」「存到主题」两个按钮**都接上线了**（真 onClick）',
  /onClick=\{resetGrade\}/.test(gpCode) && /onClick=\{saveGradeToTheme\}/.test(gpCode), '',
  '没有 onClick = 死按钮（界面在、功能不在，最难自己发现）');
check('★ 两个按钮指向 store 里真实存在的 action',
  /resetGrade: \(\) =>/.test(storeCode) && /saveGradeToTheme: async \(\) =>/.test(storeCode),
  '', '按钮指向一个不存在的 action ⇒ 点下去就抛错');
check('★ 「存到主题」真的落盘（走 setGrade 通道）',
  /API\.setGrade\(name, get\(\)\.grade\)/.test(storeCode), '',
  '没落盘 ⇒ 重启就没了，用户以为存上了');
check('★ 「存到主题」还**读回来**（进主题时把配方套回）',
  /API\.getGrade\(name\)/.test(storeCode), '',
  '只写不读 = 存了个寂寞（本项目最忌的"看着对、其实对不上"）');
check('★ 没存过的主题回出厂（不把上一个主题的调整带过去）',
  /* ⚠ 锚点跟着声明走：09-15 加了相纸之后那一行变成
     `grade: { stock: …, base: dfltName, paper: pickPaper(plist), params: {} }`
     —— 原来写死的 `base: dfltName, params: {}` 就匹配不到了（检查比代码先过时，
     表现是"检查红了但代码是对的"，很容易顺手把对的代码改坏）。
     这里放过中间的 `paper: …`，但**仍然要求** base 回默认 + 参数清空两件事都在。 */
  /base: dfltName,[\s\S]{0,120}?params: \{\}/.test(storeCode), '',
  '沿用上一个主题的值 ⇒ 主题之间串味（在 A 拧过的滑杆跟着进 B）');
const resetM = storeCode.match(/resetGrade: \(\) => \{[\s\S]*?\n  \},/);
check('★ 「恢复默认」**不动卷**（只清滑杆 + 基准回默认）',
  !!resetM && /\.\.\.get\(\)\.grade/.test(resetM[0]) && !/\bstock\b/.test(resetM[0]), '',
  '顺手把卷也抹了 ⇒ 用户会莫名其妙换了个胶片（卷是"拍什么"，不是调出来的）');
check('★ 基准那一排有可断言的选中标记（不是靠认颜色/边框）',
  /data-base-on=/.test(gradeSrc), '',
  '没有标记 ⇒ 布局自检只能去认颜色，改皮肤就废');
check('★ 布局自检的 mock 实现了 getGrade / setGrade（否则那条真浏览器检查是空转）',
  /getGrade: async \(name\)/.test(mockSrcFlat) && /setGrade: async \(name, g\)/.test(mockSrcFlat),
  '', 'mock 少一个 ⇒ enterSession 里那次调用静默抛错，检查看不见（漏 setConfig 那次的翻版）');
check('★ 布局自检 mock 的基准列表照生产端带了 isDefault（形状 + 数据都要照抄）',
  /isDefault: true/.test(mockSrcFlat), '',
  'mock 不带这个字段 ⇒「选中的是引擎给的默认那支」这条测不到（绿着但没有意义）');

/* ★★ 09-15 第二批：同一个坑的**另外两条入口** —— 这两条都不是"少了功能"，
   是"代码看着对、实际静默降级"（本项目最忌的那类）：
   ④ 进主题套回配方时**不校验 `base`**：配置是能手改、旧版本也写过的，
      存过一个引擎不认的名字（旧版就写死过 `'all'`）⇒ 一进这个主题，
      四支基准一支都不亮 + 出图悄悄变成"不套基准"。 */
check('★ 进主题套回配方时**要校验基准名**（配置是能被手改/被旧版本写过的）',
  /list\.some\(\(x\) => x\.name === b\)/.test(storeCode), '',
  '原样信任配置里的 base ⇒ 存过一个引擎不认的名字就静默变"不套基准"，界面一支都不亮');
check('★ 校验不过就回引擎默认 + 明说一句（不静默改掉用户存的值）',
  /base: known \? b : dfltName/.test(storeCode) && /引擎不认/.test(storeSrc), '',
  '静默改掉存过的值 ⇒ 下次打开"跟存的不一样"，还不知道为什么');
check('★ 「取引擎默认基准」这个动作读的是 isDefault 那条（不是列表第一支）',
  /const dfltName = \(list\.find\(\(x\) => x\.isDefault\) \|\| list\[0\]\)\?\.name \?\? ''/.test(storeCode),
  '', '腿短取 list[0] ⇒ 引擎换了默认就静默错位（mock 里默认那支故意排在最后，所以这条测得出）');

/* ⑤ `defaultConfig()` 里三个"假状态键"：`lastIdx`/`lastFilter`/`mode` —— 注释写着
   "重启后恢复"，但**全仓库没有任何一处读它们**（真键是平铺的 lastSession/lastCur/lastMode）。
   留着就是给下一个人下套：读 `cfg.lastIdx` 会拿到几周前的 `220`，还以为是"当前的"。 */
check('★ main.js 的 defaultConfig 里不再声明没人读的假状态键',
  !/lastIdx\s*:/.test(mainJsCode) && !/lastFilter\s*:/.test(mainJsCode) &&
    !/[^A-Za-z]mode\s*:\s*'pick'/.test(mainJsCode), '',
  '声明 + "重启后恢复"的注释，却没人读 ⇒ 下一个人真去读它，拿到的是几周前的旧值');
check('★ 老配置里那几个孤儿键会被清掉（照 archiveRoot/lrExe 的老办法）',
  /delete cfg\.lastIdx/.test(mainJsCode) && /delete cfg\.lastFilter/.test(mainJsCode) &&
    /delete cfg\.mode\b/.test(mainJsCode) && /delete cfg\.last\b/.test(mainJsCode), '',
  '不清 ⇒ 用户的 config.json 里永远留着 `last:{cur:12}` 这种孤儿，「谁在读」的问题每年重问一遍');
check('★ 真正在用的两个状态键在 defaultConfig 里有正经初值（不是靠 ?? 兜底）',
  /lastCur:\s*0/.test(mainJsCode) && /lastMode:\s*'pick'/.test(mainJsCode), '',
  '不声明 ⇒ 新装的用户配置里没这两个键，看着像"状态记忆没实现"');

/* ---------- 11. 大图缩放（09-15 SV 选「B」） ----------
   ★ 机制在**源码层**钉死，行为在布局自检 `[14]` 里量。
   ⚠ 锚点必须取**声明本身**：ZoomImage.tsx 的说明注释里就写着 `{passive:false}` 这几个字，
     拿 `passive: false` 当锚点会被自己的注释骗（09-15 在 api/index.ts 上栽过一次）。 */
console.log('\n[11] 大图缩放（一份实现两个入口）');
const zoomSrc = read('src/components/ZoomImage.tsx');
const viewerCode = read('src/components/Viewer.tsx');
check('★ 两处大图共用 ZoomImage（别再各写一套）',
  /import\s*\{\s*ZoomImage\s*\}/.test(viewerCode) &&
    (viewerCode.match(/<ZoomImage\b/g) || []).length === 2 &&
    !/transform:\s*`translate/.test(viewerCode), '',
  'Viewer 自己又写一套缩放 ⇒ 两份实现早晚会长歪（本项目的老毛病）');
check('★ 滚轮用**原生监听 + passive:false**（React 的 onWheel 是 passive 的）',
  /el\.addEventListener\('wheel', onWheel, \{ passive: false \}\)/.test(zoomSrc), '',
  '用 React 的 onWheel ⇒ 里面 preventDefault 无效，滚轮会把页面一起滚走');
check('★ 换图回「适应」（不然翻到下一张还停在上次的放大倍数上）',
  /useEffect\(\(\) => \{\s*reset\(\);\s*\}, \[src, reset\]\);/.test(zoomSrc), '',
  '不复位 ⇒ 翻一张图还停在上次那 400%，看着像"图坏了"');
check('★ 「1:1」的倍率是**算出来的**（按 contain 实际画出的宽，不是盒子宽）',
  /const drawnW = \(\(\) => \{/.test(zoomSrc) && /bw \/ bh > ar \? bh \* ar : bw/.test(zoomSrc), '',
  '拿盒子宽当基准 ⇒ contain 留的黑边被算进去，徽标上的 100% 是假的');

/* ---------- 12. 相纸（09-15 SV 选「C」） ----------
   ★ 为什么单开一组：一张真卷出图 = **(负片, 相纸)** 二元组。原来只开放了负片那一半
     （`init_params(film_profile=…)` 里的相纸是写死的）—— 而**相纸是最终成色的另一半**：
     同一卷负片印在不同纸上，是两套不同的颜色（人像最经典的就是
     「柯达卷 + Portra Endura」和「富士卷 + Crystal Archive」两套脸色）。
   ★ 这一组盯的是三件容易"接了半截"的事（本项目最常见的一类 bug）：
     ① 引擎侧：名字认不得要**回落 + 说出来**（别静默换纸，也别当场崩）
     ② 渲染链：`paper` 必须**一路传到物理链**（少一环 ⇒ 画面不变，用户以为"这张纸没效果"）
     ③ 段缓存：键里**必须带纸**（否则换纸后出图还是上一张的缓存 —— 同样是画面不变）
   ⚠ 而"哪张是这一卷的配套纸"**由引擎给**（`papers(stock)` 里的 `isDefault`）——
     跟基准那条同一个规矩：**前端不许写死纸名**。 */
console.log('\n[12] 相纸（印相纸要能选）');
const spekSrc = exists('svFilm/svFilm/spektra.py') ? read('svFilm/svFilm/spektra.py') : '';
const pipeSrc = exists('svFilm/svFilm/pipeline.py') ? read('svFilm/svFilm/pipeline.py') : '';

/* 引擎的相纸名（只扫 `PAPERS = {` 到 `PAPER_ORDER` 之间，并剥掉 `#` 注释 ——
   注释里也出现过纸名，不剥会被自己的注释骗，09-15 在 api/index.ts 上栽过一次）。 */
const pA = spekSrc.indexOf('PAPERS = {');
const pB = spekSrc.indexOf('PAPER_ORDER');
const paperBlock = pA >= 0 && pB > pA ? spekSrc.slice(pA, pB).replace(/#[^\n]*/g, '') : '';
const paperNames = [...paperBlock.matchAll(/\n\s+'([a-z0-9_]+)':/g)].map((m) => m[1]);
check('★ 从引擎 spektra.py 读到了相纸表', paperNames.length >= 6, `${paperNames.length} 张`);

check('★ 引擎有"认不得就回落、并且说出来"的相纸解析器',
  /def resolve_paper\(stock_name, paper=None\):/.test(spekSrc) && /unknown_paper/.test(spekSrc), '',
  '没有它 ⇒ 旧配方/手改配置里的脏名字要么直接塞给 init_params（FileNotFoundError 当场崩），' +
    '要么被静默换一张（本项目最阴的那类坑）');
check('★ 回落时**报告里标出来**（`print_fallback` 一路传到 /stats）',
  /* ⚠ 锚点取**声明本身**：pipeline 写的是 `print_fallback=bool(_paper_fb)`，
     service 是 `paper_fallback=st.get('print_fallback')` —— 别去 grep 那个光秃秃的字段名。 */
  /print_fallback=bool\(_paper_fb\)/.test(pipeSrc) &&
    /paper_fallback=st\.get\('print_fallback'\)/.test(svcSrc), '',
  '静默回落 ⇒ 用户以为在用 A 纸，其实出的是 B 纸，还看不出哪里不对');
check('★ 真卷之外没有"相纸"这回事（中性卷返回**空表**）',
  /if pair is None:\s*\n\s*return \[\]/.test(spekSrc), '',
  '中性卷也列 8 张纸、一张都不亮 ⇒ 把一个拧不动的开关摆给用户（看着能用、其实不生效）');
check('★ 相纸真进了物理链（`render(print_profile=…)`）',
  /def render\(lin, stock_name, cfg=C, print_exposure=None, print_profile=None\):/.test(spekSrc) &&
    /print_profile=_paper/.test(pipeSrc), '',
  '换纸没进渲染 ⇒ 下拉是个纯装饰');
check('★★ 段缓存键**带相纸**（不带 ⇒ 换了纸还是上一张的画面，而且看不出来）',
  /_ckey = \('film', _sample_uid\(s\), \(st or \{\}\)\.get\('name'\), _paper or '',/.test(pipeSrc), '',
  '缓存键不带纸 ⇒ 换纸后出图命中上一张的缓存，「这张纸没效果」的假象');
check('★ 引擎 /papers 路由按卷给默认纸',
  /u\.path == '\/papers'/.test(svcSrc) && /spektra\.papers\(q\.get\('stock'\) or None\)/.test(svcSrc), '');

/* 四边同步 */
check('★ main.js 有 engine-papers 通道，且渲染请求真带上了 paper',
  /ipcMain\.handle\('engine-papers'/.test(mainJsSrc) &&
    /'&paper=' \+ encodeURIComponent\(o\.paper \|\| ''\)/.test(mainJsSrc), '',
  '主进程没转发 ⇒ 前端问"这一卷配哪张纸"永远拿到空表');
check('★ preload 暴露了 enginePapers（只传卷名 —— 默认纸跟着卷走）',
  /enginePapers: \(stock\) => ipcRenderer\.invoke\('engine-papers', stock\)/.test(preload), '',
  'preload 少一边 ⇒ 调用同步抛 TypeError，`.catch` 接不到（漏 setConfig 那次的翻版）');
check('★ api/index.ts 三件都齐：Paper 类型 + Window.api 声明 + API 封装',
  /* ⚠ `Window.api` 里那条声明是**换行写的**（`enginePapers: (\n stock: string\n) => …`）
     ⇒ 锚点必须放过空白，别写死成一整行。 */
  /export interface Paper \{/.test(apiTs) &&
    /enginePapers:\s*\(\s*stock: string\s*\)\s*=>\s*Promise</.test(apiTs) &&
    /enginePapers: \(stock: string\) => api\(\)\.enginePapers\(stock\)/.test(apiTs), '');
check('★★ 渲染 opts 里必须带 `paper`（少这一行 ⇒ 界面选了纸、出图仍是旧纸）',
  /paper: grade\.paper/.test(viewerCode), '',
  '下拉是装饰：画面不变，用户会以为"这张纸没效果"（其实是根本没发出去）');
check('★ 换卷要**连坐换纸**（配套纸跟着卷走，不留"Ektar 卷 + Portra 纸"这种组合）',
  /loadPapers: async \(stock\) =>/.test(storeCode) && /get\(\)\s*\.loadPapers\(st\)/.test(storeCode), '');
check('★ 相纸初值由引擎给（前端不许写死纸名；store 与右栏里一个 kodak_/fujifilm_ 都不许有）',
  !/kodak_|fujifilm_/.test(storeCode) && !/kodak_|fujifilm_/.test(gpCode), '',
  '写死纸名 ⇒ 引擎改表就静默错位（和基准那条是同一个坑）');
check('★ 相纸下拉有可断言的"现在用的是哪张"（不去认 option 的 selected）',
  /data-paper-on=/.test(gradeSrc) && /data-paper-n=/.test(gradeSrc), '',
  '没有标记 ⇒ 布局自检只能数"option 有几个"，测不出默认纸对不对');
/* ★★ 这一条和上一条（mock 对中性卷返回空表）是**一对**，必须两条都在：
   防"中性卷不该有相纸"这件事被**两条路径互相兜住** ——
     ① 引擎/mock 对中性卷返回空表（数据侧）
     ② 右栏按"是不是真卷"决定这一栏画不画（表现侧）
   只写一条的话，另一条被破坏时**检查照样绿**（本项目的老坑：破法要一起破）。
   两条各自单边可破 ⇒ 任何一边歪掉都会当场红。 */
check('★ 真卷之外那一栏**整个不显示**（不是列一队拧不动的纸）',
  /\{isSpek && papers\.length > 0 && \(/.test(gradeSrc), '',
  '中性卷下也画一个相纸下拉 ⇒ 用户在那儿点半天没反应（相纸只对真卷有意义）');

/* ★★ 跨语言钉子：布局自检的 mock 跑的是**它自己那份假数据**，
   必须和引擎的相纸名单一模一样。名字漂了 ⇒ 布局自检在测一份不存在的纸（绿着但没意义）。 */
const mq = mockSrcFlat.indexOf('enginePapers: async');
const mz = mockSrcFlat.indexOf('getGrade: async');
const mockPaperBlock = mq >= 0 && mz > mq ? mockSrcFlat.slice(mq, mz) : '';
const mockPaperNames = [...mockPaperBlock.matchAll(/\['([a-z0-9_]+)',\s*'/g)].map((m) => m[1]);
check('★★ 布局自检 mock 的相纸名单照引擎 `spektra.PAPERS` 抄（张数与名字一个不差）',
  paperNames.length >= 6 && mockPaperNames.length === paperNames.length &&
    mockPaperNames.every((n) => paperNames.includes(n)),
  `引擎 ${paperNames.length} 张 / mock ${mockPaperNames.length} 张`,
  'mock 和引擎漂了 ⇒ 真浏览器那组检查在测一份不存在的纸');
check('★ 布局自检 mock 对**中性卷**返回空表（照引擎抄）',
  /const own = OWN\[stock\];\s*if \(!own\) return \{ ok: true, items: \[\] \};/.test(mockPaperBlock), '',
  'mock 对中性卷也给 8 张 ⇒ 黑不了"中性卷不该有相纸"这条');
check('★ 布局自检 mock 记下了渲染请求里的 paper（否则"换纸真发出去了吗"测不到）',
  /paper: opts && opts\.paper/.test(mockSrcFlat), '');

/* ---------- 13. 加入目录（库外目录：原地读，不复制） ----------
   ★ 为什么单开一组：SV 原话 —— *"如果一张照片已经在我的电脑中，我可以通过加这个目录
     让这个目录[出现在]图片库那一栏中"*。要点是**原地读**：照片已经在硬盘上，
     不再复制一份进库。和「导入照片」（复制 + 按「日期_主题_地点」建档）是**两件事**。
   ★ 这一组盯三件"接了半截就静默坏"的事：
     ① **路径**：库外目录**不在** `libRoot` 底下 ⇒ `enterSession` 必须用列表给的真实路径。
        自己拼 `库根\名字` 会拼出一个不存在的目录 ⇒ 进去 0 张照片，
        而且看着像"这个主题是空的"（本项目最像"点了没反应"的一类假象）。
     ② **身份**：星级 / 成片归档 / 调色配方都按**主题名**当键 ⇒ 库外条目要是和库内主题重名，
        两边会串味。所以重名时必须给**库外**那条改名（库内的名字一个字符都不许动，
        它是那人几周星级的身份键）。
     ③ **不静默消失**：目录被删 / 改名 ⇒ 列一条「找不到」且点不进去，
        **不能**从列表里悄悄没了（那是最难查的一类"看着对、其实对不上"）。 */
console.log('\n[13] 加入目录（库外目录：原地读，不复制）');
const paneSrc = read('src/components/SessionPane.tsx');
/* 只取 `extraSessions` 的函数体（下一个函数名当右界）—— 判"这段有没有写盘"用 */
const exA = mainJsSrc.indexOf('function extraSessions(used)');
const exB = mainJsSrc.indexOf('function scanSessions(libRoot)');
const exBody = exA >= 0 && exB > exA ? mainJsSrc.slice(exA, exB) : '';

check('★ 配置里有 `extraRoots`（加过的库外目录，存绝对路径）',
  /extraRoots:\s*\[\]/.test(mainJsSrc), '',
  '没有这个键 ⇒ 加过的目录下次开台子就没了（"加了个寂寞"）');
check('★ 主题列表把库外目录一起列出来（`scanSessions` 真的**调**了 `extraSessions`）',
  /function scanSessions\(libRoot\)/.test(mainJsSrc) &&
    /* ⚠ 锚点必须是**调用点**：函数定义 `function extraSessions(used) {` 里
       也含 `extraSessions(used)` ⇒ 拿它当锚点的话，删掉调用点这检查照样绿。
       （09-15 就是破法 61 把它抓出来的 —— 典型的"只查标题在不在"。） */
    /for \(const s of extraSessions\(used\)\) out\.push\(s\);/.test(mainJsSrc), '',
  '只扫 libRoot ⇒ 加进来的目录永远不出现');
check('★ 库外目录的表从**配置**读（不是前端再传一份）',
  /roots = loadConfig\(\)\.extraRoots;/.test(mainJsSrc), '',
  '两处各存一份 ⇒ 一旦不一致就出现"列表里有、重扫又没了"');
check('★ 扫库外目录是**纯只读**（只 statSync / readdirSync）',
  exBody.length > 200 && !/rmSync|unlinkSync|writeFileSync|copyFileSync|renameSync|mkdtemp/.test(exBody), '',
  '"看看有什么"的动作动了用户的片子 —— 这是最不可原谅的一类');
check('★★ 库外目录不在了 ⇒ 列一条 `missing`，**不静默跳过**',
  /missing: true/.test(mainJsSrc) && /missing\?: boolean/.test(apiTs), '',
  '静默跳过 ⇒ 用户加过的目录自己消失了，还查不出为什么');
check('★ 库外条目带 `path` + `external` + `rootDir`（进目录 / 画徽标 / 移除都靠它们）',
  /external: true, rootDir: dir/.test(mainJsSrc) && /external\?: boolean;/.test(apiTs) &&
    /rootDir\?: string;/.test(apiTs), '');
check('★ 库内主题也带 `path`（这样前端两处走同一条路，不用分叉）',
  /out\.push\(Object\.assign\(\{ name: e\.name, path: full \}, info\)\)/.test(mainJsSrc), '');
check('★★ 库内主题的名字**原样保留**（重名只改库外那条 —— 库内名字是星级的身份键）',
  /used\.add\(e\.name\.toLowerCase\(\)\)/.test(mainJsSrc) &&
    /function uniqueName\(used, base\)/.test(mainJsSrc), '',
  '给库内主题也改名 ⇒ 那个人几周的星级 / 配方全丢');
check('★★★ `enterSession` 用**条目给的真实路径**，不再自己拼 `库根\\名字`',
  /const sessionPath = \(s && s\.path\) \|\| \(root \? root \+ '\\\\' \+ name : ''\);/.test(storeCode), '',
  '拼路径 ⇒ 库外目录进去永远 0 张照片（看着像空主题，看不出是路径拼错）');
check('★ 加入 / 移除两个动作都在 store 里，四边齐（接口 + 实现）',
  /addExtraRootDir: \(dir: string\) => Promise<void>;/.test(storeCode) &&
    /removeExtraRootDir: \(dir: string\) => Promise<void>;/.test(storeCode) &&
    /addExtraRootDir: async \(dir\) =>/.test(storeCode) &&
    /removeExtraRootDir: async \(dir\) =>/.test(storeCode), '',
  '接口 declaration 和实现少一边 ⇒ 要么编译不过，要么这个动作根本调不到');
check('★ 加入 / 移除都落到 `config.extraRoots`（同一个键，不各存各的）',
  /API\.setConfig\(\{ extraRoots: \[\.\.\.list, d\] \}\)/.test(storeCode) &&
    /API\.setConfig\(\{ extraRoots: list\.filter\(/.test(storeCode), '');
check('★ 已经在图库里的目录**拒绝重复加入**（库根 / 库根的直接子目录）',
  /已经在图库列表里了/.test(storeCode) && /D === L \|\| D\.replace\(/.test(storeCode), '',
  '不挡 ⇒ 同一个目录列两条（还得靠改名区分），看着像出了 bug');
check('★ 移除**不删任何文件**（store 里那段没有任何删除 API）',
  !/rmSync|unlinkSync|shutil|removeDir/.test(storeCode), '',
  '删文件是"不可逆"，而且是这个动作**明确承诺过不做**的事');
check('★ 正在看的就是被移除的那个根 ⇒ 回首页（不留一个点不动的主题）',
  /removeExtraRootDir: async \(dir\) => \{[\s\S]{0,1200}?get\(\)\.goHome\(\);/.test(storeCode), '');
check('★ 左栏有「加入目录」入口，而且真接上了动作（不是个死按钮）',
  /data-add-dir/.test(paneSrc) && /加入目录/.test(paneSrc) &&
    /* ⚠ 两件事都要在：① 按钮真的挂了 onClick ② 那个函数真的去写配置。
       只查其中一样 ⇒ "按钮在、点了没反应"和"函数写好了、没人调"两种死法各漏一种。 */
    /onClick=\{addDir\}/.test(paneSrc) &&
    /const addDir = async \(\) => \{[\s\S]{0,140}?addExtraRoot\(p\)/.test(paneSrc), '',
  '没有入口 ⇒ 功能等于不存在；接了半截（按钮在、没 onClick）⇒ 点了什么都不发生，' +
    '用户以为"加了但没生效"（"导入入口点不到"那次的翻版）');
check('★ 库外条目有「×」，且 title 明说**不删文件**（用户看到 × 第一反应是怕删文件）',
  /data-session-del=/.test(paneSrc) &&
    /* ⚠ 锚点必须取**那段 title 字面量**：文件顶部的说明注释里也有「不删任何文件」几个字，
       拿它当锚点会被自己的注释骗（09-15 破法 66 抓到的就是这条）。 */
    /'从列表移除（不删任何文件）\\n'/.test(paneSrc), '',
  '没说清 ⇒ 用户不敢点，或者点了以为片子被删了');
check('★ 库外条目有可断言的标记（布局自检靠它认，不去认颜色）',
  /data-session-ext=/.test(paneSrc) && /data-session-badge/.test(paneSrc) &&
    /data-session-gone=/.test(paneSrc), '');
/* ★★ 跨层钉子：布局自检的 mock 必须**照生产端**给这两样，
   否则真浏览器那组在测一份不存在的形状（"mock 跟着前端一起错"的老坑）。 */
check('★★ 布局自检 mock 的 `getConfig` 带 `extraRoots`（照生产端抄）',
  /getConfig: async \(\) => \(\{ libRoot: 'D:\\\\lib', extraRoots: window\.__extraRoots \|\| \[\] \}\)/.test(mockSrcFlat), '',
  'mock 不给 ⇒ 「加入目录」那条链在自检里根本没有数据可走');
check('★★ 布局自检 mock 的 `scanSessions` 把库外目录并排列出来（带 path/external/rootDir）',
  /scanSessions: async \(\) => \{/.test(mockSrcFlat) && /external: true,/.test(mockSrcFlat) &&
    /const ex = window\.__extraRoots \|\| \[\];/.test(mockSrcFlat), '',
  'mock 只返回库内主题 ⇒ 「点库外条目用的是真实路径」这条根本测不到');
check('⚠ 布局自检 mock 必须实现 `pickDirectory`（左栏「换图库 / 加入目录」都会调它）',
  /pickDirectory: async \(\) =>/.test(mockSrcFlat), '',
  '漏了它 ⇒ 点那一刻同步抛 TypeError（漏 setConfig 那次的翻版）');

/* ---------- [14] 库外·纯 RAW 目录 ⇒ 预览小图（"只按 JPG 列图"的补救） ----------
   ★★ 背景：工作台列图**只按 JPG 列**（`IMG_EXT`），RAW 只当"这张有 RAF"的角标
      ⇒ 把"只拷了 RAF"的文件夹「加入目录」进来，界面上是**空的**
      —— 看着像"这个目录里没东西"，其实是"它一张 JPG 都没有"。
      修法：给它自动生成一份预览索引（抠相机自带的机内 JPG，缩到长边 1600，写进应用缓存）。
   ⚠ 这一组**不是**拿正则看"有没有写某个函数名"（那挡不住"函数体写错"，等于没查）：
      ① `parseExtIndexOutput` 真跑（拿脚本真实格式的样例行）
      ② `extIndexDirFor` 真跑（自带 fs/path/crypto + 假的 app）
      ③ 剩下的是接线钉子：源目录只读 / 缓存不写死本机路径 / 传的是源目录不是缓存目录 */
console.log('\n[14] 库外·纯 RAW 目录 ⇒ 预览小图');
const pyPath = 'tools/make_jpg_index.py';
const pySrc = exists(pyPath) ? read(pyPath) : '';
check('★ 索引脚本在仓库里（`tools/make_jpg_index.py`）—— 不用 SV 去配路径', !!pySrc, pyPath,
  '脚本不在 ⇒ 这个功能整条链是死的，而且只会在"点了没反应"的时候才被发现');

/* ---- ① 源目录必须**一个字节都不动**（这是它和「导入照片」共用的一条底线） ---- */
if (pySrc) {
  const forbidden = ['os.remove', 'os.rmdir', 'os.unlink', 'os.rename', 'shutil', 'rmtree'];
  const hits = forbidden.filter((b) => pySrc.includes(b));
  check('★★ 索引脚本里没有任何"删 / 移文件"的调用（源目录只读）', hits.length === 0,
    hits.join(', '),
    `出现了 ${hits.join(', ')} ⇒ 哪天 bug 一动手，他的照片就没了（这个脚本只该读源目录）`);
  check('★ 写盘只往 `--out` 里写（先写临时名再原子换名，中途被杀不留半张坏图）',
    /os\.path\.join\(out, /.test(pySrc) && /os\.replace\(tmp, out_path\)/.test(pySrc), '',
    '直接往目标名写 ⇒ 中途杀掉会留下半张坏图，而工作台照样把它当一张照片列出来');
  check('★ 每个索引目录写一份 `_src.txt`（出图源的凭据，缺了就会拿缩略图当原图渲染）',
    /'_src\.txt'/.test(pySrc), '',
    '不写 ⇒ 引擎从 1600 的预览小图渲染：能出图、不报错，画质悄悄掉了');
  check('★ 抠的是**相机自带的机内 JPG**（3~4 ms/张），不是自己解码整张 RAW',
    /extract_thumb\(\)/.test(pySrc), '',
    '自己解码一张 4400 万像素要好几秒 ⇒ 一个目录要跑到天亮');
}

/* ---- ② 结果行两边对得上（脚本打的那行 ↔ main.js 解析的那行） ---- */
const ksM = pySrc.match(/^KS = '([A-Z_]+)'/m);
check('★ 脚本的结果行常量跟 main.js 解析的那个名字是**同一个**',
  !!ksM && mainJsCode.includes(ksM[1]), ksM ? ksM[1] : '(取不到)',
  '两边名字不一致 ⇒ 结果永远解析不出来，看着像"转完了但没结果"');
const peM = mainJsCode.match(/function parseExtIndexOutput\(text\)\s*\{[\s\S]*?\n\}/);
let pe = null;
if (peM) {
  try { pe = new Function(peM[0] + '; return parseExtIndexOutput;')(); } catch (e) { pe = null; }
}
check('parseExtIndexOutput 能在 Node 里独立跑起来', typeof pe === 'function', '', '取出来那段跑不了');
if (typeof pe === 'function') {
  const sample = (ksM ? ksM[1] : 'KS_EXT_INDEX_OK') + ' ' +
    JSON.stringify({ src: 'D:\\a', out: 'C:\\b', n: 3, skip: 1, fail: 0, bytes: 900 });
  const r = pe(sample);
  check('★ 结果行真解析得出来（n / skip 都在）',
    r.ok === true && r.n === 3 && r.skip === 1, JSON.stringify(r),
    `解析不出来：${JSON.stringify(r)}`);
  const bad2 = pe('  [错误] 建不了索引目录\n');
  check('★★ 没有结果行 ⇒ `ok:false` **并且给了原因**（不静默）',
    bad2.ok === false && /结果行/.test(bad2.error || ''), String(bad2.error),
    '失败也说 ok ⇒ 界面以为转完了，那个目录永远空着');
}

/* ---- ③ extIndexDirFor 真跑（幂等 / 不撞名 / 落在缓存里） ---- */
const eirM = mainJsCode.match(/function extIndexRoot\(\)\s*\{[\s\S]*?\n\}/);
const eidM = mainJsCode.match(/function extIndexDirFor\(srcDir\)\s*\{[\s\S]*?\n\}/);
/* ⚠ `extIndexRoot()` 用的是一个**模块级**的缓存变量（`let _extIndexRoot = null;`，
   惰性建目录用）。漏了它 ⇒ 抽出来一跑就 `ReferenceError: _extIndexRoot is not defined`。 */
const eivM = mainJsCode.match(/let _extIndexRoot = null;/);
check('main.js 里有 extIndexRoot / extIndexDirFor（缓存根 + 目录映射）',
  !!eirM && !!eidM && !!eivM, '', '函数没了？');
check('★★ 缓存根走 `app.getPath(\'userData\')`，仓库里**不写死本机路径**（要开源）',
  !!eirM && /path\.join\(app\.getPath\('userData'\), 'extpreview'\)/.test(eirM[0]) &&
    !/[A-Za-z]:\\/.test(eirM[0]),
  '', '写死路径 ⇒ 换机器 / 开源之后这套缓存全落在别人没有的盘上');
if (eirM && eidM) {
  const fakeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'svstudio-eidx-'));
  let ed = null;
  try {
    ed = new Function(
      'fs', 'path', 'crypto', 'app',
      eivM[0] + '\n' + eirM[0] + '\n' + eidM[0] +
        '\n; return { extIndexRoot: extIndexRoot, extIndexDirFor: extIndexDirFor };'
    )(fs, path, crypto, { getPath: () => fakeRoot });
  } catch (e) { ed = null; }
  check('extIndexDirFor 能在 Node 里独立跑起来', !!ed, '', '取出来那段跑不了');
  if (ed) {
    const a = ed.extIndexDirFor('D:\\照片\\某次拍摄');
    const b = ed.extIndexDirFor('D:\\照片\\某次拍摄\\');
    const c = ed.extIndexDirFor('D:\\照片\\另一个目录');
    check('★ 索引目录落在**应用缓存**里（既不在源目录、也不在他的照片盘）',
      path.resolve(a).startsWith(path.resolve(fakeRoot)), a,
      `${a} 不在缓存根下 ⇒ 会往他的照片目录里塞缓存小图`);
    check('★ 目录名里带源目录名（出问题时一眼看得出对的是谁）',
      path.basename(a).startsWith('某次拍摄@'), path.basename(a), path.basename(a));
    check('★ 同一个目录永远算到同一个索引目录（幂等；尾斜杠不影响）', a === b, `${a} vs ${b}`,
      '不稳定 ⇒ 每次算出来都是新目录，缓存永远命中不了，每次都得重转一遍');
    check('★ 两个不同目录不会撞到同一个索引目录', a !== c, `${a} vs ${c}`,
      '撞名 ⇒ 两个目录互相覆盖对方的小图，还各以为自己是对的');
  }
  try { fs.rmSync(fakeRoot, { recursive: true, force: true }); } catch (e) { /* 留着也无害 */ }
}

/* ---- ③b extraSessions 真跑：搭一棵**真目录树**喂它（不是拿假数据假装） ----
   ★ 这条是这一组里最值钱的一条，因为踩过一次：`D:\照片库\爆光修复` 根上是 4 张 RAF、
     底下 `x100vi` 还有 6 张 JPG。改成"纯 RAW 的根也算照片目录"之后，
     根**一条封顶**就把 `x100vi` 那 6 张从列表里挤掉了 —— 而界面上没有任何迹象
     （列表里少一条而已）。这里把这棵树真搭出来跑一遍，一条都不能少。 */
{
  const a2 = mainJsCode.indexOf('function describeSessionDir(');
  const b2 = mainJsCode.indexOf('function listPhotos(');
  const cRe = [
    /^const IMG_EXT = .*$/m, /^const RAW_EXT = .*$/m,
    /^const STAR1_DIR = .*$/m, /^const STAR2_DIR = .*$/m,
    /^const PUBLISH_DIR = .*$/m, /^const REVIEW_DIR = .*$/m,
    /^const ARCHIVED_SUBDIRS = .*$/m, /^const THEME_SUBDIRS = .*$/m,
  ];
  const cSrc = cRe.map((r) => (mainJsCode.match(r) || [''])[0]).join('\n');
  check('抽到了 extraSessions 那一段需要的常量（自检里不许另写一份）',
    cRe.every((r) => r.test(mainJsCode)) && a2 > 0 && b2 > a2, '', 'main.js 结构变了？');
  if (a2 > 0 && b2 > a2 && cRe.every((r) => r.test(mainJsCode))) {
    const region = mainJsCode.slice(a2, b2);
    const treeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'svstudio-exts-'));
    const idxRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'svstudio-extidx-'));
    const theRoot = path.join(treeRoot, '某次拍摄');
    fs.mkdirSync(path.join(theRoot, 'x100vi'), { recursive: true });
    fs.writeFileSync(path.join(theRoot, 'A.RAF'), '');            // 根上只有 RAW
    fs.writeFileSync(path.join(theRoot, 'x100vi', 'B.JPG'), ''); // 子目录里是正常 JPG
    let fx = null;
    try {
      fx = new Function('fs', 'path', 'loadConfig', 'crypto', 'app',
        cSrc + '\n' + region + '\n; return { extraSessions: extraSessions };'
      )(fs, path, () => ({ extraRoots: [theRoot] }), crypto, { getPath: () => idxRoot });
    } catch (e) { fx = null; }
    check('extraSessions 能在 Node 里对**真目录树**跑起来', !!fx, '', '取出来那段跑不了');
    if (fx) {
      const list = fx.extraSessions(new Set());
      const names = list.map((x) => x.name);
      check('★★ 根上"只有 RAW"、子目录里有 JPG ⇒ **两条都要列出来**（根不能一条封顶）',
        list.length === 2 && names.some((n) => /x100vi/.test(n)) && names.some((n) => /某次拍摄/.test(n)),
        JSON.stringify(names),
        `只列出 ${JSON.stringify(names)} ⇒ 根上那几张 RAF 把下面正常主题挤掉了，` +
          '而且界面上没有任何迹象');
      const ex = list.find((x) => x.name.indexOf('x100vi') >= 0);
      check('★ 子目录那条读的是**它自己的真路径**（不是库根拼出来的）',
        ex && ex.path === path.join(theRoot, 'x100vi'), ex && ex.path, String(ex && ex.path));
      const ro = list.find((x) => x.rawOnly);
      check('★★ 纯 RAW 那条：`path` 指向**预览缓存**、`srcDir` 是真身、并标记"待建索引"',
        ro && ro.path.startsWith(path.resolve(idxRoot)) && ro.srcDir === theRoot &&
          ro.needsIndex === true && ro.count === 1,
        ro ? JSON.stringify({ path: ro.path, srcDir: ro.srcDir, needsIndex: ro.needsIndex, count: ro.count }) : '(没有这条)',
        '这三样少一个 ⇒ 要么列不出图（path 指源目录），要么拿缩略图渲染（srcDir 丢了）');
      /* ★ 幂等：同一个源目录算两次 ⇒ 同一条缓存目录（不重复占地方） */
      const list2 = fx.extraSessions(new Set());
      const idx2 = list2.find((x) => x.rawOnly);
      check('★ 同一个目录两次扫出来的索引目录是**同一个**（缓存不会越攒越多）',
        idx2 && ro && idx2.indexDir === ro.indexDir, idx2 && idx2.indexDir, '');
    }
    try { fs.rmSync(treeRoot, { recursive: true, force: true }); } catch (e) { /* ok */ }
    try { fs.rmSync(idxRoot, { recursive: true, force: true }); } catch (e) { /* ok */ }
  }
}

/* ---- ④ 接线钉子（这一段的坑都在"两边传的不是同一个东西"上） ---- */
check('★★★ 建索引传的是**源目录**（`s.srcDir`），不是预览缓存目录（`s.path`）',
  /indexExternalDir\(s\.srcDir\)/.test(storeSrc) && !/indexExternalDir\(s\.path\)/.test(storeSrc),
  '', '传缓存目录 ⇒ 那儿一张 RAW 都没有，扫出来永远是空的，而界面一点错都不报');
check('★ 进主题时「索引还没建」就先补上（否则列表显示 4 张、点进去 0 张）',
  /if \(s && s\.needsIndex && s\.srcDir\)/.test(storeSrc) &&
    /await get\(\)\.indexExternalDir\(s\.srcDir\);/.test(storeSrc), '',
  '不先补 ⇒ 列表和里面张数对不上，用户以为片子丢了');
check('★★ 建不了的时候要**说出原因**（否则那目录永远是空的，而"空"和"坏了"长得一样）',
  /* ⚠ 两条失败路径**都要出声**：① 脚本跑了但说"没成功" ② 起进程/调用本身抛了。
     只钉一条的话，另一条被删掉照样绿（"只查标题在不在"的翻版）。 */
  /'预览小图没生成：' \+ \(r\?\.error/.test(storeSrc) &&
    /'预览小图没生成：' \+ String\(e\)/.test(storeSrc), '',
  '只 catch 不报 ⇒ 用户看到的就是一个空主题');
check('★ main.js 暴露了 `ext-index` 通道 + `ext-index-progress` 事件',
  /ipcMain\.handle\('ext-index'/.test(mainJsCode) && /ext-index-progress/.test(mainJsCode), '',
  '没有通道 ⇒ 前端调下去同步抛 TypeError（"漏 setConfig"那次的翻版）');
check('★ 建索引的进度接上了（推事件给界面，不是等返回值）',
  /onExtIndexProgress/.test(preloadSrc) && /onExtIndexProgress/.test(apiTs) &&
    /API\.onExtIndexProgress/.test(appTsx), '',
  '进度接不上 ⇒ 几百张要几十秒，界面全程像死机');
check('★ 没生成索引时，条目上写明「预览待生成」', /预览待生成/.test(paneSrc), '',
  '不写 ⇒ 用户点进去看到空的，只能猜是目录空了还是台子坏了');
check('★ 缓存目录里放一份「这是什么、可以删」的说明', /_说明\.txt/.test(mainJsCode), '',
  '缓存是往用户机器上写东西，得让他知道那是什么、能不能删');
const ssM = mainJsCode.match(/function scanSessions\(libRoot\)\s*\{[\s\S]*?\n\}/);
check('★ 库内主题**不开**这个开关（突然冒出一批"只有 RAF"的主题会打乱他现有的列表）',
  !!ssM && /describeSessionDir\(full\)/.test(ssM[0]) && !/allowRawOnly/.test(ssM[0]),
  '', '给库内也开 ⇒ 列表里会凭空多出几批只有 RAW 的条目，那是我替他做的决定');
check('⚠ 布局自检 mock 必须实现 `extIndex` + `onExtIndexProgress`（「加入目录」会调）',
  /extIndex: async \(srcDir\) =>/.test(mockSrcFlat) &&
    /onExtIndexProgress: \(cb\) =>/.test(mockSrcFlat), '',
  '漏了它 ⇒ 点那一刻同步抛 TypeError，而"到底拿哪个目录去建索引"永远测不到');
check('★★ mock 的 `scanSessions` 要能给出「纯 RAW 库外目录」（path→缓存、srcDir→真身）',
  /e\.rawOnly = true;/.test(mockSrcFlat) && /e\.srcDir = d;/.test(mockSrcFlat), '',
  'mock 不给这种条目 ⇒ 真浏览器那组整段是空转');

/* ---------- [15] 界面构建是否最新（"看不到新功能"的那个坑） ----------
   ★★ 09-15 SV 报：左栏**看不到**新做的「加入目录」，其实代码当天下午就写好了。
      原因：`renderer/dist` 还是上一次构建的产物，而 `src/` 后来改过 ⇒
      台子照常开、照常能用、**一点报错都没有**，只是画的是旧界面。
   ⇒ 修法是开窗前先看一眼，旧的就重建。这一组查两件事：
      ① 判定逻辑**拿真目录树真跑**（"改了 src 认不认得出"光看正则看不出来）；
      ② 接线（必须在开窗**之前**、必须吞异常、不能假设 PATH 上有 node）。 */
console.log('\n[15] 界面构建是否最新（"看不到新功能"的那个坑）');
{
  const nurM = mainJsSrc.match(/function needsUiRebuild\(root\)\s*\{[\s\S]*?\n\}/);
  let nur = null;
  let nurErr = '';
  if (nurM) {
    try {
      nur = new Function('fs', 'path', nurM[0] + '\nreturn needsUiRebuild;')(fs, path);
    } catch (e) {
      nurErr = String(e);
    }
  }
  check('★★ 判定逻辑能独立跑起来（把 `needsUiRebuild` 抽出来真调用）',
    typeof nur === 'function', '', nurErr || '抽不出来 ⇒ 下面几条全测不到');

  if (typeof nur === 'function') {
    const troot = fs.mkdtempSync(path.join(os.tmpdir(), 'svstudio-stale-'));
    const T = Date.parse('2026-09-15T02:00:00Z');
    const mk = (rel, ms) => {
      const p = path.join(troot, rel);
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, 'x');
      const t = new Date(ms);
      fs.utimesSync(p, t, t);
    };
    /* 初始：代码 10:00、产物 10:01 ⇒ 界面是新的 */
    mk('src/components/a.tsx', T);
    mk('vite.config.ts', T - 1000);
    mk('renderer/index.html', T - 1000);
    mk('renderer/dist/index.js', T + 1000);

    check('★ 界面比代码新 ⇒ 不重建（正常启动一秒都不多花）',
      nur(troot) === false, '', '每次都重建 ⇒ 白等 2 秒，还会把刚编译好的产物反复覆盖');

    /* ⚠ 改的是 src **子目录**里的文件 —— 只看 src 根目录的话，绝大多数改动都漏掉 */
    mk('src/components/a.tsx', T + 5000);
    check('★★ 改了 src **子目录**里的文件 ⇒ 认得出要重建',
      nur(troot) === true, '', '只看 src 根 ⇒ 十次改动里有十次漏掉（组件全在子目录）');

    mk('src/components/a.tsx', T);
    mk('vite.config.ts', T + 6000);
    check('★ 只改了 `vite.config.ts` ⇒ 也要重建（它决定产物长什么样）',
      nur(troot) === true, '', '漏掉它 ⇒ 改了打包配置却一直用旧产物，最像"改了没用"');

    fs.rmSync(path.join(troot, 'renderer', 'dist', 'index.js'));
    check('★ 从来没构建过（没有 dist）⇒ 要重建',
      nur(troot) === true, '', '返回 false ⇒ 台子开了是**白屏**，且没有任何提示');

    fs.rmSync(path.join(troot, 'src'), { recursive: true, force: true });
    check('★ 打包版（没有 `src/`）⇒ 什么都不做',
      nur(troot) === false, '', '成品里也去找源码 ⇒ 行为不可预期');

    fs.rmSync(troot, { recursive: true, force: true });
  }

  const rbiM = mainJsSrc.match(/function rebuildUiIfStale\(\)\s*\{[\s\S]*?\n\}/);
  check('★★ 这一步在**开窗之前**跑（重建必须赶在窗口读 index.html 之前做完）',
    /app\.whenReady\(\)\.then\(\(\) => \{[\s\S]{0,200}?rebuildUiIfStale\(\);[\s\S]{0,80}?createWindow\(\);/.test(mainJsSrc),
    '', '放在 createWindow 之后 ⇒ 这一次开的还是旧界面，这功能等于没做');
  check('★★ 重建出任何问题都**不许挡住开台子**（异常要吞掉）',
    !!rbiM && /catch \(e\) \{[\s\S]{0,240}?(logUiRebuild|console\.log)/.test(rbiM[0]), '',
    '异常抛出去 ⇒ 台子直接打不开；为了省 2 秒把工具搞崩，不值');
  check('★★ 重建的日志**不继承控制台**（双击启动那个黑窗口 3 秒后就关了）',
    !!rbiM && /stdio: \['ignore', 'pipe', 'pipe'\]/.test(rbiM[0]) &&
      !/stdio: 'inherit'/.test(rbiM[0]), '',
    '用 inherit ⇒ 控制台先关掉、vite 往已关的句柄写 ⇒ 重建白做，而表现又是「界面没变」');
  check('★ 重建的结果写进日志（界面里看不到它，出问题得有个地方查）',
    /function logUiRebuild\(/.test(mainJsSrc) && /svstudio_rebuild\.log/.test(mainJsSrc), '',
    '不写日志 ⇒ 重建失败谁都不知道，症状还是「界面没变」，等于白做');
  check('★ 不假设 PATH 上有 node（本机持久 PATH 里**没有**）—— 拿 electron 自己当 node 跑',
    !!rbiM && /process\.execPath/.test(rbiM[0]) && /ELECTRON_RUN_AS_NODE/.test(rbiM[0]), '',
    '写成调 `node`/`vite` 命令 ⇒ 双击启动那个环境里根本找不到，静默不重建（白做）');
  check('★ 这一段的代码里不写死本机路径（要开源）',
    !!nurM && !/[A-Za-z]:\\/.test(nurM[0]) && !!rbiM && !/[A-Za-z]:\\/.test(rbiM[0]), '');
}

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
