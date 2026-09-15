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
  /base: dfltName, params: \{\}/.test(storeCode), '',
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
