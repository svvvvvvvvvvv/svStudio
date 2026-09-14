const $ = (s) => document.querySelector(s);

let config = null;
let sessions = [];
let photos = [];
let curSession = null;
let idx = -1;
let filter = 'all';
let ratings = {};
/* 'pick' = 选片台（右栏只读照片参数）| 'grade' = 调色台（右栏调真卷与参数） */
let mode = 'pick';

/* ---------- utils ---------- */
function toast(msg, ms = 2000) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.classList.add('hidden'), ms);
}
function busy(on, text) {
  const o = $('#overlay');
  if (on) {
    $('#overlayText').textContent = text || '处理中…';
    o.classList.remove('hidden');
  } else {
    o.classList.add('hidden');
  }
}

/* ---------- boot ---------- */
async function boot() {
  config = await window.api.getConfig();
  ratings = config.ratings || {};
  mode = config.mode === 'grade' ? 'grade' : 'pick';
  renderLibPath();
  await loadSessions();
  bindUI();
  applyMode(mode);        // 先定模式（右栏/底栏/大图区按模式摆好）
  if (mode === 'grade') gradeBoot();   // 调色台：拉引擎的卷/基准/参数清单
  // 自动恢复：上次离开的场次 + 照片 + 筛选档（如还有效）
  if (config.lastSession && config.lastSession.name) {
    const sessIdx = sessions.findIndex((s) => s.name === config.lastSession.name);
    if (sessIdx >= 0) {
      enterSession(sessIdx, { resume: true });
      return;
    }
  }
}

function renderLibPath() {
  $('#libPath').textContent = config.libRoot || '未设置';
}

async function loadSessions() {
  sessions = await window.api.scanSessions(config.libRoot);
  renderSessions();
  if (!sessions.length) {
    toast('照片库里没有找到主题（库根下需有含图片的主题目录）', 4200);
  }
}

function renderSessions() {
  const box = $('#sessionList');
  box.innerHTML = '';
  sessions.forEach((s, i) => {
    const d = document.createElement('div');
    d.className = 'sess-item';
    const mmdd = s.name.slice(0, 10);
    d.innerHTML =
      '<div class="sess-name">' + esc(s.name) + '</div>' +
      '<div class="sess-meta">' + s.count + ' 张' +
      (s.hasRaw ? ' · RAW' : '') + '</div>';
    d.onclick = () => enterSession(i);
    box.appendChild(d);
  });
}

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

/* ---------- session ---------- */
async function enterSession(i, opts) {
  curSession = sessions[i];
  const silent = opts && opts.silent;
  // 恢复上次状态：若进入的就是上次离开的场次，用 lastIdx + lastFilter；
  // 否则视为新切换，从 0 / all 开始。
  const isResume = opts && opts.resume;
  idx = isResume && typeof config.lastIdx === 'number' && config.lastIdx >= 0 ? config.lastIdx : 0;
  filter = isResume && config.lastFilter ? config.lastFilter : 'all';
  _exifCache.clear();   // 换场：清 EXIF 缓存，防膨胀
  _prefetch.clear();
  _gGen++;              // 换场：作废在飞的渲染结果
  gLoaded = null;
  if (!silent) busy(true, '加载照片…');
  photos = await window.api.listPhotos(curSession.path);
  if (!silent) busy(false);
  // listPhotos 后 idx 可能超出新库长度（若配置跨场次残留）
  if (idx >= photos.length) idx = 0;
  if (!photos.length) {
    toast('该主题没有可显示的图片');
    return;
  }
  $('#homeView').classList.add('hidden');
  $('#pickView').classList.remove('hidden');
  document.querySelectorAll('.sess-item').forEach((el, k) => {
    el.classList.toggle('active', k === i);
  });
  $('#sessInfo').textContent = curSession.name + ' · ' + photos.length + ' 张';
  // 调色台：换主题 → 载入这个主题自己的那套参数（按主题存）
  grade = gradeOf(curSession.name);
  if (mode === 'grade' && gStocks.length) {
    $('#gStock').value = grade.stock;
    $('#gBase').value = grade.base;
    paintStockDesc();
    paintBaseDesc();
    paintGradeParams();
    renderRenderHint();
  }
  renderFilters();
  renderDock();
  showAt(idx);
  updateArchiveBtn();
  // 持久化：记录当前场次（lastIdx/lastFilter 已通过 showAt/chip 单独保存）
  window.api.setConfig({ lastSession: { name: curSession.name } });
}

/* ---------- filters ---------- */
const FILTERS = [
  { k: 'all', label: '全部' },
  { k: 'star', label: '★有星' },
  { k: '5', label: '★★★★★' },
  { k: '4', label: '★★★★' },
  { k: '3', label: '★★★' },
  { k: '2', label: '★★' },
  { k: '1', label: '★' },
  { k: 'unrated', label: '未评' }
];

function renderFilters() {
  const box = $('#filterChips');
  box.innerHTML = '';
  FILTERS.forEach((f) => {
    const c = document.createElement('div');
    c.className = 'chip' + (filter === f.k ? ' active' : '');
    c.textContent = f.label;
    c.onclick = () => {
      filter = f.k;
      renderFilters();
      // 重建 dock，使其只包含当前筛选档的卡片
      renderDock();
      // 跳到档内第一张
      const list = visibleList();
      if (list.length) showAt(list[0]);
      else toast('该筛选下没有照片');
      // 持久化筛选档
      window.api.setConfig({ lastFilter: filter });
    };
    box.appendChild(c);
  });
}

function matchFilter(v) {
  if (filter === 'all') return true;
  if (filter === 'star') return v >= 1;
  if (filter === 'unrated') return v === undefined || v === null;
  const n = parseInt(filter, 10);
  return v === n;
}

function visibleList() {
  const out = [];
  photos.forEach((p, i) => {
    if (matchFilter(ratings[curKey(p)])) out.push(i);
  });
  return out;
}

function curKey(p) {
  return curSession.name + '||' + p.name;
}

/* ---------- dock ---------- */
let _thumbToken = 0;

/* 缩略图按需加载（性能核心）：
   744 张如果全量触发解码+IPC 会卡死（实测 27s+）。
   用 IntersectionObserver 只让"滚进视口的缩略图"请求主进程解码，
   从解码 744 张降到只解码可见的十几张 —— 首屏毫秒级，滚动时增量填充。
   主进程侧再配串行队列 + 每张让出事件循环，UI 全程不冻结。 */
