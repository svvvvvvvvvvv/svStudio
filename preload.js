const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  /* ★ 渲染进程日志（09-15 新增）：黑屏时助理直接读文件定位，SV 不用截图 */
  logLine: (line) => ipcRenderer.invoke('log-line', line),
  getConfig: () => ipcRenderer.invoke('get-config'),
  setConfig: (patch) => ipcRenderer.invoke('set-config', patch),
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  /* ★ 09-15 SV 选「B」：左栏支持**把文件夹直接拖进来**。
     ⚠ Electron 33 已经没有 `File.path`（32 起移除）⇒ 只能走 `webUtils.getPathForFile`，
       而且**必须在这一侧调**（要有 File 对象的上下文）。
     ⇒ 返回目录/文件的绝对路径；给不了就返回 ''（调用方按"拖进来的不是本地文件夹"处理）。 */
  getPathForFile: (file) => {
    try {
      return require('electron').webUtils.getPathForFile(file) || '';
    } catch (e) {
      return '';
    }
  },
  scanSessions: (libRoot) => ipcRenderer.invoke('scan-sessions', libRoot),
  listPhotos: (sessionPath) => ipcRenderer.invoke('list-photos', sessionPath),
  readImage: (sessionPath, rel) => ipcRenderer.invoke('read-image', sessionPath, rel),
  getThumb: (sessionPath, rel, width) =>
    ipcRenderer.invoke('get-thumb', sessionPath, rel, width),
  getThumbMeta: (sessionPath, rel) =>
    ipcRenderer.invoke('get-thumb-meta', sessionPath, rel),
  getExif: (sessionPath, rel) =>
    ipcRenderer.invoke('get-exif', sessionPath, rel),
  saveRatings: (ratings) => ipcRenderer.invoke('save-ratings', ratings),
  importRatings: (libRoot) => ipcRenderer.invoke('import-ratings', libRoot),
  archivePhotos: (opts) => ipcRenderer.invoke('archive-photos', opts),
  confirmDialog: (opts) => ipcRenderer.invoke('confirm-dialog', opts),
  resetColorGrade: (themePath) => ipcRenderer.invoke('reset-color-grade', themePath),

  /* ---- 调色台：svFilm 引擎（本机常驻 HTTP 服务，服务没起时统一返回 {ok:false}，
          前端据此显示「引擎未启动」而不是白屏卡死） ---- */
  engineHealth: () => ipcRenderer.invoke('engine-health'),
  engineStart: () => ipcRenderer.invoke('engine-start'),
  engineStocks: () => ipcRenderer.invoke('engine-stocks'),
  engineBases: () => ipcRenderer.invoke('engine-bases'),
  engineParams: () => ipcRenderer.invoke('engine-params'),
  /** 相纸清单（09-15 SV 选「C」）。⚠ 要带**当前这一卷**：默认相纸跟着卷走。 */
  enginePapers: (stock) => ipcRenderer.invoke('engine-papers', stock),
  engineScan: (dir, exts, limit) => ipcRenderer.invoke('engine-scan', dir, exts, limit),
  engineLoad: (paths) => ipcRenderer.invoke('engine-load', paths),
  engineBase: (id) => ipcRenderer.invoke('engine-base', id),
  engineRender: (id, opts) => ipcRenderer.invoke('engine-render', id, opts),
  /** ★★ 导出成片（09-15 SV 选「A」）：引擎渲染完**直接写盘** ——
      走 `io.save` 那条路，EXIF / 4:4:4 质量都在里面，不用前端搬 base64。
      返回 `{ ok, path, w, h, bytes, ms }`；用户取消 ⇒ `{ ok:false, canceled:true }`。 */
  exportImage: (payload) => ipcRenderer.invoke('export-image', payload),
  engineRawUrl: (sessionPath, rel) =>
    ipcRenderer.invoke('engine-raw-url', sessionPath, rel),
  /* ---- 调色参数按主题存（每个主题一份配置，跟着主题走） ---- */
  getGrade: (themeName) => ipcRenderer.invoke('get-grade', themeName),
  setGrade: (themeName, grade) => ipcRenderer.invoke('set-grade', themeName, grade),
  exportGrade: (payload) => ipcRenderer.invoke('export-grade', payload),

  /* ---- 库外·纯 RAW 目录的**预览索引** ----
     ★ 工作台列图只按 JPG 列（RAW 只当"这张有 RAF"的角标）⇒ 把"只拷了 RAF"的文件夹
       「加入目录」进来，界面上是**空的**。这里让主进程给这种目录生成一份预览小图
       （抠每张 RAW 里相机自带的机内 JPG，缩到长边 1600，写进应用缓存目录）。
     ★ 源目录**只读**；缓存可以整个删、下次自动重做；出图仍用源目录的 RAW。
     ★ 幂等：已经有的不会重转（返回里 `n` = 这次真转出来的张数）。 */
  extIndex: (srcDir) => ipcRenderer.invoke('ext-index', srcDir),
  /** 同上，进度走事件（一次几百张要几十秒，等 invoke 返回界面就像死机了） */
  onExtIndexProgress: (cb) => {
    const h = (_e, line) => cb(line);
    ipcRenderer.on('ext-index-progress', h);
    return () => ipcRenderer.removeListener('ext-index-progress', h);
  }
});
