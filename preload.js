const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  /* ★ 渲染进程日志（09-15 新增）：黑屏时助理直接读文件定位，SV 不用截图 */
  logLine: (line) => ipcRenderer.invoke('log-line', line),
  getConfig: () => ipcRenderer.invoke('get-config'),
  setConfig: (patch) => ipcRenderer.invoke('set-config', patch),
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
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
  engineRawUrl: (sessionPath, rel) =>
    ipcRenderer.invoke('engine-raw-url', sessionPath, rel),
  /* ---- 调色参数按主题存（每个主题一份配置，跟着主题走） ---- */
  getGrade: (themeName) => ipcRenderer.invoke('get-grade', themeName),
  setGrade: (themeName, grade) => ipcRenderer.invoke('set-grade', themeName, grade),
  exportGrade: (payload) => ipcRenderer.invoke('export-grade', payload),

  /* ---- 照片导入（SD 卡 / U 盘 → 照片库） ----
     ★ 真正的复制不在这里、也不在主进程里重写：直接调现成的导入脚本
       （`photo-import` 的 `import_photos.py`），主进程只负责"找卡 / 转参数 / 转发进度"。
     ★ 目标库默认取工作台当前的照片库（否则片导进另一个库、工作台看不见）。 */
  pickFile: (opts) => ipcRenderer.invoke('pick-file', opts),
  importDetect: () => ipcRenderer.invoke('import-detect'),
  /** 只看不复制（脚本的 --dry-run）：先把计划摆出来，人点了才真拷 */
  importPreview: (opts) => ipcRenderer.invoke('import-preview', opts),
  importRun: (opts) => ipcRenderer.invoke('import-run', opts),
  /** ★ 进度是**主进程推过来的事件**，不是 invoke 的返回值 ——
      一次导入几百行输出，等 invoke 返回才显示的话，界面全程像死机。
      返回值是**取消订阅函数**（React 的 useEffect 里直接 `return` 它）。 */
  onImportProgress: (cb) => {
    const h = (_e, line) => cb(line);
    ipcRenderer.on('import-progress', h);
    return () => ipcRenderer.removeListener('import-progress', h);
  }
});