let _thumbObserver = null;

function renderDock() {
  const dock = $('#dock');
  dock.innerHTML = '';
  _thumbToken++;

  // 每换一次场次/筛选，重建 observer（旧元素已销毁，须重新挂载）
  if (_thumbObserver) _thumbObserver.disconnect();
  _thumbObserver = new IntersectionObserver(
    (entries) => {
      // 一次只处理进入视口的项
      for (const ent of entries) {
        if (!ent.isIntersecting) continue;
        const img = ent.target;
        _thumbObserver.unobserve(img);
    const rel = img.dataset.rel;
    if (rel && curSession) {
      const dir = img.dataset.dir || curSession.path;
      window.api
        .getThumb(dir, rel, 260)
        .then((d) => {
              if (!d || !d.url || !img.isConnected) return;
              // 用图本身解码后的 natural 尺寸为准，反推真实"显示方向"比例。
              // 原因：相机竖拍 JPG 物理常是横(靠 EXIF Orientation 转正)，
              //       dataURL 若经系统缩略已转正→naturalWidth 竖；若主进程给横 dim
              //       也以图实际方向为准，杜绝"先横后竖"闪一下。
              img.onload = () => {
                if (img.naturalWidth > 0 && img.naturalHeight > 0)
                  _dimCache.set(rel, { ow: img.naturalWidth, oh: img.naturalHeight });
              };
              img.src = d.url;
            })
            .catch(() => {});
        }
      }
    },
    { root: dock, rootMargin: '0px 300px' }
  );

  const items = [];
  photos.forEach((p, i) => {
    const v = ratings[curKey(p)];
    if (!matchFilter(v)) return;
    items.push({ p, i, v });
  });

  // 建占位骨架（不加载图片），744 张也能瞬间渲染
  items.forEach((it) => {
    const d = document.createElement('div');
    const curSub = (it.p.dir || curSession.path).split(/[\\/]/).pop();
    d.className = 'dk' + (it.p.archived ? ' done-arch' : '');
    d.dataset.i = it.i;
    const img = document.createElement('img');
    img.alt = '';
    img.dataset.rel = it.p.rel;   // observer 回调据此请求缩略图
    img.dataset.dir = it.p.dir || curSession.path; // 真实目录（源/星级桶）
    img.loading = 'lazy';
    d.appendChild(img);
    if (it.p.hasRaw) {
      const r = document.createElement('div');
      r.className = 'raw';
      r.textContent = 'RAW';
      d.appendChild(r);
    }
    if (it.p.archived) {
      const a = document.createElement('div');
      a.className = 'arch';
      a.textContent = curSub === REVIEW_DIR ? '待验收' : '已归档';
      d.appendChild(a);
    }
    if (it.v >= 1) {
      const s = document.createElement('div');
      s.className = 'st';
      s.textContent = '★'.repeat(it.v);
      d.appendChild(s);
    }
    d.onclick = () => showAt(it.i);
    dock.appendChild(d);
    _thumbObserver.observe(img); // 交给 observer，进入视口才解码
  });

  paintDockStatus();
}

function paintDockStatus() {
  document.querySelectorAll('.dk').forEach((el) => {
    el.classList.toggle('cur', parseInt(el.dataset.i, 10) === idx);
  });
}

