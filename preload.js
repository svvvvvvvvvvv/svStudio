const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
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
  engineScan: (dir, exts, limit) => ipcRenderer.invoke('engine-scan', dir, exts, limit),
  engineLoad: (paths) => ipcRenderer.invoke('engine-load', paths),
  engineRender: (id, opts) => ipcRenderer.invoke('engine-render', id, opts),
  engineRawUrl: (sessionPath, rel) =>
    ipcRenderer.invoke('engine-raw-url', sessionPath, rel),
  /* ---- 调色参数按主题存（每个主题一份配置，跟着主题走） ---- */
  getGrade: (themeName) => ipcRenderer.invoke('get-grade', themeName),
  setGrade: (themeName, grade) => ipcRenderer.invoke('set-grade', themeName, grade),
  exportGrade: (payload) => ipcRenderer.invoke('export-grade', payload)
});
