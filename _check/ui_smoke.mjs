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
import http from 'node:http';
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

/* ---------- ★★★ 09-15（SV 报「柔光下拉框为空」）：`opts` 的形状三边必须一致 ----------
   病理：引擎 `PARAMS` 里 `opts=(('black_pro_mist','黑柔（BPM）'), …)` 是**紧凑元组**，
   过一遍 HTTP（JSON）就变成**数组对** `[['black_pro_mist','黑柔（BPM）'], …]`，
   而前端读的是 `o.v` / `o.t` ⇒ 四个 `<option>` 的 value 和文字**全是 undefined**
   ⇒ 下拉框**看着是空的**。而 `<option>` 的**个数**照样是 4 ⇒ 上面那条"四支"的断言绿着。
   ⇒ 契约：**过 HTTP 一律是对象 `{v,t}`**（在 `_param_defs()` 里那一步翻好）。
     三方（引擎发的 / 前端读的 / 布局自检 mock 里的）谁改歪都会被这里抓住。
   ⚠ 引擎那两条要在**剥掉 Python 注释**的源码上查（说明注释里就写着这个形状，会被自己骗）。 */
const svcCode = svcSrc.replace(/#[^\n]*/g, '');
const mockCode = mockSrc.replace(/\/\*[\s\S]*?\*\//g, '');
check('★★ 引擎发出去的 opts 是对象 {v,t}（元组过 JSON 会变成数组对 ⇒ 下拉就空了）',
  /q\['opts'\] = \[\{'v': o\[0\], 't': o\[1\]\} for o in/.test(svcCode), '',
  '`_param_defs()` 里少了"元组 → 对象"那一步 ⇒ 前端收到的还是数组对，下拉框空着');
check('★ 前端读的就是 `o.v` / `o.t`（跟着对象的字段名走，不是元组下标）',
  /o\.v/.test(gradeSrc) && /o\.t/.test(gradeSrc) &&
    !/<option[^>]*key=\{o\[0\]\}/.test(gradeSrc), '',
  '前端按元组下标读 ⇒ 引擎一发对象它就全 undefined（下拉空掉的另一半）');
check('★ 布局自检 mock 里的 opts 也是对象（三方一致，谁漂了都红）',
  /opts: \[\{ v: '[a-z_]+', t: '[^']+' \}/.test(mockCode), '',
  'mock 和引擎不是一个形状 ⇒ 检查绿着、真界面是空的（这个 bug 就是这么溜过去的）');

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
    ps({ A: 1, B: NaN, D: 2 }) === 'A:1,D:2',
    '', String(ps({ A: 1, B: NaN, D: 2 })));
  /* ★★ 09-15（B3）：新放的两种控件（整层开关 / 下拉型号）走的**全是这条函数** ——
     它不放行，勾选框和下拉就是"点了没反应、还不报错"的老 bug 复刻。 */
  check('★ paramStr 放行**布尔**（整层开关）—— 写成 1/0',
    ps({ GRAIN_ENABLE: false, BLOOM_ENABLE: true }) === 'GRAIN_ENABLE:0,BLOOM_ENABLE:1',
    String(ps({ GRAIN_ENABLE: false, BLOOM_ENABLE: true })),
    '布尔被丢掉 ⇒ 勾选框勾了画面一个像素都不动');
  check('★ paramStr 放行**字符串**（柔光型号那种下拉值）',
    ps({ SPEK_DIFFUSION_FAMILY: 'cinebloom' }) === 'SPEK_DIFFUSION_FAMILY:cinebloom',
    String(ps({ SPEK_DIFFUSION_FAMILY: 'cinebloom' })),
    '下拉值被丢掉 ⇒ 选了等于没选');
  check('★ 值里带 `:` / `,` 的字符串仍然丢掉（那个语法靠这两个字符切段）',
    ps({ A: 'x:y' }) === '' && ps({ A: 'x,y' }) === '',
    `${String(ps({ A: 'x:y' }))} / ${String(ps({ A: 'x,y' }))}`,
    '把分隔符放进值里 ⇒ 后面所有参数一起错位');
  check('paramStr 空输入给空串', ps(null) === '' && ps({}) === '',
    '', `${JSON.stringify(ps(null))} / ${JSON.stringify(ps({}))}`);
}
check('★ engine-render 真的走了 paramStr（没被绕过）',
  /&params=' \+ encodeURIComponent\(paramStr\(o\.params\)\)/.test(mainJsCode),
  '', '还在直接 encodeURIComponent(o.params)');
check('engine-render 里没留下"直接编码对象"的老写法',
  !/encodeURIComponent\(o\.params(?!Str)/.test(mainJsCode), '', '老写法还在');

/* ---------- ★★ 09-15 两条链**必须分开**：看图读缓存、出图读原件 ----------
   ① `attachLoadPath()` 算「**喂引擎**时用哪个文件」：同名 RAW 优先，没有才回落到 JPG。
      **为什么这是硬要求**：入口那一段（零点/成形/趾部/高光护栏）**只在 `io.load_raw` 里跑**，
      喂 JPG 的话「整张亮暗(总)」「暗部亮度」这两根滑杆永远是死的（实测同一张 DSCF0546：
      走 RAW 能带动 −18.9 ~ +31.0 个 L*，走 JPG 是 0.00）。
   ② `viewFileOf()` 算「**看**这张图读哪个文件」：桶里的成片读它自己，原图只有 RAW 就读
      预览索引缓存里那份机内 JPG（1600）。
   ★★ 两条分开是硬要求：拿 1600 去渲染＝能出图、不报错、**画质悄悄掉了**；
      拿 RAW 当缩略图＝解不动 / 慢得离谱。
   ⚠ 这一段**不是正则看"有没有写"**，而是把 main.js 里那段源码抽出来在 Node 里真跑。 */
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

  /* ★★★ `viewFileOf`：**看**这张图读哪个文件。09-15 起 `rel` 是**身份键**（`<stem>.JPG`），
     磁盘上**不一定有**这个文件 ⇒ 直接 `path.join(dir, rel)` 会读到一个不存在的东西。
     五条断言：桶里的成片 / 原图只有 RAW / 孤 JPG / 小写扩展名的 RAW / 什么都没有。 */
  const vfM = mainJsCode.match(/function viewFileOf\(dir, rel\)\s*\{[\s\S]*?\n\}/);
  check('main.js 里有 viewFileOf（“看这张图读哪个文件”的唯一出处）', !!vfM, '',
    '函数没了 ⇒ 缩略图/大图/EXIF 又会去拼一个不存在的 <stem>.JPG（全都悄悄变空）');
  const idxOf = (d) => path.join(tmpDir, '_idx', path.basename(d));
  let vf = null;
  if (vfM && rexM) {
    try {
      const RAWE2 = new Function('return ' + rexM[1])();
      vf = new Function('fs', 'path', 'RAW_EXT', 'extIndexDirFor',
        vfM[0] + '; return viewFileOf;')(fs, path, RAWE2, idxOf);
    } catch (e) { vf = null; }
  }
  check('viewFileOf 能在 Node 里独立跑起来（可单测）', typeof vf === 'function', '', '取出来那段跑不了');
  if (typeof vf === 'function') {
    /* tmpDir 里已经有 A.RAF / A.JPG / B.JPG / C.RAF / C.JPG，桶里有 D.JPG。再补两张。 */
    fs.writeFileSync(path.join(tmpDir, 'Lone.JPG'), '');   // 孤 JPG：没有同名 RAW
    fs.writeFileSync(path.join(tmpDir, 'low.raf'), '');    // 小写扩展名的 RAW
    const vBucket = vf(bucket, 'D.JPG');
    check('★ 桶里的成片 ⇒ 读它**自己**（不能拿原图顶替产物）',
      vBucket && vBucket.file === path.join(bucket, 'D.JPG') && vBucket.cache === false,
      JSON.stringify(vBucket), '');
    const vRaw = vf(tmpDir, 'A.JPG');
    check('★★★ 原图只有 RAW ⇒ 读**缓存里那份机内 JPG**（不是拼一个不存在的 <stem>.JPG）',
      vRaw && vRaw.file === path.join(idxOf(tmpDir), 'A.JPG') && vRaw.cache === true &&
        vRaw.raw === path.join(tmpDir, 'A.RAF'),
      JSON.stringify(vRaw),
      '读到源目录里那个不存在的 <stem>.JPG ⇒ 缩略图全空、EXIF 全空，而界面一点不报错');
    const vLone = vf(tmpDir, 'Lone.JPG');
    check('★ 孤 JPG（没有同名 RAW）⇒ 读它自己',
      vLone && vLone.file === path.join(tmpDir, 'Lone.JPG') && vLone.cache === false,
      JSON.stringify(vLone), '');
    const vLow = vf(tmpDir, 'low.JPG');
    check('★ 缓存文件名用 RAW 的 stem **原样大小写**（小写源的缓存别去找大写的）',
      vLow && vLow.file === path.join(idxOf(tmpDir), 'low.JPG'), JSON.stringify(vLow),
      '大写化之后再拼 ⇒ 永远找不到缓存 ⇒ 每一张都要重抠一遍');
    check('★ 既没有那个文件、也没有同名 RAW ⇒ 老实返回 null（不瞎指一个路径）',
      vf(tmpDir, 'Nope.JPG') === null, String(vf(tmpDir, 'Nope.JPG')), '');
  }
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* 留着也无害 */ }
}
check('★ 调色台加载用的是 loadPath（RAW 优先），不是自己拼 rel',
  /const full = p\.loadPath \|\| sessionPath/.test(viewerSrc), '',
  '还在自己拼 rel ⇒ 又会喂 JPG');
check('★ 出图源的标记进的是 note（没污染 img 的 alt）',
  /note=\{p \? \(p\.loadIsRaw/.test(viewerSrc) && /title="调色后"/.test(viewerSrc), '',
  'alt 被改掉的话，布局自检靠 alt 认栏位会一起失效');

const storeCode = storeSrc.replace(/\/\*[\s\S]*?\*\//g, '');
check('★ 左栏**常显**（不挂在 sessionName 上）—— 空库/新库也得点得到「加入目录」',
  !/\{sessionName && <SessionPane/.test(appTsx) && /<SessionPane \/>/.test(appTsx), '',
  '左栏还在条件渲染 ⇒ 新库里所有入口都点不到（而新库最需要它）');
/* ★★ 09-15 SV 定案：「导入照片」（插卡 / U 盘 → 复制进库）**整条删掉**，
   照片已经在硬盘上 ⇒ 只有一条路：把它那个目录「加入目录」进来（原地读、不复制）。
   下面这一组挡三种"半截状态"：①功能没了、按钮还在 ②按钮没了、功能还在 ③组件删了、界面还挂着。 */
const sessTsx = read('src/components/SessionPane.tsx');
check('★ 左栏顶部**只有**「加入目录」这一个按钮',
  /data-add-dir/.test(sessTsx) && !/data-import-open/.test(sessTsx), '',
  '两个入口并存 ⇒ 用户分不清该点哪个（这正是这轮要治的）');
check('★★ 「换图库」和「图库根」那一层**已经删干净**（09-15 SV 选「B」）',
  !/data-change-lib/.test(sessTsx) && !/换图库/.test(sessTsx) &&
    !/data-lib-root/.test(sessTsx), '',
  '留着一个「换图库」按钮 ⇒ 后台已经没有"库根"了 ⇒ 点了没反应（比没有这个按钮更坏）');
check('★ 已删的导入对话框没有残留引用（App / api / store）',
  !/<ImportDialog/.test(appTsx) && !/ImportDialog/.test(apiTs) && !/ImportDialog/.test(storeSrc), '',
  '残留引用 ⇒ tsc / 构建期报错，或运行期白屏');
check('★ 主进程 / preload 里没有导入残留（4 个通道整条清掉）',
  !/import-detect|import-preview|import-run|import-progress/.test(mainJsSrc) &&
    !/importDetect|importPreview|importRun|onImportProgress|pickFile/.test(preload), '',
  '通道删一半 ⇒ preload 调一个不存在的主进程 handler（同步抛错、catch 接不到）');
check('★ 源码里没有写死的个人路径（要开源）',
  !/Users[\\/]+psw99|E:[\\/]+WorkBuddy[\\/]+摄影助手/.test(mainJsSrc + preload + apiTs), '',
  '写死了本机路径 ⇒ 换台机器就跑不了、也没法开源');

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

/* ---------- 11. 大图：一律「适应」+ 视图三档 A / A|B / B（09-15 SV 定） ----------
   ★ 原文：「两图总用自适应**删除其他的** 并且加一个…类似于 LR 中的 `A` `A|B` 的小按钮，
            **默认 A|B** 即对比原片，`B` 则为当前调色效果**铺满**」
   ⇒ 两件事：① 白天做的缩放（滚轮 / 1:1 / 双击 / 百分比徽标）**全删**；
             ② 换成三档"看图姿势"：A = 只看原片 · A|B = 左右对比（默认）· B = 只看调色后。
   ★ 机制在**源码层**钉死，行为（点下去真剩几张图）在布局自检 `[14]` 里量。
   ⚠ 锚点一律取**代码本身**：`FitImage.tsx` 的说明注释里就写着「1:1」「滚轮」这些字，
     拿它们当锚点会被自己的注释骗 ⇒ 下面全部在**剥掉注释**的源码上查
     （09-15 在 api/index.ts 上因为注释栽过一次）。 */
/* ---------- [10.5] 右栏滑杆：每根一个重置 / 参数名字号 ×1.5 / 「?」点开说明 ----------
   ★ 三条都是 09-15 SV 直接点的名（原话）：
     「每个滑杆给个重置按钮 参数名文本字号大1.5倍 不要悬浮文字 改成后面加个问号点击出详细说明」
   ⚠★ 这一段**全部用剥掉注释的 `gpCode`** 查 —— 我们自己写的说明注释里就会出现这些字
     （`data-param-reset` / `Popover` / 参数名…），不剥注释的话检查会被自己的注释骗过去。
     同款坑踩过两次：`export const API` 当锚点、`base: 'all'` 当锚点（见本文件 [10] 那段注释）。
   ⚠ 静态只查"结构对不对"；"点下去真发生什么"在布局自检 `[7.5]`（那个才作数）。 */
console.log('\n[10.5] 右栏滑杆：重置 / 名字字号 / 「?」说明');

check('★★ 参数名再也没有挂在"悬停提示"上（SV：不要悬浮文字）',
  !/Tooltip/.test(gpCode), '',
  '还留着悬停提示 ⇒ 鼠标一划过就蹦出说明挡住滑杆（他要的正是把这个去掉）');
check('★★ 参数名字号 = **16px**（12px → ×1.5=18px → 当晚再定「缩到当前的 0.9」）',
  /fontSize:\s*16/.test(gpCode), '',
  '不是 16px ⇒ 对不上 SV 定的那两次数（先大 1.5 倍，再缩到 0.9）');
check('★ 参数名那行**不再吃 `size="1"`**（否则 16px 会被 Radix 的类盖回去）',
  !/<Text size="1">\{d\.name\}<\/Text>/.test(gpCode), '',
  '又回到 size="1" ⇒ 字号白放大（Radix 的 size 类会把 inline 之外的写法压回去）');
check('★★ 说明改成参数名后面的「?」，点开才出（Popover，不是悬停）',
  /data-param-help=\{d\.k\}/.test(gpCode) && /Popover\.Content/.test(gpCode) &&
    /data-param-help-pop=\{d\.k\}/.test(gpCode), '',
  '没有「?」⇒ 那段详细说明就再也看不到了（悬停被去掉了、又没给新的入口）');
check('★★ 说明的文案来自**引擎给的那段**（`d`），不是前端自己编一份',
  /d\.d \|\|/.test(gpCode), '',
  '前端自己编说明 ⇒ 和引擎 `PARAMS` 里的说法迟早不是一回事（改引擎忘了改这边，没人看得出来）');
check('★ 说明里顺带给了"这个数怎么读"：范围 / 每格 / **出厂值**',
  /范围 \{d\.lo\} ~ \{d\.hi\}/.test(gpCode) && /每格 \{d\.step\}/.test(gpCode) &&
    /d\.dv \?\?/.test(gpCode), '',
  '没有出厂值就看不出"现在这格离默认差多远"；出厂值必须读 `dv`（前端算的区间中点是老 bug）');
check('★★ 每根滑杆都配了一个重置按钮（在按参数 map 出来的那一行里）',
  /data-param-reset=\{d\.k\}/.test(gpCode) &&
    /data-param-reset-on=\{touched \? '1' : '0'\}/.test(gpCode), '',
  '没有"每根一个"⇒ 只能整组回默认（23 根一起回，想只退一根做不到）');
check('★★★ 重置是**把键从参数串里删掉**，不是把默认值写回去',
  /delete np\[d\.k\]/.test(gpCode) && !/np\[d\.k\]\s*=/.test(gpCode), '',
  '写回 `dv` ⇒ 把出厂值也塞进了渲染请求，和引擎自己的出厂打架（这条契约由 [7] 那条钉着）');
check('★ 点重置会顺手出一张（和"拖到哪出到哪"同一条规矩）',
  /delete np\[d\.k\];[\s\S]{0,220}?requestRender\(\);/.test(gpCode), '',
  '只改数字不出图 ⇒ 数字回到默认、画面还停在拧过的样子（"看着对、其实对不上"）');
check('★ 「?」用得着的那两个东西都从 Radix 拿到了（Popover 进来了、Tooltip 出去了）',
  /import \{[^}]*Popover[^}]*\} from '@radix-ui\/themes'/.test(gradeSrc) &&
    !/import \{[^}]*Tooltip[^}]*\}/.test(gradeSrc), '',
  'import 没跟着改 ⇒ tsc 直接红（`Cannot find name`），或者留着没用的 Tooltip 又被下一个人捡去用');

/* ---- 底栏空的时候必须**说出为什么**（09-15 SV 报「底部栏没了」）----
   ★ 现场：调色台 + 一个一张星都没有的主题 ⇒ 底栏一格都没有，**和"坏了"长得一模一样**。 */
const dockCode = read('src/components/Dock.tsx').replace(/\/\*[\s\S]*?\*\//g, '');
check('★★ 底栏**空了要出声**（不是一片空白等着用户猜）',
  /data-dock-empty=/.test(dockCode), '',
  '空着不出声 ⇒ 用户只看到"底下那条没了"，无从判断是坏了还是筛掉了（本项目最忌的一类）');
check('★ 两种"空"要分开说：调色台是"只列已打星"，选片台是"筛选下没有"',
  /grade-no-star/.test(dockCode) && /mode === 'grade'/.test(dockCode), '',
  '一句话套两种情形 ⇒ 在选片台点了"5★"却被告知"去选片台打星"，更糊涂');
check('★ 空态那层**不许挡住**底栏自己的滚动/点击',
  /pointerEvents: 'none'/.test(dockCode), '',
  '盖在上面还吃事件 ⇒ 底栏虽然空、但连滑动/点空白都失灵（比空着更难查）');


console.log('\n[11] 大图：一律「适应」+ 视图三档（A / A|B / B）');
const viewerCode = read('src/components/Viewer.tsx').replace(/\/\*[\s\S]*?\*\//g, '');
const fitSrc = read('src/components/FitImage.tsx');
const fitCode = fitSrc.replace(/\/\*[\s\S]*?\*\//g, '');
check('★ 缩放那个组件**已经删掉了**（别再留个没人用的 ZoomImage 让人捡去用）',
  !exists('src/components/ZoomImage.tsx'), '',
  '文件还在 ⇒ 下一个人会以为"缩放还在、只是没用上"，又给接回去');
/* ⚠ 是 **2 处**不是 3：选片台一处 + `Pane` 里一处（`Pane` 渲染两份 ⇒ 屏幕上最多三张图，
   但写法只有两处 —— 数 `<FitImage` 的**出现次数**，不是数屏幕上的图）。 */
check('★★ 三张图共用 `FitImage`（选片台一张 + 调色台两栏，写法只有两处）',
  /import\s*\{\s*FitImage\s*\}/.test(viewerCode) &&
    (viewerCode.match(/<FitImage\b/g) || []).length === 2 &&
    !/transform:\s*`scale/.test(viewerCode), '',
  'Viewer 自己又写一套尺寸 ⇒ 两份实现早晚会长歪（本项目的老毛病）');
check('★★★ 缩放**全删**（滚轮 / 1:1 / 双击 / 百分比徽标）—— SV 09-15 定「删除其他的」',
  !/wheel/i.test(fitCode) && !/scale\(/.test(fitCode) && !/1:1/.test(fitCode) &&
    !/data-zoom/.test(fitCode) && !/data-zoom/.test(viewerCode), '',
  '还留着某一样（或某个 `data-zoom` 探针）⇒ 就是没删干净，他要的是"总用适应"');
check('★ 图仍然是 contain + 绝对定位（防"小窗下被裁"那个老 bug 复发）',
  /objectFit: 'contain'/.test(fitCode) && /position: 'absolute'/.test(fitCode), '',
  '退回 maxWidth/maxHeight:100% ⇒ 高度不确定时图按原尺寸渲染、被外层 overflow 裁掉');
check('★★★ 三档就是 A / A|B / B，且**默认 A|B**（一进来是左右对比，不是单张）',
  /useState<ViewMode>\('ab'\)/.test(viewerCode) &&
    /\{ id: 'a', label: 'A'/.test(viewerCode) &&
    /\{ id: 'ab', label: 'A\|B'/.test(viewerCode) &&
    /\{ id: 'b', label: 'B'/.test(viewerCode), '',
  '默认档不是 ab ⇒ 一进调色台看到的是单张');
check('★★ 三档**真的改画面**（两栏按档位条件渲染，不是画个按钮摆着）',
  /\{view !== 'b' && <Pane title="原图"/.test(viewerCode) && /\{view !== 'a' && \(/.test(viewerCode),
  '', '按钮点了没反应 ⇒ 比没有按钮更糟');
check('★★ 切档**不碰渲染**（换看法 ≠ 重新出一张；`setView` 附近不许出现出图调用）',
  !/setView[\s\S]{0,60}?requestRender\(\)/.test(viewerCode) &&
    !/onChange=\{setView\}[\s\S]{0,200}?requestRender\(\)/.test(viewerCode), '',
  '切个视图就重出一张 ⇒ 变成"切档等 6 秒"（那张 dataURL 本来就还在手上）');

/* ---------- [11.5] 起引擎：只许有**一个** spawn 点 + 单飞守卫（09-15 SV 报「点出图没反应」） ----------
   ★ 事故：台子每次启动起了**两个**引擎，两个都 LISTENING 8765。机理与证据（都实测过）：
     · `engine_start.log` 里 `[spawn] pid=…` **一律成对出现**；
     · `netstat` 里 8765 同时挂着两个 PID；CPU 采样一个用掉 4.53 CPU 秒、另一个 0.00；
     · `ThreadingHTTPServer.allow_reuse_address = 1` 在 Windows 上走 SO_REUSEADDR，
       语义是"**可以抢**"（不是 POSIX 的"TIME_WAIT 能重绑"）⇒ 第二个 bind **不报错**。
     ⇒ 请求被两进程分掉，落在"刚起、什么都没载入"的空引擎上的**瞬间失败**
       （`_cache_get` 对不存在的 id 是秒回），而真在干活的那个 CPU 一动不动。
   ★ 触发：进调色台时**两个地方同时**调 `engine-start`（Viewer 装载 effect + 右栏拉参数），
     而 `engine-start` 第一句 `await engineGet('/health')` 要等冷启动 10~20 秒
     ⇒ 两个调用**都**看到"没人应答" ⇒ 各 spawn 一个。
   ★ 这一节盯台子侧那道闸；引擎侧那道（`_Server.allow_reuse_address = False` + `_port_taken`）
     在引擎自检 `t_single_engine` 里。两边缺一不可：这里拦"我们自己的重复调用"，
     那边拦"任何来源的第二份"，还顺手把"绑不上"这件事**说人话**。
   ⚠ 只查"源码里有 `engineStartInflight` 这几个字"是假绿 —— 守卫被绕过、字串还在。
     所以下面**数 `spawn(` 的出现次数**：起引擎那条路必须全仓库只有一处。 */

console.log('\n[11.5] 起引擎：唯一的 spawn 点 + 单飞守卫');
{
  const gM = mainJsCode.match(/let engineStartInflight = null;[\s\S]*?ipcMain\.handle\('engine-start'[\s\S]*?\n\}\);\n/);
  check('★★ 能抽出「单飞守卫 + engine-start 处理函数」整段（抽不出来 ⇒ 下面全是空转）',
    !!gM, '', '改名/挪位置了，这一节必须跟着改，别让它悄悄变绿');
  const gCode = gM ? gM[0] : '';

  check('★★★ 起引擎这条路上全仓库**只有一处** `spawn(py, [` —— 多的那处就是第二个引擎',
    (mainJsCode.match(/spawn\(py, \['-u', '-m', 'svFilm\.service'/g) || []).length === 1, '',
    '两处 spawn ⇒ 进调色台时两个调用各起一个，两个都绑上 8765（Windows 上不报错），' +
    '请求被分掉 = 点了像没反应');

  check('★★ 同一时刻只许一次"起引擎"在飞（后到的接上同一个 Promise，不是再起一个进程）',
    /if \(!engineStartInflight\) \{/.test(gCode) &&
      /engineStartInflight = doEngineStart\(\)/.test(gCode) &&
      /return engineStartInflight;/.test(gCode), '',
    '少了这个 if ⇒ 两个并发调用各进一次 doEngineStart，守卫等于没写');

  check('★★ 跑完要**放开**（`.finally` 里清空）—— 引擎真挂了还得能再拉一次',
    /\.finally\(\(\) => \{ engineStartInflight = null; \}\)/.test(gCode), '',
    '不放开 ⇒ 引擎崩了之后永远拉不起来，症状还是"点了没反应"');

  check('★ 处理函数本身**不许**直接调 doEngineStart（必须经过守卫）',
    !/ipcMain\.handle\('engine-start',\s*(async\s*)?\(\)\s*=>\s*doEngineStart\(\)/.test(mainJsCode), '',
    '绕过守卫直接起 ⇒ 又是一个只有一处 spawn 但每次都能开两个的写法');

  check('★ 起引擎前先探 `/health`（已在跑就别再 spawn）—— 探测的等待就是双 spawn 的窗口',
    /const alive = await engineGet\('\/health'/.test(gCode) &&
      /if \(alive\.ok\) return \{ ok: true, already: true/.test(gCode), '');

  check('★ 子进程的 stdout/stderr 落**日志文件**（静默正是这次查半天的原因）',
    /stdio: \['ignore', fs\.openSync\(engineLogFile\(\), 'a'\)/.test(gCode), '',
    'stdio 全 ignore ⇒ 引擎起不来时一点线索都没有');
}

/* ---------- [11.6] 出图 / 导出的**请求痕迹**（09-15 深夜 SV 选「A」） ----------
   ★ 起因：SV 报「点出图中没反应，我看后台也没动静」，而事后**一点痕迹都查不到** ——
     `engineGet` 一行日志都不写：请求发出去没有、超时没有、引擎回了什么，全看不见。
   ★ 这一节盯的是「**能不能分辨下面这两种**」——它们表现一模一样、成因完全不同：
       · 只有「发出」没有「回来」 ⇒ 卡在**引擎**里（这次的双引擎就是这种）
       · 「发出」都没有           ⇒ 卡在**台子**这侧（导出那个「另存为」对话框还挂着 / 被取消）
     ⇒ 所以必须记**两行**，而且**要真的跑一遍** ——
       只查"源码里有 engineTrace 这几个字"是假绿（守卫被绕过、字串还在）。
   ★ 只记 /load /base /render /export 这几条要等的路；/health、/stocks、/params 不记，否则刷屏。
   ⚠ 只许从 `engineGet` 走：谁直接调 `engineGetRaw` 就**悄悄丢了日志**
     （本项目的病根族：**绕过去 = 静默**，和"名字认不得就吞掉"是一回事）。 */

console.log('\n[11.6] 出图请求的痕迹（发出去 / 回来，各一行）');
{
  const one = (re) => (mainJsCode.match(re) || [''])[0];
  const traceBody = one(/function engineTrace\(line\) \{[\s\S]*?\n\}/);
  const trSrc = (mainJsCode.match(/const ENGINE_TRACE_RE = (\/[^\n]+?\/);/) || [])[1];
  const body = [
    one(/const ENGINE = \{[^}]*\};/),
    (mainJsCode.match(/const ENGINE_TRACE_RE = \/[^\n]+?\/;/) || [''])[0],
    one(/const ENGINE_TRACE_PATH_KEYS = \{[^}]*\};/),
    traceBody,
    one(/function engineTraceQ\(search\) \{[\s\S]*?\n\}/),
    one(/function engineGet\(pathname, timeoutMs\) \{[\s\S]*?\n\}/),
    one(/function engineGetRaw\(pathname, timeoutMs\) \{[\s\S]*?\n\}/)
  ].join('\n');

  check('★★ 能把「痕迹」那一整段原样抠出来（抠不出来 ⇒ 下面全是空转）',
    !!trSrc && !!traceBody && /function engineGetRaw\(/.test(body) && /function engineTraceQ\(/.test(body),
    '', '改名/挪位置了，这一节必须跟着改，别让它悄悄变绿');

  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), 'svst-trace-'));
  let M = null;
  try {
    M = new Function('fs', 'path', 'debugDir', 'http',
      body + '; return { engineGet, engineTraceQ, ENGINE_TRACE_RE, ENGINE };'
    )(fs, path, () => logDir, http);
  } catch (e) {
    M = null;
  }
  check('★ 这段代码能在 Node 里独立跑起来（不是只做字面检查）', !!M, '',
    '抠出来跑不了，说明它依赖了外面没给的东西');

  if (M) {
    const logFile = path.join(logDir, 'svstudio_render.log');
    const readLog = () => (fs.existsSync(logFile) ? fs.readFileSync(logFile, 'utf8') : '');
    // 挑一个几乎不可能有人在听的端口：发出去必然失败，但**失败也得留痕**
    M.ENGINE.port = 41000 + Math.floor(Math.random() * 2000);
    M.ENGINE.base = 'http://127.0.0.1:' + M.ENGINE.port;

    await M.engineGet('/render?id=1&stock=portra400&params=A:1', 3000);
    const lines = readLog().trim().split('\n').filter((l) => l);
    check('★★★ 发一次 /render ⇒ 日志里正好**两行**：先「发出」后「回来」',
      lines.length === 2 && /\[→\] render /.test(lines[0]) && /\[←\] render /.test(lines[1]),
      '', '实际 ' + lines.length + ' 行——' + JSON.stringify(lines));
    check('★ 「发出」那行说清在要什么（哪张 / 哪个卷 / 什么参数）',
      /id=1/.test(lines[0] || '') && /stock=portra400/.test(lines[0] || ''), '', String(lines[0]));
    check('★ 「回来」那行带**耗时和结果**（有耗时才能把"慢"和"没反应"分开）',
      /\d+\.\ds/.test(lines[1] || '') && /(ok|失败：)/.test(lines[1] || ''), '', String(lines[1]));

    const before = readLog();
    await M.engineGet('/health', 1500);
    check('★★ /health 那种探询**一行都不记**（不然拉一下滑杆日志就刷屏）',
      readLog() === before, '', '多写了：' + JSON.stringify(readLog().slice(before.length)));

    /* ---- engineTraceQ：纯函数，直接喂 ----
       ⚠ 这里**必须用假路径**（`X:\示例库\主题A`）——真实拍摄信息不许进仓库。 */
    const q = M.engineTraceQ;
    const red = q('?paths=' + encodeURIComponent('X:\\示例库\\主题A\\示例0001.RAF'));
    check('★★ 路径只剩**文件名**（日志不该出现真实目录名）',
      red.indexOf('示例0001.RAF') >= 0 && red.indexOf('示例库') < 0 && red.indexOf('主题A') < 0, '', red);
    const red2 = q('?src=' + encodeURIComponent('X:\\示例库\\主题A\\示例0002.RAF') +
      '&path=' + encodeURIComponent('X:\\示例库\\主题A\\示例0002_svfilm.jpg'));
    check('★★ src / path 一样只留文件名（导出那条路也带真实目录）',
      red2.indexOf('示例0002.RAF') >= 0 && red2.indexOf('示例库') < 0, '', red2);
    check('★ 不是路径的键**原样保留**（别把真正要看的信息也脱敏掉）',
      q('?id=7&stock=portra400&side=700') === 'id=7&stock=portra400&side=700', '', q('?id=7'));
    const long = q('?params=' + 'K'.repeat(900));
    check('★ 超长要**截断并说明截了多少**（一行几 KB 反而看不清）',
      long.length < 500 && /共 \d+ 字符/.test(long), '', '长度 ' + long.length);
    check('★ 空 query 也说句话（留一段空白会让人以为日志坏了）',
      q('') === '(无参数)' && q('?') === '(无参数)', '');

    const tr = trSrc ? new Function('return ' + trSrc)() : null;
    check('★★★ 要记的四条路都在（load / base / render / export）',
      !!tr && ['/load', '/base', '/render', '/export'].every((p) => tr.test(p)), '');
    check('★★ 探询那几条**不在**（/health /stocks /params）',
      !!tr && !['/health', '/stocks', '/params'].some((p) => tr.test(p)), '');
  }

  /* ---- 接线钉子：谁都不许绕过 `engineGet` 直接调 `engineGetRaw` ---- */
  const rawCalls = (mainJsCode.match(/\bengineGetRaw\(/g) || []).length;
  check('★★★ `engineGetRaw(` 全仓库只出现**两处**（定义 + 包装里那一次）—— 没人绕过日志',
    rawCalls === 2, '实际 ' + rawCalls + ' 处',
    '有调用点绕过 ⇒ 那条路出图不留痕迹，症状还是"点了没反应、查不到"');
  check('★ 「发出」「回来」各只有一处（`[→]` / `[←]`）',
    (mainJsCode.match(/engineTrace\('\[→\] /g) || []).length === 1 &&
    (mainJsCode.match(/engineTrace\('\[←\] /g) || []).length === 1, '',
    '两处以上 ⇒ 同一次请求会写重复的行，读数的时候会以为自己看错了');
  check('★ 和界面日志**同一个文件**（出事时只看一个地方）',
    /svstudio_render\.log/.test(traceBody), '', '写到别处去了 ⇒ 查问题时得知道去哪个文件翻');
  check('★★ 写日志失败**不许影响出图**（appendFileSync 外面必须有 try）',
    /function engineTrace\(line\) \{\s*try \{/.test(traceBody), '',
    '日志写不出去就把出图也带崩 ⇒ 为了留痕迹反而把功能搞坏');
}

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
const exB = mainJsSrc.indexOf('function scanSessions()');
const exBody = exA >= 0 && exB > exA ? mainJsSrc.slice(exA, exB) : '';

check('★ 配置里有 `extraRoots`（加过的库外目录，存绝对路径）',
  /extraRoots:\s*\[\]/.test(mainJsSrc), '',
  '没有这个键 ⇒ 加过的目录下次开台子就没了（"加了个寂寞"）');
check('★★ 主题列表**只**从"加过的文件夹"来（`scanSessions` 除了 `extraSessions` 没有别的来源）',
  /function scanSessions\(\)/.test(mainJsSrc) &&
    /* ⚠ 锚点必须是**调用点**：函数定义 `function extraSessions(used) {` 里
       也含 `extraSessions(used)` ⇒ 拿它当锚点的话，删掉调用点这检查照样绿。
       （09-15 就是破法 61 把它抓出来的 —— 典型的"只查标题在不在"。） */
    /for \(const s of extraSessions\(used\)\) out\.push\(s\);/.test(mainJsSrc) &&
    /* ★ 反向：不许再冒出"扫某个固定根的一级子目录"那段（09-15 那个死锁就是它） */
    !/fs\.readdirSync\(libRoot/.test(mainJsSrc), '',
  '只扫某个固定根 ⇒ 加进来的文件夹永远不出现（09-15 那个"点了没反应"就是这么来的）');
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
check('★ 每条都带 `path`（前端只剩这一条路，进目录**不许**再自己拼）',
  /name: uniqueName\(used, name\),\s*\n\s*path: full, external: true, rootDir: rootDir,/
    .test(mainJsSrc), '',
  '没有真路径 ⇒ `enterSession` 只能瞎拼一个，进去 0 张还看不出是拼错了');
check('★★ 重名时只改**后面**那条（先来的名字是星级的身份键，一个字符都不许动）',
  /function uniqueName\(used, base\)/.test(mainJsSrc) &&
    /name: uniqueName\(used, name\)/.test(mainJsSrc) &&
    !/used\.add\(e\.name\.toLowerCase\(\)\)/.test(mainJsSrc), '',
  '两边都改名 / 改错边 ⇒ 那个人几周的星级 / 配方全丢');
check('★★★ `enterSession` 用**条目给的真实路径**，而且**一点拼路径的退路都不留**',
  /const sessionPath = \(s && s\.path\) \|\| '';/.test(storeCode) &&
    !/root \+ '\\\\' \+ name/.test(storeCode) &&
    !/get\(\)\.libRoot/.test(storeCode), '',
  '留着拼路径的退路 ⇒ 拼出来的目录不存在，进去 0 张还看不出是拼错了');
check('★ 加入 / 移除两个动作都在 store 里，四边齐（接口 + 实现）',
  /addExtraRootDir: \(dir: string\) => Promise<void>;/.test(storeCode) &&
    /removeExtraRootDir: \(dir: string\) => Promise<void>;/.test(storeCode) &&
    /addExtraRootDir: async \(dir\) =>/.test(storeCode) &&
    /removeExtraRootDir: async \(dir\) =>/.test(storeCode), '',
  '接口 declaration 和实现少一边 ⇒ 要么编译不过，要么这个动作根本调不到');
check('★ 加入 / 移除都落到 `config.extraRoots`（同一个键，不各存各的）',
  /API\.setConfig\(\{ extraRoots: \[\.\.\.list, d\] \}\)/.test(storeCode) &&
    /API\.setConfig\(\{ extraRoots: list\.filter\(/.test(storeCode), '');
check('★ 同一个文件夹不许加两遍（自己 / 上一层已经加过 ⇒ 都挡掉）',
  /这个文件夹已经加过了/.test(storeCode) &&
    /D\.startsWith\(normPath\(x\) \+ '\\\\'\)/.test(storeCode), '',
  '不挡 ⇒ 同一个文件夹列两条（还得靠改名区分），看着像出了 bug');

/* ★★ 老配置迁移：**把函数抽出来真跑一遍**（不是查"函数名在不在"）。
   09-15 SV 选「B」把「图库根」砍了 —— 老 config 里的 `libRoot` 必须变成
   "加过的第一个文件夹"，否则他原来那个照片库**凭空从列表里消失**。
   ⚠ 这是"删键 + 搬家"两步，最容易写成"键删了、东西没搬"（界面上就是一片空）。 */
const sameSrc = (mainJsSrc.match(/\nfunction samePath\(a, b\) \{[\s\S]*?\n\}\n/) || [''])[0];
const migSrc = (mainJsSrc.match(/\nfunction migrateConfig\(cfg\) \{[\s\S]*?\n\}\n/) || [''])[0];
check('★★ 老配置迁移的两个函数能**原样抽出来**（抽不出来下面几条就是空转）',
  !!sameSrc && !!migSrc, '', '抽不出来 ⇒ 下面那条测的是空气');
if (sameSrc && migSrc) {
  const made = [];
  const migrate = new Function(
    'saveConfig',
    sameSrc + migSrc + '\nreturn migrateConfig;'
  )((c) => made.push(JSON.parse(JSON.stringify(c))));

  const c1 = { libRoot: 'D:\\照片库', extraRoots: [] };
  migrate(c1);
  check('★★★ 老 config 的 `libRoot` 变成 `extraRoots` 里的一条（不然他的照片库凭空消失）',
    !('libRoot' in c1) && c1.extraRoots.length === 1 && c1.extraRoots[0] === 'D:\\照片库',
    JSON.stringify(c1), '键删了、东西没搬 ⇒ 左栏空着，他会以为片子没了');

  migrate(c1);
  check('★ 迁移只发生一次（第二次跑不再改、也不重复加）',
    made.length === 1 && c1.extraRoots.length === 1,
    `saveConfig 被调 ${made.length} 次`, '不幂等 ⇒ 每次开台子都往列表里塞一条');

  const c2 = { libRoot: 'D:\\照片库', extraRoots: ['E:\\别的'] };
  migrate(c2);
  check('★ 已经有 `extraRoots` 时，老 `libRoot` 也照搬（老的排最前）',
    c2.extraRoots.length === 2 && c2.extraRoots[0] === 'D:\\照片库',
    JSON.stringify(c2.extraRoots), '漏搬 / 顺序乱 ⇒ 列表里少一条，或者第一条不是原来那个库');

  const c3 = { libRoot: 'd:/照片库/', extraRoots: ['D:\\照片库'] };
  migrate(c3);
  check('★ 大小写 / 尾斜杠不同**算同一个**文件夹（不重复加一条）',
    c3.extraRoots.length === 1, JSON.stringify(c3.extraRoots),
    '不去重 ⇒ 同一个库在列表里出现两条');
}
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
check('★ 条目有可断言的标记（布局自检靠它认，不去认颜色）',
  /* ⚠ `data-session-ext` 在这儿**不是**"库外"的意思 —— B 之后每条都来自"加过的文件夹"。
     留着它是因为布局自检要拿它把"加进来的文件夹"和 mock 里那两条默认条目**区分开**。 */
  /data-session=/.test(paneSrc) && /data-session-ext=/.test(paneSrc) &&
    /data-session-gone=/.test(paneSrc) && /data-session-del=/.test(paneSrc), '',
  '没有能选中的标记 ⇒ 布局自检只能去认颜色/文字，改个样式就误报');
/* ★★ 跨层钉子：布局自检的 mock 必须**照生产端**给这两样，
   否则真浏览器那组在测一份不存在的形状（"mock 跟着前端一起错"的老坑）。 */
check('★★ 布局自检 mock 的 `getConfig` 带 `extraRoots`、且**不再有 `libRoot`**（照生产端抄）',
  /getConfig: async \(\) => \(\{ extraRoots: window\.__extraRoots \|\| \[\] \}\)/.test(mockSrcFlat) &&
    !/libRoot: 'D:\\\\lib'/.test(mockSrcFlat), '',
  'mock 不给 ⇒ 「加入目录」那条链在自检里根本没数据可走；' +
    '还留着 libRoot ⇒ 自检在测一个生产端已经没有的键');
check('★★ 布局自检 mock 的 `scanSessions` 把库外目录并排列出来（带 path/external/rootDir）',
  /scanSessions: async \(\) => \{/.test(mockSrcFlat) && /external: true,/.test(mockSrcFlat) &&
    /const ex = window\.__extraRoots \|\| \[\];/.test(mockSrcFlat), '',
  'mock 只返回库内主题 ⇒ 「点库外条目用的是真实路径」这条根本测不到');
check('⚠ 布局自检 mock 必须实现 `pickDirectory`（左栏「加入目录」会调它）',
  /pickDirectory: async \(\) =>/.test(mockSrcFlat), '',
  '漏了它 ⇒ 点那一刻同步抛 TypeError（漏 setConfig 那次的翻版）');

/* ---------- [14] 每个目录 ⇒ 预览小图（片 = RAW；缩略图一律取机内 JPG） ----------
   ★★ 背景：工作台以前列图**只按 JPG 列**（`IMG_EXT`），RAW 只当"这张有 RAF"的角标
      ⇒ 一张 JPG 都没有的目录在界面上是**空的**；更阴的是**混着 JPG 的目录**里，
      那些"没有同名 JPG"的 RAW 连角标都没有、直接不出现。
      09-15 SV 定：**一张片 = 一个 RAW**，缩略图一律从 RAW 抠机内 JPG
      （缩到长边 1600，写进应用缓存）。
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
  check('★★ 脚本给**每一张 RAW**都配小图（不再跳过"有同名 JPG"的那些）',
    /def pick_raws\(src\)/.test(pySrc) && !/have_jpg/.test(pySrc), '',
    '跳过有同名 JPG 的 RAW ⇒ 混着 JPG 的目录里，那些"没有同名 JPG"的 RAW 整片消失（09-15 修的那个 bug）');
  check('★ 脚本有单文件模式（`--one` / `--out-file`）—— 归档要一份全尺寸相机直出时用',
    /'--one'/.test(pySrc) && /'--out-file'/.test(pySrc) && /def run_one\(/.test(pySrc), '',
    '没有单文件模式 ⇒ 归档时只能拿 1600 的缩略图当"直出件"，或者直接报"root 原图不存在"');
  check('★ 不再写 `_src.txt`（那是"path 指向缓存"时代的凭据，现在 path 就是源目录）',
    !/'_src\.txt'/.test(pySrc), '',
    '还在写 ⇒ 两条出图源凭据并存，下一个人不知道该信哪条');
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
    const theRoot = path.join(treeRoot, '某次拍摄');          // 纯壳：自己一张片都没有
    const put = (d, names) => {
      fs.mkdirSync(d, { recursive: true });
      for (const n of names) fs.writeFileSync(path.join(d, n), '');
    };
    put(path.join(theRoot, '纯RAW组'), ['A.RAF', 'B.RAF']);          // 一张 JPG 都没有
    put(path.join(theRoot, '混着的'), ['A.JPG', 'A.RAF', 'B.RAF']);  // ★ 关键：B 只有 RAW
    put(path.join(theRoot, '混着的', '初筛1星'), ['A.JPG']);          // 星级桶：**不是**另一个主题
    put(path.join(theRoot, '只有JPG'), ['C.JPG']);                   // 孤 JPG
    put(path.join(theRoot, '壳', 'x100vi'), ['D.RAF']);              // 壳自己没有片
    put(path.join(theRoot, '壳2'), ['E.RAF']);                       // 壳2 自己有片
    put(path.join(theRoot, '壳2', 'sub'), ['F.JPG']);                // ⇒ 也各列一条（不许丢）
    /* ⚠ `describeSessionDir` 现在会调 `photoFilesIn`（"一层目录里有哪几张片"的唯一口径）。
       不把它一起抽出来的话，下面那段一跑就 ReferenceError —— 整个自检断在那儿
       （这是**好事**：说明这条钩子真的在跑；技能 §19.3 记的就是这个坑）。 */
    const pfiM = mainJsCode.match(/function photoFilesIn\(files\)\s*\{[\s\S]*?\n\}/);
    check('main.js 里有 photoFilesIn（“一层目录里有哪几张片”的唯一口径）', !!pfiM, '',
      '函数没了？它是「片 = RAW」这条口径的唯一出处');
    const pfiSrc = pfiM ? pfiM[0] : '';
    /* ★★★ `photoFilesIn` 是「一层目录里有哪几张片」的**唯一口径**，必须**真跑**。
       ⚠★ 为什么不能只断言目录的 `count`：`describeSessionDir` 会把四个星级桶里的键并进 `count`，
         桶里恰好也有一张同名片时，**root 少算一张也照样等于 2** ——
         破法脚本当场抓到过这个假绿（把这条口径改坏，自检照旧全绿）。
         ⇒ 所以直接对函数断言，连**身份键**和 `rawOf` 一起钉死。 */
    let pfi = null;
    /* ⚠ `photoFilesIn` 依赖 `RAW_EXT` + `IMG_EXT` 两个常量（技能 §19.3 那个坑：
       抽一段源码出来跑，它依赖的东西要一起注进去，否则整段断在这儿）。 */
    const imxM = mainJsCode.match(/const IMG_EXT = (\/[^\n]*?\/i);/);
    if (pfiSrc && rexM && imxM) {
      try {
        const RAWE4 = new Function('return ' + rexM[1])();
        const IMGE4 = new Function('return ' + imxM[1])();
        pfi = new Function('RAW_EXT', 'IMG_EXT', pfiSrc + '; return photoFilesIn;')(RAWE4, IMGE4);
      } catch (e) { pfi = null; }
    }
    check('photoFilesIn 能在 Node 里独立跑起来（可单测）', typeof pfi === 'function', '',
      '取出来那段跑不了 ⇒ 下面那几条全在空转');
    if (typeof pfi === 'function') {
      const mix = pfi(['A.JPG', 'A.RAF']);
      check('★★★ 同一层里 RAW 和**同名 JPG** 都在 ⇒ 只算**一张**（键是 `<stem>.JPG`，RAW 才是真身）',
        mix.count === 1 && mix.keys.length === 1 && mix.keys[0] === 'A.JPG' &&
          mix.rawOf.get('A.JPG') === 'A.RAF' && mix.hasRaw === true,
        JSON.stringify({ keys: mix.keys, rawOf: mix.rawOf.get('A.JPG') }),
        '把同名 JPG 另算一张 ⇒ 同一张片在界面上出现两条；或者反过来把 RAW 当成多余的 ⇒ 那张片直接不出现');
      check('★★★ 身份键必须是 `<stem>.JPG`（**不能**改成 `.RAF`）—— 桶里的复制件靠它跟原图合并',
        pfi(['A.RAF']).keys[0] === 'A.JPG', JSON.stringify(pfi(['A.RAF']).keys),
        '键变成 .RAF ⇒ 桶里的 JPG 复制件合并不上同一张片（列表出现两条），**已有星级全部落空**');
      check('★★ 混着的目录：**没有同名 JPG** 的 RAW 也要各算一张（09-15 那个 bug 的正身）',
        pfi(['A.JPG', 'A.RAF', 'B.RAF']).count === 2, pfi(['A.JPG', 'A.RAF', 'B.RAF']).count,
        '只数 JPG ⇒ B.RAF 在界面上**根本不出现**，连角标都没有');
      check('★ 一张 JPG 都没有也照样数出来（纯 RAW 目录不是"空的"）',
        pfi(['A.RAF', 'B.RAF']).count === 2 && pfi(['A.RAF']).hasRaw === true, '',
        '纯 RAW 目录算空 ⇒ 选片台空、调色台进不去');
      check('★ 孤 JPG（没有同名 RAW）沿用**它自己的真实文件名**当键，`rawOf` 里查不到',
        pfi(['Lone.JPG']).keys[0] === 'Lone.JPG' && pfi(['Lone.JPG']).rawOf.has('Lone.JPG') === false,
        JSON.stringify(pfi(['Lone.JPG']).keys), '');
      check('★ 同名多形态 / 大小写不同只留一条（`d.raf` + `D.RAF` + `D.JPG` ⇒ 一张）',
        pfi(['d.raf', 'D.RAF', 'D.JPG']).count === 1 &&
          pfi(['d.raf', 'D.RAF', 'D.JPG']).rawOf.get('D.JPG') === 'd.raf',
        JSON.stringify(pfi(['d.raf', 'D.RAF', 'D.JPG']).keys), '');
    }
    let fx = null;
    try {
      fx = new Function('fs', 'path', 'loadConfig', 'crypto', 'app',
        cSrc + '\n' + pfiSrc + '\n' + region + '\n; return { extraSessions: extraSessions };'
      )(fs, path, () => ({ extraRoots: [theRoot] }), crypto, { getPath: () => idxRoot });
    } catch (e) { fx = null; }
    check('extraSessions 能在 Node 里对**真目录树**跑起来', !!fx, '', '取出来那段跑不了');
    if (fx) {
      const list = fx.extraSessions(new Set());
      const names = list.map((x) => x.name);
      const by = (n) => list.find((x) => x.name === n);
      check('★★ 没片的壳**自己不出现在列表里**，但它底下"有片的目录"要各列一条',
        !names.some((n) => /^某次拍摄$/.test(n)) && !!by('x100vi'), JSON.stringify(names),
        '没片的壳也列一条 ⇒ 左栏全是点进去空的条目；底下有片的没列 ⇒ 那批照片直接看不见');
      check('★★★ 自己有片的目录**也要继续往下看**（不封顶）—— 否则"照片库→某次拍摄→子目录"会丢照片',
        list.length === 6 && !!by('壳2') && !!by('sub'), JSON.stringify(names),
        `只列出 ${JSON.stringify(names)} ⇒ "壳2/sub" 那批照片从列表里凭空消失，而界面一点迹象都没有`);
      check('★★★ 四个星级桶（初筛1星/…）**不是**主题，不许被列成独立条目',
        !names.some((n) => /初筛1星|调色后满意|待发布|调色待验收/.test(n)), JSON.stringify(names),
        '桶被当成主题 ⇒ 每条主题都多出三四条，左栏彻底没法看');
      /* ⚠ 这条只是**目录级**的粗验：`count` 会把星级桶里的键并进来，桶里正好是 `A.JPG`
         ⇒ 就算 root 少算一张，`count` 还是 2（**掩盖**）。能真正咬住的是上面那组
         `photoFilesIn` 直接断言 —— 破法脚本验过：那组会红，这条不会。 */
      check('★★★ 混着 JPG 的目录：**那些没有同名 JPG 的 RAW 也要算一张**（09-15 修的那个 bug）',
        by('混着的') && by('混着的').count === 2, by('混着的') && by('混着的').count,
        '只数 JPG ⇒ 只有 A.JPG 那张算数，B.RAF 在界面上**根本不出现**（连角标都没有）');
      check('★ 一张 JPG 都没有的目录也算照片目录（纯 RAW），张数按 RAW 数',
        by('纯RAW组') && by('纯RAW组').count === 2 && by('纯RAW组').hasRaw === true,
        by('纯RAW组') && by('纯RAW组').count, '纯 RAW 目录算"空的"⇒ 选片台空、调色台进不去');
      check('★ 孤 JPG（没有同名 RAW）也算一张',
        by('只有JPG') && by('只有JPG').count === 1 && by('只有JPG').hasRaw === false,
        by('只有JPG') && by('只有JPG').count, '');
      check('★ 展开只往下 2 层（再深就不是"一次拍摄"而是"目录层级"了）',
        list.length === 6, list.length,
        '深度没兜住 ⇒ 万一把整个盘拖进来会把左栏打成几千条');
      check('★ `path` 是**源目录**（09-15 起不再指向预览缓存）',
        by('纯RAW组') && by('纯RAW组').path === path.join(theRoot, '纯RAW组') &&
          !String(by('纯RAW组').path).startsWith(path.resolve(idxRoot)),
        by('纯RAW组') && by('纯RAW组').path,
        'path 指缓存 ⇒ 列表/桶/星级全落在缓存上，而缓存一删他的星级也跟着"消失"');
      check('★ 每条都标了「待建索引」+ 索引目录（每张 RAW 都要一张预览小图）',
        by('纯RAW组') && by('纯RAW组').needsIndex === true && !!by('纯RAW组').indexDir,
        by('纯RAW组') ? JSON.stringify({ needsIndex: by('纯RAW组').needsIndex }) : '',
        '不标 ⇒ 界面不会去建索引，那些片在左栏里就是没图的');
      const list2 = fx.extraSessions(new Set());
      const idx2 = (list2.find((x) => x.name === '纯RAW组') || {}).indexDir;
      check('★ 同一个目录两次扫出来的索引目录是**同一个**（缓存不会越攒越多）',
        idx2 && by('纯RAW组') && idx2 === by('纯RAW组').indexDir, String(idx2), '');
    }
    try { fs.rmSync(treeRoot, { recursive: true, force: true }); } catch (e) { /* ok */ }
    try { fs.rmSync(idxRoot, { recursive: true, force: true }); } catch (e) { /* ok */ }
  }
}

/* ---- ④ 接线钉子（这一段的坑都在"两边传的不是同一个东西"上） ---- */
check('★★★ 建索引传的是**源目录**（`s.path`），而且"缓存目录当源目录"那种写法不许回来',
  /* ⚠ 第二参（`force`）是可选的，正则别把括号写死 —— 09-15 加了 `, true` 就假红过一次。 */
  /indexExternalDir\(s\.path[,)]/.test(storeSrc) && !/indexExternalDir\(s\.srcDir/.test(storeSrc),
  '', '传缓存目录 ⇒ 那儿一张 RAW 都没有，扫出来永远是空的，而界面一点错都不报');
check('★ 进主题时「索引还没建」就先补上，而且**带 `force` 重试**（否则列表显示 4 张、点进去 0 张）',
  /if \(s && s\.needsIndex\)/.test(storeSrc) &&
    /await get\(\)\.indexExternalDir\(sessionPath, true\);/.test(storeSrc), '',
  '不先补 ⇒ 列表和里面张数对不上，用户以为片子丢了；少了 `true` ⇒ 失败过一次以后再点那个文件夹一声不吭');
check('★★ 建不了的时候要**说出原因**（否则那目录永远是空的，而"空"和"坏了"长得一样）',
  /* ⚠ 两条失败路径**都要出声**：① 脚本跑了但说"没成功" ② 起进程/调用本身抛了。
     只钉一条的话，另一条被删掉照样绿（"只查标题在不在"的翻版）。
     ⇒ 两条路现在都写成 `+ why`，靠正则分不出来了 ⇒ 数**条数**：
       同一句 toast 至少 2 次、`_indexFailed.set(` 至少 2 次。 */
  (storeSrc.match(/预览小图没生成：/g) || []).length >= 2 &&
    (storeSrc.match(/_indexFailed\.set\(/g) || []).length >= 2, '',
  '只 catch 不报 ⇒ 用户看到的就是一个空主题');
check('★★★ 失败过也**不许静默挡住**：自动那条路拦，用户点的那条路必须重试',
  /if \(_indexFailed\.has\(key\) && !force\) return false;/.test(storeSrc), '',
  '不带 `&& !force` ⇒ 再点那个文件夹一声不吭、什么都不发生（"点了没反应"那一类）');
check('★★ 记住的是**失败原因**（Map<路径,原因>），不是光记"失败过"；成功要清掉',
  /const _indexFailed = new Map<string, string>\(\);/.test(storeSrc) &&
    /_indexFailed\.delete\(key\)/.test(storeSrc), '',
  '只记布尔 ⇒ 第二次被挡住时说不出为什么；成功不清 ⇒ 环境补好了也不会再试');
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
/* ★ 09-15 SV 选「B」：库内 / 库外那条分界没有了 ⇒ 原来"库内不许开 allowRawOnly"
   这条已经**没有对象**了。换成盯**新**的形态：两层模型不许偷偷回来。
   （直接删掉就成了"没护栏"，而这条恰恰是 SV 骂"点了没反应"的根因所在。） */
check('★★ `scanSessions` 只剩"加过的文件夹"这一条来源（两层模型不许偷偷回来）',
  /function scanSessions\(\)/.test(mainJsCode) &&
    !/function scanSessions\(libRoot\)/.test(mainJsCode) &&
    !/readdirSync\(libRoot/.test(mainJsCode), '',
  '两层模型回来 ⇒ "我手上就是一个文件夹"那个人又被锁死（09-15 SV 骂的那次）');
check('⚠ 布局自检 mock 必须实现 `extIndex` + `onExtIndexProgress`（「加入目录」会调）',
  /extIndex: async \(srcDir\) =>/.test(mockSrcFlat) &&
    /onExtIndexProgress: \(cb\) =>/.test(mockSrcFlat), '',
  '漏了它 ⇒ 点那一刻同步抛 TypeError，而"到底拿哪个目录去建索引"永远测不到');
check('★★ mock 的 `scanSessions` 要能给出「只有 RAW 的目录」（path→**源目录**、带待建标记）',
  /e\.indexDir = /.test(mockSrcFlat) && /e\.needsIndex = !window\.__indexBuilt;/.test(mockSrcFlat) &&
    !/e\.srcDir = d;/.test(mockSrcFlat), '',
  'mock 不给这种条目 ⇒ 真浏览器那组整段是空转；`srcDir` 又冒出来 ⇒ 两层模型偷偷回来了');

/* ---------- [14.5] 归档产出的「相机直出件」 ----------
   ★★ 归档（星 ≥ 1）会把 root 里那张**相机直出 JPG** 拷进星级桶，给 LR 精选用。
   ⚠ 09-15 前 = 直接 `copyFileSync(path.join(themePath, rel), out)`。
     纯 RAW 目录的 `themePath` 指向**预览缓存** ⇒ 拷进桶里的是 **1600 缩略图**
     （拿它当"直出件"是错的，进 LR 一放大就糊）；原图只有 RAW 的更糟，直接报"root 原图不存在"。
   ⇒ 现在 = ① root 里真有同名 JPG 就拷它（最快）② 否则从同名 RAW 抠**全尺寸**机内 JPG。
   ⚠ 这一组**真跑函数**（只看函数名在不在挡不住"参数写错"，等于没查）：
     抠 RAW 那条路**必须**验 `--max-side=0` —— 漏了它就是 1600，而那正是这次要修的 bug。 */
console.log('\n[14.5] 归档 · 相机直出件');
{
  const soM = mainJsCode.match(/async function makeStraightOut\(themePath, rel, outFile\)\s*\{[\s\S]*?\n\}/);
  check('main.js 里有 makeStraightOut（归档"直出件"的唯一出处）', !!soM, '',
    '函数没了 ⇒ 归档退回"直接拷 root 里那个同名文件" ⇒ 纯 RAW 目录拷进桶里的是 1600 缩略图');
  let so = null;
  const pyCalls = [];
  if (soM && rexM) {
    try {
      const RAWE3 = new Function('return ' + rexM[1])();
      const fakeSpawn = async (py, script, args) => {
        pyCalls.push(args.slice());
        const hit = args.find((a) => a.startsWith('--out-file='));
        if (hit) {
          const f = hit.slice('--out-file='.length);
          fs.mkdirSync(path.dirname(f), { recursive: true });
          fs.writeFileSync(f, 'FROM-RAW');
        }
        return { text: 'ok' };
      };
      so = new Function('fs', 'path', 'RAW_EXT', 'spawnPy', 'extIndexPy', 'extIndexScriptPath',
        soM[0] + '; return makeStraightOut;'
      )(fs, path, RAWE3, fakeSpawn, () => 'PY', () => 'SCRIPT');
    } catch (e) { so = null; }
  }
  check('makeStraightOut 能在 Node 里独立跑起来（可单测）', typeof so === 'function', '', '取出来那段跑不了');
  if (typeof so === 'function') {
    const sroot = fs.mkdtempSync(path.join(os.tmpdir(), 'svso-'));
    const rd = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch (e) { return '(读不到)'; } };
    /* ① root 里既有 A.JPG 又有 A.RAF ⇒ 拷 JPG，**不许**去起 python。 */
    fs.writeFileSync(path.join(sroot, 'A.JPG'), 'CAMERA-JPG');
    fs.writeFileSync(path.join(sroot, 'A.RAF'), 'RAW');
    const o1 = path.join(sroot, '_out', 'A.JPG');
    const r1 = await so(sroot, 'A.JPG', o1);
    check('★ root 里有同名 JPG ⇒ 直接拷它（不走抠 RAW 那条路）',
      r1 && r1.ok === true && r1.from === 'jpg' && rd(o1) === 'CAMERA-JPG' && pyCalls.length === 0,
      JSON.stringify(r1),
      '有 JPG 还去起 python ⇒ 每张多等几秒；拷错源 ⇒ 桶里放的不是相机直出');
    /* ② 只有 B.RAF（连 B.JPG 都没有）⇒ 从 RAW 抠，而且必须是**全尺寸**。 */
    fs.writeFileSync(path.join(sroot, 'B.RAF'), 'RAW');
    const o2 = path.join(sroot, '_out', 'B.JPG');
    const r2 = await so(sroot, 'B.JPG', o2);
    const last = pyCalls.slice(-1)[0] || [];
    check('★★★ root 只有同名 RAW ⇒ 从 RAW 抠一份出来（不再报"root 原图不存在"）',
      r2 && r2.ok === true && r2.from === 'raw' && rd(o2) === 'FROM-RAW', JSON.stringify(r2),
      '只有 RAW 就出不了归档 ⇒ 纯 RAW 目录的星级桶永远是空的');
    check('★★★ 抠的是**全尺寸**（`--max-side=0`）—— 不许把 1600 缩略图当"直出件"',
      last.some((a) => a === '--max-side=0'), JSON.stringify(last),
      '漏了 `--max-side=0` ⇒ 拷进桶里的是 1600 缩略图，进 LR 一放大就糊，而且一句错都不报');
    check('★ 抠 RAW 时喂的是**同名 RAW 的原件路径**（不是 `themePath/rel` 那个不存在的文件）',
      last.some((a) => a === '--one=' + path.join(sroot, 'B.RAF')), JSON.stringify(last),
      '喂错源 ⇒ 抠不出图，或者抠到别的片');
    /* ③ 既没有 JPG 也没有 RAW ⇒ 老实说失败（不许静默产出一个空件）。 */
    const r3 = await so(sroot, 'C.JPG', path.join(sroot, '_out', 'C.JPG'));
    check('★ root 里既没有同名 JPG、也没有同名 RAW ⇒ 报失败（不静默产出空件）',
      r3 && r3.ok === false && !!r3.error, JSON.stringify(r3),
      '静默拷个空文件进桶 ⇒ 精选时才发现少了片');
    try { fs.rmSync(sroot, { recursive: true, force: true }); } catch (e) { /* ok */ }
  }
}

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