function centerDock(i) {
  const el = document.querySelector('.dk[data-i="' + i + '"]');
  if (el && el.scrollIntoView) {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
}

/* ---------- viewer（双图交叉淡入，消除切图闪烁） ---------- */
let _bigCur = 'A';   // 当前显示在顶层的图（A 或 B）
async function showAt(i) {
  if (i < 0 || i >= photos.length) return;
  idx = i;
  const p = photos[i];
  const url = await window.api.readImage(p.dir || curSession.path, p.rel);
  // 选择非活动的那一层作为新图层，活动那层是旧的（要被淡出）
  const newId = _bigCur === 'A' ? 'B' : 'A';
  const oldId = _bigCur;
  const oldImg = $('#bigImg' + oldId);
  const newImg = $('#bigImg' + newId);
  if (url) {
    newImg.src = url;
    // 等到新图像素 ready（fire-and-forget 不阻塞下面的渲染）：
    // 旧图层保持 .show 可见，新图层在底部等 decode。一旦 ready → 加 .show，
    // 旧图层同时 remove .show → CSS transition 同时淡出/淡入，cross-fade。
    try {
      await newImg.decode();
    } catch (e) { /* 解码失败也淡入，至少有图 */ }
    // 同一帧内做切换，让 transition 真正生效（不 microtask 推迟）
    newImg.classList.add('show');
    oldImg.classList.remove('show');
    _bigCur = newId;
  } else {
    // url 为空：什么都不做（旧图保留）
  }
  $('#exifBar').textContent = p.name + (p.hasRaw ? '  ⟡ RAW' : '');
  renderStars();
  paintDockStatus();
  centerDock(i);
  prefetchNeighbors(i);
  // EXIF 右栏：当前懒读渲染 + 空闲预取相邻
  paintParams(p);
  // 调色台：换图就换原图 + 重新出图（左原图 / 右渲染）
  if (mode === 'grade') {
    $('#gPhoto').textContent = p.name;
    prepareGradeImage();
  }
  [1, -1].forEach((d) => {
    const k = i + d;
    if (k >= 0 && k < photos.length) warmExif(photos[k]);
  });
  // 持久化：记住"现在在哪张照片"
  window.api.setConfig({ lastIdx: idx });
}

/* 相邻图片预解码：让浏览器提前 decode 前后各 1 张，翻图时几乎无等待。
   注意内存权衡：24MP 图解码后约 50MB/张，因此只预载 ±1（约 100MB），
   不预载更多，避免内存膨胀。用隐藏 Image 异步解码，不打断当前显示。 */
/* 相邻图片预解码：让浏览器提前 decode 前后各 1 张，翻图时几乎无等待。
   用隐藏 Image + decode() 真解码（不仅 src 赋值），让 img.decode() 在 showAt 中
   await 时能几乎瞬时 resolve，避免大图切换的"暗帧"。注意内存权衡：24MP 解码约
   50MB/张，因此只预载 ±1（约 100MB），不预载更多，避免内存膨胀。 */
const _prefetch = new Set();
function prefetchNeighbors(i) {
  const offs = [1, -1];
  offs.forEach((d) => {
    const k = i + d;
    if (k < 0 || k >= photos.length) return;
    const key = curSession.name + '||' + photos[k].name;
    if (_prefetch.has(key)) return;
    _prefetch.add(key);
    const im = new Image();
    im.decoding = 'async';
    im.src = 'file:///' + ((photos[k].dir || curSession.path) + '/' + photos[k].name).replace(/\\/g, '/');
    // 真解码（fire-and-forget，结果仅供浏览器复用解码缓存）
    im.decode().catch(() => { /* ignore */ });
  });
}

function renderStars() {
  const row = $('#starRow');
  row.innerHTML = '';
  const p = photos[idx];
  const v = ratings[curKey(p)] || 0;
  for (let n = 1; n <= 5; n++) {
    const s = document.createElement('div');
    s.className = 'star' + (n <= v ? ' on' : '');
    s.textContent = '★';
    s.onclick = () => rate(n);
    row.appendChild(s);
  }
  const clr = document.createElement('div');
  clr.className = 'star';
  clr.textContent = '✕';
  clr.style.fontSize = '13px';
  clr.title = '清除评分 (0)';
  clr.onclick = () => rate(0);
  row.appendChild(clr);
}

function rate(v) {
  if (idx < 0) return;
  const p = photos[idx];
  const k = curKey(p);
  // ★ 0 星必须**写进 ratings**，不能 delete（2026-09-12 修）：
  //   原先 delete 掉 → selectedList() 的 `!== undefined` 把这张整个漏掉 →
  //   主进程收不到这条 → 「初筛1星」里的复制件永远删不掉（实测：降 13 张，目录里 45 张纹丝不动）。
  //   0 的含义是「已看过 · 主动略过」，是一个**要存档的状态**（hover 面板本来就有 '0 · 略过' 这一档）。
  ratings[k] = v;
  window.api.saveRatings(ratings);
  scheduleAutoPlace();   // 防抖自动同步星级目录
  renderStars();
  // 关键：打分后当前片可能脱离当前筛选档（如 ★有星/1星档打 0、未评档打分）。
  // 脱离 → dock 重建剔除该卡，并跳到档内"当前之后"第一张（无则回档首）。
  const newVal = v;
  const stillVisible = matchFilter(newVal);
  if (!stillVisible) {
    renderDock();
    updateArchiveBtn();
    const list = visibleList();
    let next = list.find((i) => i > idx);
    if (next === undefined && list.length) next = list[0];
    if (next !== undefined) showAt(next);
    else { toast('该筛选下没有照片了'); goHome(); }
    return;
  }
  // 仍在档内（all 档打分 / 同档改分）：只更新角标，不重建 dock
  updateDockBadge(idx, p, v);
  updateArchiveBtn();
  nextAfterRate();
}

function updateDockBadge(i, p, v) {
  const d = document.querySelector('.dk[data-i="' + i + '"]');
  if (!d) return;
  const old = d.querySelector('.st');
  if (old) old.remove();
  if (v >= 1) {
    const s = document.createElement('div');
    s.className = 'st';
    s.textContent = '★'.repeat(v);
    d.appendChild(s);
  }
}

/* ---------- EXIF 右栏参数 ---------- */
const _exifCache = new Map(); // key -> {v: exifObj}  (进入场次时整体重建)
const PARAM_ROWS = [
  ['file', '文件'], ['size', '尺寸'], ['camera', '相机'], ['lens', '镜头'],
  ['focal', '焦段'], ['aperture', '光圈'], ['shutter', '快门'], ['iso', 'ISO'],
  ['bias', '曝光补偿'], ['flash', '闪光'], ['wb', '白平衡'], ['k', '色温'],
  ['time', '拍摄时间']
];

function keyForExif(p) { return curSession.name + '||' + p.name; }

async function ensureExif(p) {
  const k = keyForExif(p);
  if (_exifCache.has(k)) return _exifCache.get(k);
  try {
    const ex = await window.api.getExif(p.dir || curSession.path, p.rel) || {};
    _exifCache.set(k, ex);
    return ex;
  } catch (e) {
    return {};
  }
}

/* 渲染右栏参数：仅更新当前图（每次 showAt 调用） */
async function paintParams(p) {
  const list = $('#paramList');
  const nameEl = document.createElement('div'); // 先清空
  list.innerHTML = '';
  // 第一行文件名
  const first = document.createElement('div');
  first.className = 'p-row';
  first.innerHTML = '<span class="k">名称</span><span class="v hl" title="' + esc(p.name) + '">' + esc(p.name) + '</span>';
  list.appendChild(first);
  // 参数占位（EXIF 未就绪先显示 …）
  const rows = {};
  PARAM_ROWS.forEach(([k, label]) => {
    const r = document.createElement('div');
    r.className = 'p-row';
    r.dataset.k = k;
    r.innerHTML = '<span class="k">' + label + '</span><span class="v">…</span>';
    list.appendChild(r);
    rows[k] = r.querySelector('.v');
  });
  const ex = await ensureExif(p);
  // 若翻图了就不再写旧结果
  const cur = photos[idx];
  if (!cur || keyForExif(cur) !== keyForExif(p)) return;
  PARAM_ROWS.forEach(([k]) => {
    const v = ex[k];
    const el = rows[k];
    if (!el) return;
    if (v === undefined || v === null || v === '') el.textContent = '—';
    else el.textContent = v;
  });
  // 略过标记：0 星显示在 star legend 由角标体现
}

/* EXIF 懒读 + 预取相邻（进入当前后空闲读前后 2 张） */
function warmExif(p) { ensureExif(p); }

function nextAfterRate() {
  const list = visibleList();
  const pos = list.indexOf(idx);
  if (pos >= 0 && pos < list.length - 1) {
    showAt(list[pos + 1]);
  } else if (list.length) {
    // 到末尾，尝试全局下一张未评
    const un = photos.findIndex((p, i) => i > idx && ratings[curKey(p)] === undefined);
    if (un >= 0 && filter === 'all') showAt(un);
  }
}

function updateArchiveBtn() {
  const n = selectedList().length;
  $('#archCount').textContent = n;
  $('#btnArchive').disabled = n === 0;
  $('#btnResetColor').disabled = !curSession;
}

/* ---------- 星级目录（与 main.js 常量镜像，改名两处同步） ---------- */
const STAR1_DIR = '初筛1星';
const STAR2_DIR = '调色后满意的2星';
const PUBLISH_DIR = '待发布';
const REVIEW_DIR = '调色待验收';
const THEME_SUBDIRS = [STAR1_DIR, STAR2_DIR, PUBLISH_DIR, REVIEW_DIR];
/** 星级语义标签 */
function starLabel(v) {
  if (v >= 5) return '神图';
  if (v === 4) return '超级满意';
  if (v === 3) return '可发社媒';
  if (v === 2) return '调色满意';
  return '需调色';
}

function selectedList() {
  if (!curSession) return [];
  // ★累积桶模型：同步=全量幂等对账。主进程逐桶补齐/削减（复制/删除，绝不移动），
  //   已正确的片毫秒级跳过，全量跑无压力。前端无法感知低桶缺件，故全交主进程判定。
  //   未评（undefined）不动；0 星=主动略过 → 清桶；root 原图永不动。
  return photos.filter((p) => ratings[curKey(p)] !== undefined);
}

/* ---------- dock hover 大预览 ---------- */
let _hpTimer = null;       // 防抖：鼠标短暂停留才弹
let _hpRel = null;         // 当前预览的 rel，防止异步错位
const _dimCache = new Map();   // rel -> {ow, oh}，按需拉取并缓存，避免重复 IPC
function hpEl() { return $('#hoverPrev'); }
function hpImg() { return $('#hpImg'); }

function dockMouseMove(ev) {
  const dk = ev.target.closest ? ev.target.closest('.dk') : null;
  if (!dk) { hideHoverPrev(); return; }
  const i = parseInt(dk.dataset.i, 10);
  const p = photos[i];
  if (!p) return;
  // 移动到新卡片：防抖 200ms 再弹，避免快速划过闪烁
  clearTimeout(_hpTimer);
  _hpTimer = setTimeout(() => {
    if (idx === i) { hideHoverPrev(); return; }  // 当前大图即此，无需预览
    showHoverPrev(dk, i, p);
  }, 160);
}

/* 计算 box 尺寸 + 定位（纯函数，可同步或异步拿尺寸后调用）
 * 尺寸策略（"既铺满又不爆框"）：
 *   软上限：高 ≤ 720、宽 ≤ 540（这两个是手感甜点，再大就过头，参见截图证据）。
 *   硬上限：高 ≤ dock 上方实际空隙、宽 ≤ 窗口内宽 - 24（防盖顶栏/左栏）。
 *   二者取 min：保证在任何窗口大小/分辨率下都不会"撑爆"。
 *   在此矩形内按原图比例 contain，保证竖图顶高、横图顶宽，且不出现黑边。 */
function _layoutHover(dk, dim) {
  const box = hpEl();
  const dock = $('#dock');
  const dRect = dock.getBoundingClientRect();
  const SOFT_MAX_W = 720;   // 宽屏甜点：横图更饱满
  const SOFT_MAX_H = 720;   // 竖图甜点：竖图更高更显细节
  const HARD_MAX_H = Math.max(180, dRect.top - 12);    // dock 上方实际空隙
  const HARD_MAX_W = Math.max(160, window.innerWidth - 24);
  const maxH = Math.min(SOFT_MAX_H, HARD_MAX_H);
  const maxW = Math.min(SOFT_MAX_W, HARD_MAX_W);
  const MIN_W = 200, MIN_H = 200;

  let bw, bh;
  if (dim && dim.ow > 0 && dim.oh > 0) {
    const r = dim.ow / dim.oh;   // 宽/高，竖图 r<1
    // contain：在 (maxW, maxH) 矩形内尽量大，按比例
    bw = maxH * r;
    bh = maxH;
    if (bw > maxW) { bw = maxW; bh = maxW / r; }
    if (bh > maxH) { bh = maxH; bw = maxH * r; }
    // 下限保护
    if (bw < MIN_W) {
      bw = MIN_W;
      bh = bw / r;
      if (bh > maxH) bh = maxH;
    }
    if (bh < MIN_H) {
      bh = MIN_H;
      bw = bh * r;
      if (bw > maxW) bw = maxW;
    }
    bw = Math.round(bw);
    bh = Math.round(bh);
  } else {
    // dim 还没到，先按 3:2 横图估位（与 CSS 默认一致，避免首帧塌缩）
    const r = 3 / 2;
    bw = Math.min(maxW, Math.round(maxH * r));
    bh = Math.round(bw / r);
    if (bh > maxH) { bh = maxH; bw = Math.round(bh * r); }
    // 估位也尽量大：横图 720×540
    if (bw < 540 && maxW >= 540) bw = 540, bh = 360;
  }
  const totalH = bh + 38;
  box.style.width = bw + 'px';
  box.style.height = totalH + 'px';
  const imgEl = hpImg().parentElement;
  imgEl.style.width = bw + 'px';
  imgEl.style.height = bh + 'px';
  // 定位：水平跟卡片中心，垂直贴 dock 内容顶
  const rk = dk.getBoundingClientRect();
  let x = rk.left + rk.width / 2 - bw / 2;
  x = Math.max(10, Math.min(x, window.innerWidth - bw - 10));
  let y = dRect.top - totalH + 6;
  if (y < 6) y = 6;
  box.style.left = x + 'px';
  box.style.top = y + 'px';
}

function showHoverPrev(dk, i, p) {
  const box = hpEl();
  _hpRel = p.rel;
  // 文件名/星级
  $('#hpName').textContent = p.name + (p.hasRaw ? ' ⟡RAW' : '');
  const v = ratings[curKey(p)] || 0;
  const starEl = $('#hpStar');
  if (v >= 1) {
    starEl.className = 'hp-star';
    starEl.textContent = '★'.repeat(v) + ' · ' + starLabel(v);
  } else if (v === 0) {
    starEl.className = 'hp-star skip';
    starEl.textContent = '0 · 略过';
  } else {
    starEl.className = 'hp-star empty';
    starEl.textContent = '未评';
  }
  // 解除上一轮 hide 退场态；无 .show → opacity 0，box 此刻不可见
  box.classList.remove('hidden');
  box.classList.remove('show');
  // 清掉旧图，显 loading 文字；框保持 CSS 默认小尺寸（不猜横竖）
  const im = hpImg();
  im.removeAttribute('src');
  const ph = $('#hpPh');
  ph.style.display = 'flex';
  ph.textContent = '加载中…';
  _layoutHover(dk, null);   // 同步把框定位到 dock 上方（小 loading 框，方向中性）

  // ── 方向权威来源：file:// 原图（浏览器解码自动应用 EXIF Orientation）。
  //    大图区 showAt 用同一 readImage + file:// 解码，方向 100% 正确（用户从未报大图横竖错）。
  //    dock 系统缩略虽方向对，但 dock 卡 hover 时未必已 decode，不依赖它。
  // 入场时机 = decode 完成后：用 natural 尺寸一次 layout + add('show')，入场框即最终框，
  //    同方向无二次修正 → 无横竖翻转。CSS 已去掉 width/height transition，尺寸瞬间到位。
  let settled = false;   // 防止 decode 与超时兜底双弹
  const finish = (dim) => {
    if (settled || _hpRel !== p.rel || box.classList.contains('hidden')) return;
    settled = true;
    if (dim && dim.ow > 0 && dim.oh > 0) {
      _dimCache.set(p.rel, { ow: dim.ow, oh: dim.oh });
      _layoutHover(dk, { ow: dim.ow, oh: dim.oh });   // 按真实方向定框
    } else {
      _layoutHover(dk, null);                         // 图失败：保持 loading 估框
      ph.textContent = '无预览';
    }
    ph.style.display = 'none';
    box.classList.add('show');                        // 淡入（入场即正确方向）
  };

  im.onload = () => finish({ ow: im.naturalWidth, oh: im.naturalHeight });
  im.onerror = () => finish(null);

  window.api.readImage(p.dir || curSession.path, p.rel).then((url) => {
    if (url && _hpRel === p.rel) im.src = url; else finish(null);
  }).catch(() => finish(null));

  // 超时兜底：file 原图 decode 极慢/失败也弹（多数命中大图解码缓存，decode 即时）
  setTimeout(() => finish(_dimCache.get(p.rel) || null), 800);
}

function hideHoverPrev() {
  clearTimeout(_hpTimer);
  const box = hpEl();
  box.classList.add('hidden');
  box.classList.remove('show');
  _hpRel = null;
}

/* 回到当前选中：从筛选视图/浏览位置跳到当前 idx 并居中 */
function goCenter() {
  if (!curSession || idx < 0) return;
  centerDock(idx);
}

/* ---------- 星级目录同步（自动归位；root 原图永不移动/删除） ---------- */
let _syncTimer = null;
/** 打星后防抖自动同步（"其他线程"：升级/降级移动、清星删副本） */
function scheduleAutoPlace() {
  clearTimeout(_syncTimer);
  _syncTimer = setTimeout(() => { syncPlacement(true); }, 900);
}

async function syncPlacement(silent) {
  if (!curSession) return;
  const sel = selectedList();
  if (!sel.length) return;
  const items = sel.map((p) => ({
    rel: p.rel,
    star: ratings[curKey(p)] || 0
  }));
  if (!silent) busy(true, '同步 ' + items.length + ' 张…');
  const res = await window.api.archivePhotos({ themePath: curSession.path, items });
  if (!silent) busy(false);
  if (!res.ok) {
    toast('同步失败: ' + res.error, 4000);
    return;
  }
  if (res.failed && res.failed.length) {
    toast(res.failed.length + ' 张同步失败: ' + res.failed[0].file + ' ' + res.failed[0].error, 4500);
  } else if (!silent) {
    toast('已同步 ' + res.done + ' 张（按星级归位）', 3600);
  }
  // 静默刷新（保位置）：记下当前 idx/filter 后 resume 恢复；dock 形态随同步切换
  config = await window.api.setConfig({ lastIdx: idx, lastFilter: filter });
  await loadSessions();
  const i = sessions.findIndex((s) => s.name === curSession.name);
  if (i >= 0) await enterSession(i, { resume: true, silent });
}

/* ---------- bind ---------- */
function bindUI() {
  /* 顶栏模式切换：选片台 ｜ 调色台。
     只切右栏内容 + 中间大图显示源（分屏），左栏图库/底部缩略图/快捷键全部复用。 */
  $('#modeTabs').querySelectorAll('.mtab').forEach((b) => {
    b.onclick = () => { if (b.dataset.mode !== mode) applyMode(b.dataset.mode); };
  });

  /* ---- 调色台控件 ---- */
  $('#gStock').onchange = () => {
    grade.stock = $('#gStock').value;
    paintStockDesc();
    gradeSave();
    renderRenderHint();
    scheduleGradeRender(0);        // 换卷必然要重出（引擎侧换卷 6~7 秒）
  };
  $('#gBase').onchange = () => {
    grade.base = $('#gBase').value;
    paintBaseDesc();
    gradeSave();
    scheduleGradeRender(0);
  };
  $('#gReset').onclick = async () => {
    if (!curSession) return;
    const okGo = await window.api.confirmDialog({
      title: '恢复默认',
      okLabel: '恢复',
      message: '把「' + curSession.name + '」的调色参数恢复成默认？',
      detail: '卷回到 ' + GRADE_DEFAULT.stock + '，基准回到 ' + GRADE_DEFAULT.base +
        '，所有滑杆回到出厂值。\n这一主题之前存的参数会被覆盖。'
    });
    if (!okGo) return;
    grade = JSON.parse(JSON.stringify(GRADE_DEFAULT));
    gradeSave();
    $('#gStock').value = grade.stock;
    $('#gBase').value = grade.base;
    paintStockDesc();
    paintBaseDesc();
    paintGradeParams();
    renderRenderHint();
    scheduleGradeRender(0);
  };
  $('#gSave').onclick = () => {
    if (!curSession) return;
    gradeSave();
    toast('已存到「' + curSession.name + '」（换主题再回来还在）', 3200);
  };
  $('#btnRender').onclick = () => { clearTimeout(_gTimer); renderGrade(); };
  $('#chkAuto').onchange = () => {
    if ($('#chkAuto').checked) scheduleGradeRender(0);
  };

  $('#btnPickLib').onclick = async () => {
    const dir = await window.api.pickDirectory();
    if (!dir) return;
    // 切库：旧库的最后位置不再适用，清掉 lastSession/lastIdx/lastFilter
    config = await window.api.setConfig({
      libRoot: dir,
      lastSession: null,
      lastIdx: -1,
      lastFilter: 'all'
    });
    renderLibPath();
    await loadSessions();
    goHome();
    toast('照片库已切换');
  };

  $('#btnHome').onclick = goHome;

  $('#btnArchive').onclick = () => syncPlacement(false);

  /* 重置调色：清调色待验收/2星/待发布三桶 JPG + 评分 >1 重置为 ★1。
     场景：换新调色思路重跑调色管线前，把旧成片/旧输出全作废。
     原图（JPG+RAF）与 初筛1星 不动；RAF 由主进程防御性只删 JPG。 */
  $('#btnResetColor').onclick = async () => {
    if (!curSession) return;
    // 统计：最新形态在三个待清桶里的张数（同名多形态计一次，真实删除数以主进程返回为准）
    const nGone = photos.filter((p) => {
      const sub = (p.dir || curSession.path).split(/[\\/]/).pop();
      return p.archived && sub !== STAR1_DIR;
    }).length;
    const nReset = photos.filter((p) => (ratings[curKey(p)] || 0) > 1).length;
    const okGo = await window.api.confirmDialog({
      title: '重置调色',
      okLabel: '确认重置',
      message: `清除「${curSession.name}」的调色结果？`,
      detail:
        `将删除 调色待验收 / 调色后满意的2星 / 待发布 里的全部 JPG（约 ${nGone} 张），` +
        `并把 ${nReset} 张 >1 星的评分重置为 ★1。\n` +
        `主题根原图（JPG+RAF）与 初筛1星 不受影响，RAW 文件一律不动。`
    });
    if (!okGo) return;
    busy(true, '重置调色…');
    const res = await window.api.resetColorGrade(curSession.path);
    if (res.ok) {
      // 评分必须与物理同步回退：否则自动同步会拿 root 直出兜底复制进 2星桶，冒充成片
      let changed = false;
      for (const p of photos) {
        const k = curKey(p);
        if ((ratings[k] || 0) > 1) { ratings[k] = 1; changed = true; }
      }
      if (changed) await window.api.saveRatings(ratings);
    }
    busy(false);
    if (!res.ok) { toast('重置失败', 4000); return; }
    if (res.errors && res.errors.length) {
      toast(res.errors.length + ' 个文件删除失败: ' + res.errors[0], 4500);
    } else {
      toast(`已重置：删 ${res.removed} 张，评分回 ★1`, 3800);
    }
    await loadSessions();
    const i = sessions.findIndex((s) => s.name === curSession.name);
    if (i >= 0) await enterSession(i, { resume: true });
  };

  // dock 悬停大预览（委托，容器不变只重建子元素）
  const dockBox = $('#dock');
  dockBox.addEventListener('mousemove', dockMouseMove);
  dockBox.addEventListener('mouseleave', hideHoverPrev);
  // 滚动/拖拽中卡片位置在变，预览会与卡片错开 → 直接收起
  dockBox.addEventListener('scroll', hideHoverPrev, { passive: true });

  // 悬停滚轮 → 横向快进（dock 原生是横向滚动，把纵向滚轮映射过去）
  dockBox.addEventListener('wheel', (e) => {
    e.preventDefault();
    dockBox.scrollLeft += e.deltaY + e.deltaX;
  }, { passive: false });

  // 拖拽滚动：按下拖动移动 scrollLeft，阈值防误触
  let _dragState = null;
  let _dragMoved = false;   // 本次是否真拖拽过（用于吞掉拖后 click）
  dockBox.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.dk')) return;   // 点卡片是选中，不拖拽
    _dragState = { startX: e.clientX, startLeft: dockBox.scrollLeft };
    _dragMoved = false;
    try { dockBox.setPointerCapture(e.pointerId); } catch (err) {}
  });
  dockBox.addEventListener('pointermove', (e) => {
    if (!_dragState) return;
    const dx = e.clientX - _dragState.startX;
    if (Math.abs(dx) > 4) _dragMoved = true;
    dockBox.scrollLeft = _dragState.startLeft - dx;
  });
  const endDrag = () => { _dragState = null; };
  dockBox.addEventListener('pointerup', endDrag);
  dockBox.addEventListener('pointercancel', endDrag);
  dockBox.addEventListener('click', (e) => {
    if (_dragMoved) {
      e.stopPropagation();
      e.preventDefault();
      _dragMoved = false;   // 只吞拖完那一下
    }
  });

  // 回到当前选中
  const bc = $('#btnCenter');
  if (bc) bc.onclick = goCenter;

  document.addEventListener('keydown', (e) => {
    if (e.key === 'c' || e.key === 'C') { goCenter(); return; }
    if (!curSession || idx < 0) return;
    if (e.key >= '1' && e.key <= '5') {
      // 调色台：数字键是渲染次数之外的「打星」太容易误触（且调色模式看不到星条）⇒ 不打星
      if (mode === 'grade') return;
      rate(parseInt(e.key, 10));
    } else if (e.key === '0') {
      if (mode === 'grade') return;
      rate(0);
    } else if (e.key === 'ArrowRight') {
      const list = visibleList();
      const pos = list.indexOf(idx);
      if (pos < list.length - 1) showAt(list[pos + 1]);
    } else if (e.key === 'ArrowLeft') {
      const list = visibleList();
      const pos = list.indexOf(idx);
      if (pos > 0) showAt(list[pos - 1]);
    }
  });
}

function goHome() {
  curSession = null;
  photos = [];
  idx = -1;
  hideHoverPrev();
  $('#pickView').classList.add('hidden');
  $('#homeView').classList.remove('hidden');
  $('#sessInfo').textContent = '';
  document.querySelectorAll('.sess-item').forEach((el) => el.classList.remove('active'));
  updateArchiveBtn();
}

/* =========================================================================
   调色台（grade）—— 与选片台共用「左栏图库 / 中间大图 / 底部缩略图 / 快捷键」，
   只多两件事：① 右栏从「只读照片参数」换成「调色的各种参数」
             ② 中间大图换成左右分屏（左原图 / 右渲染结果）

   ★ 边界：台子不做任何调色数学。参数原样丢给 svFilm 引擎（本机常驻 HTTP 服务），
     引擎吐图回来显示。台子只负责「改哪个数 → 传过去 → 显示结果 → 记住这个数」。
   ========================================================================= */

let gStocks = [];        // [{name, label, desc}] 胶片卷
let gBases = [];         // [{name, label, desc}] 成色基准
let gParamDefs = [];     // [{k, name, lo, hi, step, d}] 引擎支持的可调项
let grade = null;        // 当前主题的调色配置（按主题存）
let gFiles = [];         // 引擎 scan 出来的文件绝对路径（与 photos 一一对应）
let gLoaded = null;      // 引擎里已载入的那张 {id, path, ms}
let gRendering = false;
let _gTimer = null;      // 自动出图防抖
let _gGen = 0;           // 渲染代次：翻图后旧结果回来直接丢掉

/* 出厂默认（与引擎侧配置一致；「恢复默认」就回到这组） */
const GRADE_DEFAULT = { stock: 'portra400', base: 'BASE_FULL', params: {} };

/** 当前主题的调色配置：优先读已存的，没有就用出厂默认 */
function gradeOf(name) {
  const cfg = config.grades || {};
  const g = cfg[name];
  if (!g) return JSON.parse(JSON.stringify(GRADE_DEFAULT));
  return {
    stock: g.stock !== undefined ? g.stock : GRADE_DEFAULT.stock,
    base: g.base !== undefined ? g.base : GRADE_DEFAULT.base,
    params: Object.assign({}, GRADE_DEFAULT.params, g.params || {})
  };
}

function gradeSave() {
  if (!curSession || !grade) return;
  config.grades = config.grades || {};
  // 本地立即生效（换主题再回来不等 IPC）
  config.grades[curSession.name] = JSON.parse(JSON.stringify(grade));
  window.api.setGrade(curSession.name, grade);
}

/** 把参数拼成引擎吃的 `KEY:VAL,KEY:VAL`（只发与默认不同的那些，别把一坨默认值也发过去） */
function gradeParamsStr() {
  const out = [];
  for (const p of gParamDefs) {
    const v = grade.params[p.k];
    if (v === undefined || v === null) continue;
    out.push(p.k + ':' + v);
  }
  return out.join(',');
}

/* ---------- 调色台启动：拉引擎的卷 / 基准 / 参数清单 ---------- */
async function gradeBoot() {
  const h = await window.api.engineHealth();
  if (!h.ok) {
    $('#renderHint').textContent = '引擎没在跑 —— 点这里拉起（或看 svFilm 服务窗口）';
    $('#renderHint').style.cursor = 'pointer';
    $('#renderHint').onclick = gradeStartEngine;
    return;
  }
  await gradeLoadCatalog();
}

async function gradeStartEngine() {
  const hint = $('#renderHint');
  hint.textContent = '正在启动引擎（首次要 import 模型，几秒~几十秒）…';
  hint.onclick = null;
  hint.style.cursor = 'default';
  const r = await window.api.engineStart();
  if (!r.ok) {
    hint.textContent = '引擎启动失败：' + r.error;
    toast('引擎启动失败：' + r.error, 6000);
    return;
  }
  toast(r.started ? '引擎已启动（' + r.seconds + ' 秒）' : '引擎已在运行', 3000);
  await gradeLoadCatalog();
  renderRenderHint();
}

async function gradeLoadCatalog() {
  const [st, ba, pa] = await Promise.all([
    window.api.engineStocks(),
    window.api.engineBases(),
    window.api.engineParams()
  ]);
  gStocks = (st && st.ok && st.items) || [];
  gBases = (ba && ba.ok && ba.items) || [];
  gParamDefs = (pa && pa.ok && pa.items) || [];
  paintStockSelect();
  paintBaseSelect();
  paintGradeParams();
}

function paintStockSelect() {
  const sel = $('#gStock');
  if (!sel) return;
  sel.innerHTML = '';
  for (const s of gStocks) {
    const o = document.createElement('option');
    o.value = s.name;
    o.textContent = s.label || s.name;
    sel.appendChild(o);
  }
  if (grade) sel.value = grade.stock || GRADE_DEFAULT.stock;
  paintStockDesc();
}

function paintStockDesc() {
  const el = $('#gStockDesc');
  if (!el) return;
  const s = gStocks.find((x) => x.name === ($('#gStock').value));
  el.textContent = s ? (s.desc || '') : '';
}

function paintBaseSelect() {
  const sel = $('#gBase');
  if (!sel) return;
  sel.innerHTML = '';
  for (const b of gBases) {
    const o = document.createElement('option');
    o.value = b.name;
    o.textContent = b.label || b.name;
    sel.appendChild(o);
  }
  if (grade) sel.value = grade.base || GRADE_DEFAULT.base;
  paintBaseDesc();
}

function paintBaseDesc() {
  const el = $('#gBaseDesc');
  if (!el) return;
  const b = gBases.find((x) => x.name === $('#gBase').value);
  el.textContent = b ? (b.desc || '') : '';
}

/** 画参数滑杆（参数清单来自引擎 `/params`，台子不写死任何一项） */
function paintGradeParams() {
  const box = $('#gParams');
  if (!box) return;
  box.innerHTML = '';
  for (const p of gParamDefs) {
    const row = document.createElement('div');
    row.className = 'g-pr';
    const cur = grade && grade.params && grade.params[p.k] !== undefined
      ? grade.params[p.k] : p.value;
    row.innerHTML =
      '<div class="g-pr-top">' +
      '<span class="g-pr-name">' + esc(p.name || p.k) + '</span>' +
      '<span class="g-pr-val" data-v="' + esc(p.k) + '">' + fmtNum(cur) + '</span>' +
      '</div>';
    const r = document.createElement('input');
    r.type = 'range';
    r.className = 'g-range';
    r.min = p.lo; r.max = p.hi; r.step = p.step;
    r.value = cur;
    r.title = p.d || '';
    r.oninput = () => {
      grade.params[p.k] = parseFloat(r.value);
      const vEl = row.querySelector('.g-pr-val');
      if (vEl) vEl.textContent = fmtNum(grade.params[p.k]);
      gradeSave();
      scheduleGradeRender();
    };
    row.appendChild(r);
    if (p.d) {
      const d = document.createElement('div');
      d.className = 'g-pr-d';
      d.textContent = p.d;
      row.appendChild(d);
    }
    box.appendChild(row);
  }
}

function fmtNum(v) {
  if (v === undefined || v === null) return '—';
  const n = parseFloat(v);
  if (isNaN(n)) return String(v);
  if (Math.abs(n) >= 10) return n.toFixed(1);
  if (Math.abs(n) >= 1) return n.toFixed(2);
  return n.toFixed(3);
}

/* ---------- 切模式 ---------- */
function applyMode(m, opts) {
  mode = m === 'grade' ? 'grade' : 'pick';
  $('#modeTabs').querySelectorAll('.mtab').forEach((b) => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  const isGrade = mode === 'grade';
  $('#paramPane').classList.toggle('hidden', isGrade);
  $('#gradePane').classList.toggle('hidden', !isGrade);
  $('#renderBar').classList.toggle('hidden', !isGrade);
  $('#splitView').classList.toggle('hidden', !isGrade);
  // 选片模式：大图区回到 A/B 交叉淡入（分屏藏起来）
  if (!isGrade) { $('#splitBefore').removeAttribute('src'); $('#splitAfter').removeAttribute('src'); }
  $('#rateOverlay').classList.toggle('hidden', isGrade);   // 调色模式不挡打星、也省地方
  if (!opts || !opts.silent) window.api.setConfig({ mode: mode });
  if (isGrade) {
    renderRenderHint();
    if (!grade && curSession) { grade = gradeOf(curSession.name); paintGradeParams(); }
    if (idx >= 0) prepareGradeImage();
  }
}

function renderRenderHint() {
  const el = $('#renderHint');
  if (!el || mode !== 'grade') return;
  el.style.cursor = 'default';
  el.onclick = null;
  if (!curSession) { el.textContent = '调色台 · 先在左栏选一个主题'; $('#btnRender').disabled = true; return; }
  if (!gStocks.length) { el.textContent = '调色台 · 引擎没连上，点这里重试'; el.style.cursor = 'pointer'; el.onclick = gradeStartEngine; return; }
  const s = gStocks.find((x) => x.name === grade.stock);
  el.textContent = '调色台 · ' + (s ? s.label : grade.stock) + ' · 选中一张后按「渲染」';
  $('#btnRender').disabled = idx < 0 || gRendering;
}

/* ---------- 取图：把「当前主题」的文件清单交给引擎 ---------- */
/** photos 是「按形态合并后」的列表（同名的成片/原图只留最新一条），
    这里要的是**原始 RAW 绝对路径**，所以按 photo 的 rel 去主题根目录找同名 RAW。 */
async function resolveGradeFiles() {
  if (!curSession) return [];
  const r = await window.api.engineScan(curSession.path, 'raf,jpg,jpeg', 2000);
  if (!r.ok) { toast('引擎扫目录失败：' + r.error, 5000); return []; }
  return r.files || [];
}

async function prepareGradeImage() {
  if (!curSession || idx < 0) return;
  const p = photos[idx];
  if (!p) return;
  const gen = ++_gGen;
  // 原图预览：引擎 /load 出来的 disp（和渲染结果同分辨率、同口径，A/B 才公平）
  const rb = await window.api.engineRawUrl(p.dir || curSession.path, p.rel);
  if (gen !== _gGen) return;
  if (rb.ok) $('#splitBefore').src = rb.image;
  else { $('#splitBefore').removeAttribute('src'); }
  $('#splitAfter').removeAttribute('src');
  $('#splitPh').style.display = 'flex';
  $('#splitPh').textContent = $('#chkAuto').checked ? '自动出图中…' : '按「渲染」出图';
  if ($('#chkAuto').checked) scheduleGradeRender(0);
  else renderRenderHint();
}

/** 自动出图：防抖，避免拖滑杆时每一帧都发一次渲染（一次 6~7 秒） */
function scheduleGradeRender(ms) {
  clearTimeout(_gTimer);
  _gTimer = setTimeout(() => renderGrade(), ms === undefined ? 450 : ms);
}

/* ---------- 渲染 ---------- */
async function renderGrade() {
  if (mode !== 'grade' || !curSession || idx < 0) return;
  const p = photos[idx];
  if (!p) return;
  const btn = $('#btnRender');
  const gen = _gGen;
  gRendering = true;
  btn.disabled = true;
  btn.textContent = '渲染中…';
  $('#splitPh').style.display = 'flex';
  $('#splitPh').textContent = '渲染中…';

  try {
    // 1) 把原图送进引擎（解码 + 入口 + 锚点，只做一次；之后换卷/换参数都复用）
    const abs = await resolveGradeFiles();
    if (gen !== _gGen) return;
    let hit = abs.find((f) => {
      const base = f.replace(/\\/g, '/').split('/').pop().replace(/\.[^.]+$/, '');
      return base.toUpperCase() === p.name.replace(/\.[^.]+$/, '').toUpperCase();
    });
    if (!hit) hit = abs.find((f) => f.replace(/\\/g, '/').endsWith('/' + p.name));
    if (!hit) {
      $('#splitPh').textContent = '没在主题里找到这张的 RAW / JPG';
      return;
    }
    const ld = await window.api.engineLoad([hit]);
    if (gen !== _gGen) return;
    if (!ld.ok || !ld.items || !ld.items.length || ld.items[0].error) {
      $('#splitPh').textContent = '引擎载入失败：' + ((ld.items && ld.items[0] && ld.items[0].error) || ld.error || '未知');
      return;
    }
    gLoaded = ld.items[0];

    // 2) 出图（换卷 6~7 秒，参数改动也走这一步）
    const rd = await window.api.engineRender(gLoaded.id, {
      stock: grade.stock, base: grade.base, params: gradeParamsStr(), side: 700, q: 92
    });
    if (gen !== _gGen) return;
    if (!rd.ok) {
      $('#splitPh').textContent = '渲染失败：' + (rd.error || '未知');
      return;
    }
    $('#splitAfter').src = rd.image;
    $('#splitPh').style.display = 'none';
  } finally {
    if (gen === _gGen) {
      gRendering = false;
      btn.disabled = idx < 0;
      btn.textContent = '渲染';
      renderRenderHint();
    }
  }
}

boot();
