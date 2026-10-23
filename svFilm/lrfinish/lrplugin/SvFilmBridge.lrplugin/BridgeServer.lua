-- svFilm Bridge: 文件队列命令服务（自动随插件加载启动）。
-- 命令目录固定为 E:\lr_agent\lr_cmd；Python 写 cmd.txt，插件写 result.txt。
-- 命令：
--   PING
--   SELECT <path>
--   EXPORT <outdir>
--   APPLY_EXPORT <outdir>

local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrFunctionContext = import 'LrFunctionContext'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrExportSession = import 'LrExportSession'
local LrDevelopController = import 'LrDevelopController'
local LrApplicationView = import 'LrApplicationView'

local CMD_DIR = 'E:\\lr_agent\\lr_cmd'
local CMD = CMD_DIR .. '\\cmd.txt'
local RES = CMD_DIR .. '\\result.txt'
local BLOG = CMD_DIR .. '\\plugin.log'

-- 桥自己的调试日志：循环里发生了什么必须能看见，否则"桥失联"只能靠猜。
function blog(tag)
  pcall(function()
    local f = io.open(BLOG, 'a')
    if f then f:write(os.date('%H:%M:%S') .. ' ' .. tostring(tag) .. '\n'); f:flush(); f:close() end
  end)
end

local function write_result(s)
  local f = io.open(RES, 'w')
  if f then
    f:write(s or '')
    f:close()
    return true
  end
  return false
end

local function read_cmd()
  -- ★ 不用 LrFileUtils.exists/readFile：10-22 实测在这个回调里 exists() 恒返回假，
  --   循环照转但永远读不到命令（日志只有 loop: enter，之后一条命令都没进来）。
  --   io.open 是和 write_result 同一套路径，write_result 能写成功，读就用它。
  local f = io.open(CMD, 'r')
  if not f then return nil end
  local s = f:read('*a')
  f:close()
  return s
end

local function remove_cmd()
  -- ★★ 光 os.remove 不够：10-22 实测它有时删不掉，循环就会把**同一条命令重复执行**
  --    （plugin.log 里同一个 id 连出 5 行 export）。删不掉就清空内容，双重保险。
  if os and os.remove then
    pcall(os.remove, CMD)
  end
  -- ★★ 探测用的句柄**必须 close**：不关的话 LR 一直占着 cmd.txt，
  --    调用方 `os.replace(tmp, cmd.txt)` 会报 WinError 5 拒绝访问（10-22 真踩）。
  local chk = io.open(CMD, 'r')
  if chk then
    chk:close()
    local f = io.open(CMD, 'w')
    if f then f:close() end
  end
end

local function sidecar_for(photo)
  local path = photo:getRawMetadata('path')
  if not path then return nil end
  local dir = LrPathUtils.parent(path)
  local base = LrPathUtils.removeExtension(path)
  local stem = base
  if stem:sub(-5) == '_flat' then stem = stem:sub(1, -6)
  elseif stem:sub(-6) == '_print' then stem = stem:sub(1, -7) end
  local candidates = {
    LrPathUtils.child(dir, LrPathUtils.leafName(stem) .. '.svfilm.lr.txt'),
    LrPathUtils.child(dir, LrPathUtils.leafName(base) .. '.svfilm.lr.txt'),
  }
  for _, p in ipairs(candidates) do
    if LrFileUtils.exists(p) then return p end
  end
  return nil
end

local function read_settings(path)
  local text = LrFileUtils.readFile(path)
  if not text then return nil end
  local out = {}
  for line in text:gmatch('[^\r\n]+') do
    line = line:gsub('^%s+', ''):gsub('%s+$', '')
    if line ~= '' and line:sub(1, 1) ~= '#' then
      local k, v = line:match('^([%w_]+)=(.*)$')
      if k then
        if k:match('^ToneCurvePV') then
          local arr = {}
          for num in v:gmatch('[^,]+') do
            local n = tonumber(num)
            if n ~= nil then arr[#arr + 1] = n end
          end
          if #arr >= 4 then out[k] = arr end
        elseif k == 'ToneCurveName2012' or k == 'WhiteBalance' or k == 'Profile' or k == 'CameraProfile' then
          out[k] = v
        else
          local n = tonumber(v)
          if n ~= nil then out[k] = n end
        end
      end
    end
  end
  return out
end

local function apply_with_full_table(photo, settings, full)
  if type(full) ~= 'table' then
    photo:applyDevelopSettings(settings, 'svFilm')
    return
  end
  for k, v in pairs(settings) do
    if k:match('^ToneCurvePV') then
      local existing = full[k]
      if type(existing) == 'table' and type(existing[1]) == 'table' then
        local pts = {}
        for i = 1, #v, 2 do pts[#pts + 1] = { x = v[i], y = v[i + 1] } end
        full[k] = pts
      elseif type(existing) == 'table' and type(existing[1]) == 'string' then
        local pts = {}
        for i = 1, #v, 2 do pts[#pts + 1] = tostring(v[i]) .. ', ' .. tostring(v[i + 1]) end
        full[k] = pts
      else
        full[k] = v
      end
    else
      full[k] = v
    end
  end
  photo:applyDevelopSettings(full, 'svFilm')
end

local function get_photos()
  local catalog = LrApplication.activeCatalog()
  local photos = catalog:getTargetPhotos()
  if #photos == 0 then error('no selection') end
  return catalog, photos
end

local function select_path(path)
  -- ★ 必须先切来源再选片（setSelectedPhotos 只对"当前来源里可见"的照片有效）。
  --   ⚠ 这里**不要** switchToModule：EXPORT 在 develop 模块下会导出 0 个文件（实测），
  --     develop 的切换交给 activate_develop 自己做。
  local catalog, photo = find_photo(path)
  catalog:setActiveSources({ catalog.kAllPhotos })
  LrTasks.sleep(0.3)
  catalog:setSelectedPhotos(photo, { photo })
  LrTasks.sleep(0.4)
  return photo
end

-- 当前所在模块（全局：export_photos 在文件靠前，local 必须先定义后引用）。
function cur_module()
  local ok, m = LrTasks.pcall(function() return LrApplicationView.getCurrentModuleName() end)
  if ok and m ~= nil then return tostring(m) end
  return nil
end

local function export_photos(outdir)
  local catalog, photos = get_photos()
  -- ★★ JPEG 导出必须在**库模块**：在 develop 下 `LrExportSession` 会导 0 个文件（本文件旧注记已记）。
  --    今天实测：`EXPORT` 在 develop 里既不出文件也不报错，桥一直等到超时。
  local was = cur_module()
  if was ~= nil and was ~= 'library' then
    LrTasks.pcall(function() LrApplicationView.gridView() end)
    LrTasks.sleep(1.2)
  end
  LrFileUtils.createAllDirectories(outdir)
  -- 先删同名导出，避免 LR 弹“文件已存在”把桥接卡住。
  for _, photo in ipairs(photos) do
    local p = photo:getRawMetadata('path')
    if p then
      local base = LrPathUtils.removeExtension(LrPathUtils.leafName(p))
      local target = LrPathUtils.child(outdir, base .. '.jpg')
      if LrFileUtils.exists(target) then
        LrFileUtils.delete(target)
      end
    end
  end
  local settings = {
    LR_export_destinationType = 'specificFolder',
    LR_export_destinationPathPrefix = outdir,
    LR_export_useSubfolder = false,
    LR_format = 'JPEG',
    LR_jpeg_quality = 0.95,
    -- 底图只有 1024 宽；限制到 2048 会把它放大一倍。不限制 = 原尺寸出，少一个变量。
    LR_size_doConstrain = false,
    LR_outputSharpeningOn = false,
    LR_export_colorSpace = 'sRGB',
    LR_export_overwriteWithoutPrompting = true,
  }
  local session = LrExportSession({ photosToExport = photos, exportSettings = settings })
  session:doExportOnCurrentTask()
  -- ★ 以前直接 return #photos，选择为空时也报 "EXPORTED 1"，看不出没写出文件。
  --   这里按盘上真实文件数回报，写 0 个一眼就能发现。
  local wrote = 0
  for _, photo in ipairs(photos) do
    local p = photo:getRawMetadata('path')
    if p then
      local base = LrPathUtils.removeExtension(LrPathUtils.leafName(p))
      if LrFileUtils.exists(LrPathUtils.child(outdir, base .. '.jpg')) then
        wrote = wrote + 1
      end
    end
  end
  if was ~= nil and was ~= 'library' then
    LrTasks.pcall(function() LrApplicationView.switchToModule(was) end)
    LrTasks.sleep(0.8)
  end
  return wrote
end

-- ★ Windows 上 `getRawMetadata('path')` 给的是**反斜杠**，而调用方（Python）常传正斜杠
--   ⇒ 必须先把两种分隔符归一化再比，否则"照片明明在 catalog 里"也会报 photo not in catalog。
function norm_path(p)   -- 全局：select_path 比它先定义，靠运行时解析
  return (tostring(p):gsub('\\', '/'):lower())
end

function find_photo(path)   -- 全局：同上
  local catalog = LrApplication.activeCatalog()
  local want = norm_path(path)
  -- ★★ 不要用 `getAllPhotos()` 全表遍历（10-22 实测的**真正慢源**）：
  --    目录里几万张时，每条命令光"找这张照片"就要遍历几万次 `getRawMetadata('path')`，
  --    每次都是一次 Lua→C++ 调用 ⇒ SELECT 26 s、RESET 28 s，看着像导出慢，其实全耗在查找上。
  --    改走目录索引：按**文件名**精确查，结果只有几条，再比完整路径。
  local leaf = LrPathUtils.leafName(tostring(path):gsub('\\', '/'))
  local cands
  local ok = LrTasks.pcall(function()
    cands = catalog:findPhotos({ searchDesc = {
      criteria = 'filename', operation = 'equals', value = leaf } })
  end)
  if not ok or type(cands) ~= 'table' or #cands == 0 then
    cands = catalog:getAllPhotos()          -- 兜底：索引查不到才走全表
  end
  for _, photo in ipairs(cands) do
    local p = photo:getRawMetadata('path')
    if p and norm_path(p) == want then
      return catalog, photo
    end
  end
  error('photo not in catalog: ' .. path)
end

local function apply_one(photo)
  local catalog = LrApplication.activeCatalog()
  local sidecar = sidecar_for(photo)
  local settings = sidecar and read_settings(sidecar) or nil
  if not settings then error('no sidecar for ' .. tostring(photo:getRawMetadata('path'))) end
  local ok, full = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  catalog:withWriteAccessDo('svFilm bridge one', function()
    apply_with_full_table(photo, settings, ok and full or nil)
  end)
end

local function export_one(photo, outdir)
  LrFileUtils.createAllDirectories(outdir)
  local p = photo:getRawMetadata('path')
  local base = LrPathUtils.removeExtension(LrPathUtils.leafName(p))
  local target = LrPathUtils.child(outdir, base .. '.jpg')
  if LrFileUtils.exists(target) then LrFileUtils.delete(target) end
  local settings = {
    LR_export_destinationType = 'specificFolder',
    LR_export_destinationPathPrefix = outdir,
    LR_export_useSubfolder = false,
    LR_format = 'JPEG',
    LR_jpeg_quality = 0.95,
    -- 底图只有 1024 宽；限制到 2048 会把它放大一倍。不限制 = 原尺寸出，少一个变量。
    LR_size_doConstrain = false,
    LR_outputSharpeningOn = false,
    LR_export_colorSpace = 'sRGB',
    LR_export_overwriteWithoutPrompting = true,
  }
  local session = LrExportSession({ photosToExport = { photo }, exportSettings = settings })
  session:doExportOnCurrentTask()
  return target
end

local function apply_selected()
  local catalog, photos = get_photos()
  local applied, missing, failed = 0, 0, 0
  local fulls = {}
  for i, photo in ipairs(photos) do
    local ok, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
    fulls[i] = ok and s or nil
  end
  catalog:withWriteAccessDo('svFilm bridge apply', function()
    for i, photo in ipairs(photos) do
      local sidecar = sidecar_for(photo)
      local settings = sidecar and read_settings(sidecar) or nil
      if settings and settings.ToneCurveName2012 ~= nil then
        local ok = LrTasks.pcall(function()
          apply_with_full_table(photo, settings, fulls[i])
        end)
        if ok then applied = applied + 1 else failed = failed + 1 end
      else
        missing = missing + 1
      end
    end
  end)
  return applied, missing, failed
end

local function dump_path(path)
  local photo = select_path(path)
  local s = photo:getDevelopSettings()
  local keys = {
    'WhiteBalance', 'Temperature', 'Tint', 'Profile', 'CameraProfile', 'Look',
    'ToneCurveName2012', 'ToneCurvePV2012', 'ToneCurvePV2012Red',
    'ToneCurvePV2012Green', 'ToneCurvePV2012Blue',
    'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012',
    'Whites2012', 'Blacks2012', 'GrainAmount',
    'ColorGradeShadowHue', 'ColorGradeShadowSat',
    'ColorGradeHighlightHue', 'ColorGradeHighlightSat',
    'SplitToningShadowHue', 'SplitToningShadowSaturation',
    'SplitToningHighlightHue', 'SplitToningHighlightSaturation',
    'SplitToningBalance', 'ShadowTint',
    'RedHue', 'RedSaturation', 'GreenHue', 'GreenSaturation',
    'BlueHue', 'BlueSaturation',
    'SaturationAdjustmentOrange', 'LuminanceAdjustmentOrange',
    'SaturationAdjustmentYellow', 'SaturationAdjustmentGreen',
  }
  local parts = {}
  for _, k in ipairs(keys) do
    local v = s[k]
    if type(v) == 'table' then
      local n = #v
      local first = v[1]
      local last = v[n]
      if type(first) == 'table' then
        first = tostring(first[1] or first.x) .. ',' .. tostring(first[2] or first.y)
        last = tostring(last[1] or last.x) .. ',' .. tostring(last[2] or last.y)
      end
      parts[#parts + 1] = string.format('%s[n=%d,%s..%s]', k, n, tostring(first), tostring(last))
    else
      parts[#parts + 1] = k .. '=' .. tostring(v)
    end
  end
  return table.concat(parts, '; ')
end

-- 让某张照片真正成为"开发模块的当前照片"，并返回实际生效的目标照片路径。
--
-- 这里有一个决定性的坑（Adobe 论坛 johnrellis 实测，2024）：
--   setSelectedPhotos() 只能选中"当前来源（文件夹/收藏夹）里可见"的照片。
--   传入不在当前来源里的照片时，选择会被设成"无"（不是报错！），
--   之后 createNewMask / setValue 全部作用在上一张还显示着的照片上。
-- 表现就是：命令返回成功、蒙版数"有变化"（变的是别的照片）、参数读回也像写进去了，
-- 但目标照片一点没变。之前整批标定作废就是这个原因。
-- 所以：先把来源切成"所有照片"，再选中；UI 需要 sleep 才认（不 sleep 会被忽略）。
function activate_develop(photo)
  local catalog = LrApplication.activeCatalog()
  LrTasks.pcall(function() catalog:setActiveSources({ catalog.kAllPhotos }) end)
  LrTasks.sleep(0.3)
  LrTasks.pcall(function() catalog:setSelectedPhotos(photo, { photo }) end)
  LrTasks.sleep(0.4)
  LrTasks.pcall(function()
    if LrApplicationView.getCurrentModuleName() ~= 'develop' then
      LrApplicationView.switchToModule('develop')
    end
  end)
  -- 注意：这里曾经调 LrApplicationView.loupeView()，本意是"别停在网格视图"，
  -- 但 loupeView 是 Library 的视图命令 —— 会把模块切回"库"，
  -- 之后 LrDevelopController.setValue 就全成了空操作（读回永远是旧值）。
  -- 改模块只能切到 develop，视图命令不能再调。
  LrTasks.sleep(1.2)
  local ok, target = LrTasks.pcall(function() return catalog:getTargetPhoto() end)
  if ok and target then
    -- pcall 返回的是 (ok, 值)，别只取第一个
    local ok2, p = LrTasks.pcall(function() return target:getRawMetadata('path') end)
    if ok2 and type(p) == 'string' then return p end
  end
  return nil
end

-- 目标照片校验：切不过去就绝不下手，否则会污染别的照片。
function target_matches(photo, tpath)
  local want = photo:getRawMetadata('path')
  if not tpath or not want then return false, want, tpath end
  return tpath:lower() == want:lower(), want, tpath
end

local AI_SUBTYPES = {
  people = true, person = true, subject = true, sky = true,
  background = true, objects = true, landscape = true,
}
-- ★ 亮度/颜色/景深范围蒙版：maskType=rangeMask + maskSubType
--   参考 E:\lr_agent\research\ref\varunkumar_Server.lua:514 的写法（第三方实测可用的入口）。
local RANGE_SUBTYPES = { luminance = true, color = true, depth = true }
-- ★ 基础「几何」蒙版：createNewMask 的第一个参数就是 maskType 本身（不是 aiSelection）。
--   ⚠ `radialGradient` 是驼峰、大小写敏感；输入已被 lower ⇒ 这里映射回正确写法。
--   出处：E:\lr_agent\research\ref\varunkumar_Server.lua:504（第三方实测可用的入口）。
local BASIC_MASKS = {
  ['gradient'] = 'gradient',              -- 线性渐变
  ['radialgradient'] = 'radialGradient',  -- 径向渐变（圆形/椭圆）
  ['brush'] = 'brush',                    -- 调整画笔
}

local function masks_count()
  local ok, m = LrTasks.pcall(function() return LrDevelopController.getAllMasks() end)
  if ok and type(m) == 'table' then return #m end
  return nil
end

-- ★★★ 清空蒙版（10-21 深夜发现）：`RESET`（resetAllDevelopAdjustments）在某些状态下
--   **只复位滑块、不删蒙版**（实测 `RESET ok=true masks 1->1`，而几小时前同一张图还是 1->0）。
--   蒙版一旦残留，下一轮的 base 导出就带着上一轮的塑形 ⇒ 所有 before/after 数字全废。
--   这里用整表替换语义把 `MaskGroupBasedCorrections` 写成空表（我们的蒙版全是直写表建的）。
local function clear_masks(path)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('CLEARMASKS target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local before = masks_count()
  local inner_err = nil
  catalog:withWriteAccessDo('svFilm clear masks', function()
    local ok, e = LrTasks.pcall(function()
      photo:applyDevelopSettings({ MaskGroupBasedCorrections = {} }, 'svFilm')
    end)
    if not ok then inner_err = tostring(e) end
  end, { timeout = 60 })
  LrTasks.sleep(1.0)
  local o, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  local m = (o and type(s) == 'table') and s.MaskGroupBasedCorrections or nil
  local after_g = (type(m) == 'table') and #m or 0
  local after_t = 0
  if type(m) == 'table' then
    for _, g in ipairs(m) do
      local mk = g.CorrectionMasks or g.Masks
      if type(mk) == 'table' then after_t = after_t + #mk end
    end
  end
  return string.format('CLEARMASKS groups %s->%s tiles ->%s api=%s%s',
    tostring(before or -1), tostring(after_g), tostring(after_t),
    tostring(masks_count() or -1), inner_err and (' err=' .. inner_err) or '')
end

-- ★ 蒙版「总片数」= 每组 Corrections 里 Masks 的个数之和。
--   rangeMask 若是"与上一蒙版相交"，组数(getAllMasks)不变、但某组的 Masks 片数会 +1；
--   只看组数会误判成"没建出来"然后重试，一次调用建出好几片（历史踩过）。
local function mask_total()
  -- ★ 不能用 LrDevelopController.getAllDevelopSettings()（那个方法不存在，会静默返回 nil）。
  --   正本要从 activeCatalog:getTargetPhoto():getDevelopSettings() 读。
  local cat = LrApplication.activeCatalog()
  local ok, photo = LrTasks.pcall(function() return cat:getTargetPhoto() end)
  if not ok or not photo then return nil end
  local ok2, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  local m = (ok2 and type(s) == 'table') and s.MaskGroupBasedCorrections or nil
  if type(m) ~= 'table' then return nil end
  local n = 0
  for _, g in ipairs(m) do
    -- ★ 字段名是 CorrectionMasks（不是 Masks）。写成 Masks 会永远得 0，
    --   于是 add_mask 判定"没建出来"→ 重试 → 一次调用建出 3 个蒙版（真踩过）。
    if type(g) == 'table' then
      local mk = g.CorrectionMasks or g.Masks
      if type(mk) == 'table' then n = n + #mk end
    end
  end
  return n
end

-- 往当前激活的蒙版写局部参数；空写事务是官方推荐的 flush 方式。
local function set_local_values(spec)
  local applied = {}
  for k, v in spec:gmatch('([%w_]+)%s*=%s*([%+%-%d%.]+)') do
    local key = (k:sub(1, 6) == 'local_') and k or ('local_' .. k)
    local num = tonumber(v)
    if num ~= nil then
      local ok, err = LrTasks.pcall(function() LrDevelopController.setValue(key, num) end)
      applied[#applied + 1] = key .. '=' .. tostring(num) .. (ok and '' or ('!' .. tostring(err)))
    end
  end
  if #applied == 0 then return 'local=none' end
  local catalog = LrApplication.activeCatalog()
  LrTasks.pcall(function()
    catalog:withWriteAccessDo('svFilm local', function() end, { timeout = 20 })
  end)
  return 'local[' .. table.concat(applied, ',') .. ']'
end

-- createNewMask 是 develop 控制器的 UI 操作：必须直接调用（包 withWriteAccessDo 会被回滚），
-- 也不能塞进 LrTasks.execute 再靠固定睡眠取结果（异步里读不到返回值）。
local function add_mask(path, mask_type, adjust_spec)
  mask_type = (mask_type ~= '' and mask_type) or 'people'
  mask_type = mask_type:lower()
  if mask_type == 'person' then mask_type = 'people' end
  if mask_type == 'object' then mask_type = 'objects' end
  -- ★ rangeMask（亮度/颜色/景深）走另一个 maskType；subject 等仍走 aiSelection；
  --   线性/径向/画笔三种「几何蒙版」的 maskType 就是它自己。
  local kind = RANGE_SUBTYPES[mask_type] and 'rangeMask'
    or BASIC_MASKS[mask_type]
    or 'aiSelection'
  if not AI_SUBTYPES[mask_type] and not RANGE_SUBTYPES[mask_type]
     and not BASIC_MASKS[mask_type] then
    error('unknown mask type: ' .. tostring(mask_type))
  end
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    -- 目标没切过去：继续建蒙版只会把它建到上一张照片上（历史上真发生过）
    return string.format('MASK %s target=MISMATCH want=%s got=%s (skipped)',
      mask_type, LrPathUtils.leafName(want or '?'),
      got and LrPathUtils.leafName(got) or 'nil')
  end
  local before = masks_count()
  local before_t = mask_total() or before
  -- 刚导入/刚 reset 的照片，第一次 createNewMask 常常"成功但没建"（AI 选区还算不出来）。
  -- 重试到蒙版数真的增加为止；不靠单次返回值下结论。
  local ok, err = false, nil
  local after = before
  local after_t = before_t
  for attempt = 1, 3 do
    local o, e = LrTasks.pcall(function()
      -- ★★ 几何蒙版（brush/gradient/radialGradient）**不能带第二个参数**：
      --   带了报 "maskSubtype should not be present with maskTypes : brush, gradient, radialGradient"，
      --   然后什么也不建（这次真踩了）。只有 aiSelection / rangeMask 才要 subtype。
      if BASIC_MASKS[mask_type] then
        LrDevelopController.createNewMask(kind)
      else
        LrDevelopController.createNewMask(kind, mask_type)
      end
    end)
    ok, err = o, e
    -- 蒙版数不是同步更新的：轮询等它落库，别因为"读到还是旧值"就以为失败了重试，
    -- 那样一次调用会建出 2 个蒙版（实测发生过）。
    -- ★ 用「总片数」判定，相交型 rangeMask 只加片数不加组数。
    for _ = 1, 6 do
      LrTasks.sleep(0.8)
      after = masks_count()
      after_t = mask_total() or after
      if before_t and after_t and after_t > before_t then break end
    end
    if before_t and after_t and after_t > before_t then break end
    if attempt < 3 then LrTasks.sleep(1.0) end
  end
  local parts = { 'MASK ' .. kind .. '/' .. mask_type, 'ok=' .. tostring(ok) }
  if not ok then parts[#parts + 1] = 'err=' .. tostring(err) end
  -- createNewMask 是 UI 操作，建出来的蒙版只在内存里；不开一个空写事务 flush，
  -- catalog 的 MaskGroupBasedCorrections 会是空的（导出/重开就没了）。
  LrTasks.pcall(function()
    LrApplication.activeCatalog():withWriteAccessDo('svFilm mask flush', function() end,
      { timeout = 30 })
  end)
  LrTasks.sleep(0.5)
  after = masks_count() or after
  after_t = mask_total() or after_t
  parts[#parts + 1] = string.format('groups=%s->%s tiles=%s->%s',
    tostring(before or -1), tostring(after or -1),
    tostring(before_t or -1), tostring(after_t or -1))
  if adjust_spec and adjust_spec ~= '' then
    parts[#parts + 1] = set_local_values(adjust_spec)
  end
  return table.concat(parts, ' ')
end

local function set_local(path, spec)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('LOCAL target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local n = masks_count()
  if n == nil then error('getAllMasks not available') end
  if n == 0 then error('no mask on photo (run MASK first)') end
  return 'LOCAL masks=' .. tostring(n) .. ' ' .. set_local_values(spec)
end

-- 把 sidecar 的扁平键值转成 applyDevelopSettings 能吃的形式。
-- 只提交 sidecar 里有的键：LR 的 apply 是"合并"语义，不需要把整份 getDevelopSettings 回灌；
-- 回灌会带上只读键，LR 会静默拒绝整次写入（表现为返回成功但值不变）。
local function build_clean_settings(settings)
  local clean = {}
  for k, v in pairs(settings) do
    if k:match('^ToneCurvePV') and type(v) == 'table' then
      local pts = {}
      local ok = true
      for i = 1, #v, 2 do
        if type(v[i]) == 'table' then
          pts[#pts + 1] = { x = v[i][1] or v[i].x, y = v[i][2] or v[i].y }
        elseif type(v[i]) == 'number' and type(v[i + 1]) == 'number' then
          pts[#pts + 1] = { x = v[i], y = v[i + 1] }
        else
          ok = false
        end
      end
      if ok and #pts > 0 then clean[k] = pts end
    else
      clean[k] = v
    end
  end
  return clean
end

-- 只应用 sidecar 的 master 参数，不导出（标定局部参数时要在"交付状态"上做，
-- 裸平基底 L* 只有 25 左右，跟加了 master 曝光后的调色响应不是一回事）。
-- 注意：必须在 activate_develop 之后定义（Lua 的 local function 不提升）。
local function apply_path(path)
  local catalog, photo = find_photo(path)
  -- 必须先设成当前照片：getDevelopSettings 对"未选中"的照片会返回 nil，
  -- 于是 full=nil 走 applyDevelopSettings(settings) 分支，扁平 ToneCurvePV 格式不对，
  -- 异常又被 withWriteAccessDo 吞掉 -> 静默回滚（表现为"APPLIED 但 DUMP 没变"）。
  local tpath0 = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath0)
  if not hit then
    error(string.format('target mismatch: want=%s got=%s',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil'))
  end
  local sidecar = sidecar_for(photo)
  if not sidecar then error('no sidecar for ' .. path) end
  local settings = read_settings(sidecar)
  if not settings then error('sidecar unreadable: ' .. sidecar) end
  local clean = build_clean_settings(settings)
  local nclean = 0
  for _ in pairs(clean) do nclean = nclean + 1 end
  local inner_err = nil
  catalog:withWriteAccessDo('svFilm bridge apply one', function()
    local o, e = LrTasks.pcall(function()
      photo:applyDevelopSettings(clean, 'svFilm')
    end)
    if not o then inner_err = tostring(e) end
  end, { timeout = 60 })
  if inner_err then error('apply failed: ' .. inner_err) end
  LrTasks.sleep(1.2)
  local curve, expo = 'n/a', 'n/a'
  local o2, s2 = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  if o2 and type(s2) == 'table' then
    curve = tostring(s2.ToneCurveName2012)
    expo = tostring(s2.Exposure2012)
  end
  return string.format('APPLIED %s curve=%s expo=%s n=%d',
    LrPathUtils.leafName(sidecar), curve, expo, nclean)
end

-- 写全局参数（无 local_ 前缀）。
-- 不能用 LrDevelopController.setValue：它对全局键（Exposure2012 等）会被静默忽略，
-- 返回 ok 但 catalog 里读回还是 0。applyDevelopSettings 才是能落库的路径。
-- 找照片旁边由 svFilm 生成的 LR 预设（<stem>.lrpreset.xmp）。
local function preset_for(photo)
  local path = photo:getRawMetadata('path')
  if not path then return nil end
  local dir = LrPathUtils.parent(path)
  local base = LrPathUtils.removeExtension(path)
  local stem = base
  if stem:sub(-5) == '_flat' then stem = stem:sub(1, -6)
  elseif stem:sub(-6) == '_print' then stem = stem:sub(1, -7) end
  local candidates = {
    LrPathUtils.child(dir, LrPathUtils.leafName(stem) .. '.lrpreset.xmp'),
    LrPathUtils.child(dir, LrPathUtils.leafName(base) .. '.lrpreset.xmp'),
  }
  for _, p in ipairs(candidates) do
    if LrFileUtils.exists(p) then return p end
  end
  return nil
end

-- 用 LR 自己的"应用预设"来落参数。
--
-- 为什么不用 applyDevelopSettings：它写的是 catalog 正本，但 LR 当前会话里那份
-- 内存中的 develop 状态不会跟着变 —— 导出渲染用的是内存那份（所以"写进去了、导出没变"），
-- 而且之后任何一次 LR 自身的操作（比如建蒙版）都会用内存那份把正本覆盖回去。
-- applyDevelopPreset 是 LR 的正式操作，正本和内存一起更新，渲染立刻认。
local function apply_preset(path)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('PRESET target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local pre = preset_for(photo)
  if not pre then error('no .lrpreset.xmp next to ' .. path) end
  -- 调用方式几种都试一遍：SDK 对 applyDevelopPreset 的"在不在写事务里 / 要不要 pluginName"
  -- 没有明确文档，实测第一版（写事务内 + pluginName）直接 assert 失败。
  local attempts = {
    { 'path+write', true, nil },
    { 'path-only', false, nil },
    { 'path+plugin', true, 'svFilmBridge' },
  }
  local errs = {}
  for _, a in ipairs(attempts) do
    local name, in_write, plugin_name = a[1], a[2], a[3]
    local call = function()
      if plugin_name then
        photo:applyDevelopPreset(pre, plugin_name)
      else
        photo:applyDevelopPreset(pre)
      end
    end
    local ok, e
    if in_write then
      ok, e = LrTasks.pcall(function()
        catalog:withWriteAccessDo('svFilm preset', call, { timeout = 90 })
      end)
    else
      ok, e = LrTasks.pcall(call)
    end
    if ok then
      LrTasks.sleep(2.0)
      local curve, expo, contrast = 'n/a', 'n/a', 'n/a'
      local o, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
      if o and type(s) == 'table' then
        curve = tostring(s.ToneCurveName2012)
        expo = tostring(s.Exposure2012)
        contrast = tostring(s.Contrast2012)
      end
      return string.format('PRESET ok(%s) %s curve=%s expo=%s contrast=%s',
        name, LrPathUtils.leafName(pre), curve, expo, contrast)
    end
    errs[#errs + 1] = name .. ':' .. tostring(e)
  end
  error('preset failed: ' .. table.concat(errs, ' | '))
end

-- 只走 LrDevelopController.setValue（等价于拖滑块），不写 catalog。
-- 用来把两件事分开验证："写进了 catalog 但渲染不认" vs "压根没生效"。
local function set_live(path, spec)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('LIVE target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local applied = {}
  for k, v in spec:gmatch('([%w_]+)%s*=%s*([%+%-%d%.]+)') do
    local num = tonumber(v)
    if num ~= nil then
      local ok, err = LrTasks.pcall(function() LrDevelopController.setValue(k, num) end)
      applied[#applied + 1] = k .. '=' .. tostring(num) .. (ok and '' or ('!' .. tostring(err)))
    end
  end
  LrTasks.sleep(1.2)
  local o, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  local rb = 'n/a'
  if o and type(s) == 'table' then rb = tostring(s.Exposure2012) end
  local mod = 'n/a'
  local okm, m = LrTasks.pcall(function() return LrApplicationView.getCurrentModuleName() end)
  if okm then mod = tostring(m) end
  return 'LIVE[' .. table.concat(applied, ',') .. '] module=' .. mod .. ' expo_readback=' .. rb
end

local function set_global(path, spec)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('GLOBAL target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local settings = {}
  local names = {}
  for k, v in spec:gmatch('([%w_]+)%s*=%s*([%+%-%d%.]+)') do
    local num = tonumber(v)
    if num ~= nil then
      settings[k] = num
      names[#names + 1] = k .. '=' .. tostring(num)
    end
  end
  if #names == 0 then return 'GLOBAL none' end
  local inner_err = nil
  -- 只走 applyDevelopSettings（直写 catalog）。
  -- 不要先调 LrDevelopController.setValue：它对全局键本来就无效（返回成功、值不变），
  -- 而且会把开发模块里那份旧的编辑状态留在内存里，随后覆盖掉刚写进 catalog 的值
  -- —— 表现为 getDevelopSettings 读回 1、catalog 表和导出都还是 0。
  catalog:withWriteAccessDo('svFilm global set', function()
    local ok, e = LrTasks.pcall(function() photo:applyDevelopSettings(settings, 'svFilm') end)
    if not ok then inner_err = tostring(e) end
  end, { timeout = 60 })
  if inner_err then error('global failed: ' .. inner_err) end
  LrTasks.sleep(1.2)
  -- 逐键读回对比：只有"读回值真等于写入值"才算写成。
  -- 之前只报一个 expo_readback，掩盖了"另一个键根本没写进去"的情况。
  local o, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  local rparts = {}
  local bad = 0
  for k, v in pairs(settings) do
    local r = nil
    if o and type(s) == 'table' then
      r = s[k]
      if r == nil then r = s[k .. '2012'] end
    end
    local okv = (type(r) == 'number' and math.abs(r - v) < 0.01)
      or (type(r) == 'string' and tostring(v) == r)
    if not okv then bad = bad + 1 end
    rparts[#rparts + 1] = string.format('%s:%s->%s', k, tostring(v),
      r == nil and 'nil' or tostring(r))
  end
  return string.format('GLOBAL[%s] readback{%s} %s',
    table.concat(names, ','), table.concat(rparts, ' '),
    bad == 0 and 'VERIFIED' or ('MISMATCH x' .. tostring(bad)))
end

-- ★ 点曲线：`ToneCurvePV2012` 是**点数组**，文本 key=value 表达不了 ⇒ 单开一条命令。
--   `CURVE <path> <key> <x,y,x,y,...>`，key 省略时用 ToneCurvePV2012。
--   ★ 必须拿 `getDevelopSettings()` 的全表改一个键再写回：`applyDevelopSettings` 是**整表替换**，
--     只发 `{ToneCurvePV2012=...}` 会把 Exposure / Highlights 全部打回默认（10-21 踩过）。
--   ★ 点必须写成 `{x=,y=}` 的表数组；平铺的 {0,0,32,30,...} SDK 不认（10-22 实测）。
local function set_curve(path, key, spec)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('CURVE target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local nums = {}
  for v in spec:gmatch('[^,]+') do
    local n = tonumber(v)
    if n ~= nil then nums[#nums + 1] = n end
  end
  if #nums < 4 or #nums % 2 ~= 0 then error('CURVE 需要成对的 x,y，至少两个点') end
  local pts = {}
  for i = 1, #nums, 2 do pts[#pts + 1] = { x = nums[i], y = nums[i + 1] } end

  local full = photo:getDevelopSettings()
  if type(full) ~= 'table' then error('getDevelopSettings 返回空') end
  full[key] = pts
  full.ToneCurveName2012 = 'Custom'
  local inner_err = nil
  catalog:withWriteAccessDo('svFilm curve set', function()
    local ok, e = LrTasks.pcall(function() photo:applyDevelopSettings(full, 'svFilm') end)
    if not ok then inner_err = tostring(e) end
  end, { timeout = 60 })
  if inner_err then error('curve failed: ' .. inner_err) end
  LrTasks.sleep(1.0)
  local s = photo:getDevelopSettings()
  local rb = type(s) == 'table' and type(s[key]) == 'table' and #s[key] or 0
  return string.format('CURVED %s n=%d readback=%d exposure=%s', key, #pts, rb,
    type(s) == 'table' and tostring(s.Exposure2012) or 'nil')
end

-- ★★ 把曲线**点值**打出来（GETVAL 只给表地址，看不出是我们的点还是 LR 的默认 4 点）。
local function dump_curve(path, key)
  local _, photo = find_photo(path)
  activate_develop(photo)
  local s = photo:getDevelopSettings()
  local out = {}
  local t = type(s) == 'table' and s[key] or nil
  if type(t) == 'table' then
    for _, p in ipairs(t) do
      if type(p) == 'table' then
        local x = p.x or p[1]
        local y = p.y or p[2]
        out[#out + 1] = tostring(x) .. ',' .. tostring(y)
      else
        out[#out + 1] = 'str[' .. tostring(p) .. ']'   -- ★ 很可能是 "x, y" 字符串
      end
    end
  end
  return string.format('DUMP %s name=%s pv=%s n=%d pts=%s', key,
    type(s) == 'table' and tostring(s.ToneCurveName2012) or 'nil',
    type(s) == 'table' and tostring(s.ProcessVersion) or 'nil',
    #out, table.concat(out, ';'))
end

-- ★★ 三种坐标格式各写一次：`xy`(0-255 键值表) / `pos`(位置式 {x,y}) / `norm`(0-1)。
local function set_curve_fmt(path, key, spec, mode)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  if not hit then return 'CURVEFMT target=MISMATCH (skipped)' end
  local nums = {}
  for v in spec:gmatch('[^,]+') do local n = tonumber(v); if n ~= nil then nums[#nums + 1] = n end end
  if #nums < 4 or #nums % 2 ~= 0 then error('CURVEFMT 需要成对的 x,y') end
  local pts = {}
  for i = 1, #nums, 2 do
    local x, y = nums[i], nums[i + 1]
    if mode == 'norm' then x, y = x / 255.0, y / 255.0 end
    if mode == 'pos' then
      pts[#pts + 1] = { x, y }
    elseif mode == 'str' then
      pts[#pts + 1] = string.format('%d, %d', math.floor(x), math.floor(y))
    elseif mode == 'flat' then
      pts[#pts + 1] = x          -- ★ 扁平数字：x0,y0,x1,y1,...
      pts[#pts + 1] = y
    else
      pts[#pts + 1] = { x = x, y = y }
    end
  end
  local full = photo:getDevelopSettings()
  if type(full) ~= 'table' then error('getDevelopSettings 返回空') end
  full[key] = pts
  full.ToneCurveName2012 = 'Custom'
  local err = nil
  catalog:withWriteAccessDo('svFilm curve fmt', function()
    local ok, e = LrTasks.pcall(function() photo:applyDevelopSettings(full, 'svFilm') end)
    if not ok then err = tostring(e) end
  end, { timeout = 60 })
  LrTasks.sleep(0.8)
  return string.format('CURVEFMT mode=%s n=%d err=%s | %s', tostring(mode), #pts,
    tostring(err), dump_curve(path, key))
end

-- ★★ 部分表写入：只发曲线两个键（不读全表再写回）。验证"全表里含只读字段导致整次丢弃"这个假设。
local function set_curve_partial(path, key, spec)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  if not hit then return 'CURVEP target=MISMATCH (skipped)' end
  local nums = {}
  for v in spec:gmatch('[^,]+') do local n = tonumber(v); if n ~= nil then nums[#nums + 1] = n end end
  if #nums < 4 or #nums % 2 ~= 0 then error('CURVEP 需要成对的 x,y') end
  local pts = {}
  for i = 1, #nums, 2 do pts[#pts + 1] = { x = nums[i], y = nums[i + 1] } end
  -- 第一步：只写名字
  catalog:withWriteAccessDo('svFilm curve name', function()
    LrTasks.pcall(function()
      photo:applyDevelopSettings({ ToneCurveName2012 = 'Custom' }, 'svFilm')
    end)
  end, { timeout = 60 })
  LrTasks.sleep(0.8)
  local s1 = photo:getDevelopSettings()
  local n1 = type(s1) == 'table' and tostring(s1.ToneCurveName2012) or 'nil'
  -- 第二步：只写点 + 名字
  catalog:withWriteAccessDo('svFilm curve pts', function()
    LrTasks.pcall(function()
      photo:applyDevelopSettings({ ToneCurveName2012 = 'Custom', [key] = pts }, 'svFilm')
    end)
  end, { timeout = 60 })
  LrTasks.sleep(1.0)
  local s2 = photo:getDevelopSettings()
  local n2 = type(s2) == 'table' and tostring(s2.ToneCurveName2012) or 'nil'
  local c2 = type(s2) == 'table' and type(s2[key]) == 'table' and #s2[key] or 0
  return string.format('CURVEP %s n=%d name_after_name=%s name_after_pts=%s pts=%d',
    key, #pts, n1, n2, c2)
end

-- ★★★ 曲线走**实时控件**：官方文档把 ToneCurvePV2012 列为 LrDevelopController 的 tonePanel 参数，
--     说明它该走 `setValue`（等价于拖点），而不是 applyDevelopSettings。
--     之前五条路全走 applyDevelopSettings ⇒ `ToneCurveName2012` 永远是 Linear。
local function set_curve_live(path, key, spec)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  if not hit then
    return 'SETCURVE target=MISMATCH (skipped)'
  end
  local nums = {}
  for v in spec:gmatch('[^,]+') do
    local n = tonumber(v)
    if n ~= nil then nums[#nums + 1] = n end
  end
  if #nums < 4 or #nums % 2 ~= 0 then error('SETCURVE 需要成对的 x,y') end
  local pts = {}
  for i = 1, #nums, 2 do pts[#pts + 1] = { x = nums[i], y = nums[i + 1] } end
  local ok, err = LrTasks.pcall(function()
    LrDevelopController.setValue(key, pts)
  end)
  LrTasks.sleep(1.0)
  local s = photo:getDevelopSettings()
  return string.format('SETCURVE %s n=%d ok=%s err=%s curve=%s',
    key, #pts, tostring(ok), tostring(err),
    type(s) == 'table' and tostring(s.ToneCurveName2012) or 'nil')
end

-- ★★★ 多条曲线**一次写**：applyDevelopSettings 是整表替换，逐条写会互相擦
--   （每次 getDevelopSettings 拿到的是旧表 ⇒ 后写的把先写的抹掉，10-22 实测）。
--   语法：`CURVES <path> Key1:x,y,x,y;Key2:...`
local function set_curves_multi(path, spec)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  if not hit then return 'CURVES target=MISMATCH (skipped)' end
  local full = photo:getDevelopSettings()
  if type(full) ~= 'table' then error('getDevelopSettings 返回空') end
  local n = 0
  for seg in spec:gmatch('[^;]+') do
    local key, body = seg:match('^([%w_]+)%s*[:=]%s*(.+)$')
    if key then
      local nums = {}
      for v in body:gmatch('[^,]+') do
        local x = tonumber(v)
        if x ~= nil then nums[#nums + 1] = x end
      end
      if #nums >= 4 and #nums % 2 == 0 then
        local pts = {}
        for i = 1, #nums, 2 do
          pts[#pts + 1] = nums[i]        -- ★ 必须是扁平数字（{x=,y=} 会被 LR 丢掉）
          pts[#pts + 1] = nums[i + 1]
        end
        full[key] = pts
        n = n + 1
      end
    end
  end
  full.ToneCurveName2012 = 'Custom'
  local err = nil
  catalog:withWriteAccessDo('svFilm curves', function()
    local ok, e = LrTasks.pcall(function() photo:applyDevelopSettings(full, 'svFilm') end)
    if not ok then err = tostring(e) end
  end, { timeout = 90 })
  LrTasks.sleep(1.0)
  if err then error('curves failed: ' .. err) end
  local s = photo:getDevelopSettings()
  local rb = 0
  for seg in spec:gmatch('[^;]+') do
    local key = seg:match('^([%w_]+)')
    if key and type(s) == 'table' and type(s[key]) == 'table' then rb = rb + #s[key] end
  end
  return string.format('CURVES ok n=%d readback=%d', n, rb)
end

-- ★★★ 从**文件**重读元数据：写 .xmp 侧车后再执行，catalog 与内存一起刷新。
--   为什么需要它：`applyDevelopSettings` / `CURVE` 只写正本，内存那份不动 ⇒ 导出不变
--   （10-22 实测：点曲线写进去、`ToneCurveName2012` 读回还是 Linear）；
--   `applyDevelopPreset` 在这里三种调用方式都 assert 失败。
--   `readMetadata()` 是 LR 的正式操作，会把 .xmp 里的 ToneCurveName2012=Custom 一起吃进去。
local function read_meta(path)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('READMETA target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local err = nil
  catalog:withWriteAccessDo('svFilm read metadata', function()
    local ok, e = LrTasks.pcall(function() photo:readMetadata() end)
    if not ok then err = tostring(e) end
  end, { timeout = 90 })
  if err then error('readmeta failed: ' .. err) end
  LrTasks.sleep(1.5)
  local s = photo:getDevelopSettings()
  return string.format('READMETA ok curve=%s exposure=%s',
    type(s) == 'table' and tostring(s.ToneCurveName2012) or 'nil',
    type(s) == 'table' and tostring(s.Exposure2012) or 'nil')
end

-- ★★★ 直接写蒙版表 —— 绕开 createNewMask 建不出几何蒙版这件事。
--   几何蒙版（radialGradient / gradient / brush）在 SDK 里 createNewMask 静默无效
--   （返回 true、蒙版数 0->0，四种前置动作全试过）。但 catalog 里留着一张历史径向渐变的
--   完整结构 ⇒ 整表直写是唯一出路。
--   payload 语法（不许有空格）：
--     G:CorrectionName=xxx,CorrectionReferenceX=0.5|M:What=Mask/CircularGradient,Left=0.2,LocalExposure2012=1.0
--   `G:` 开一组，`M:` 往当前组加一片蒙版；值支持 number / true / false / 字符串。
local function _kv(str)
  local t = {}
  for k, v in str:gmatch('([%w_]+)%s*=%s*([^,|]+)') do
    -- ID 类字段一律当字符串：纯数字的 hex 串被 tonumber 会丢精度，ID 一旦改写 LR 会当成新蒙版
    local vv = tostring(v):gsub('^%+', '')   -- ★ 不认 '+' 号会静默丢掉正数
    local n = k:match('ID$') and nil or tonumber(vv)
    if n ~= nil then t[k] = n
    elseif v == 'true' then t[k] = true
    elseif v == 'false' then t[k] = false
    else t[k] = v end
  end
  return t
end

-- ★★★ 亮度/颜色范围蒙版的参数是**嵌套表** `CorrectionRangeMask`（不是平铺层）。
--   实测（10-21）：`M:What=Mask/RangeMask,Type=1` 会被收下，但 Type 被丢掉、回到默认 0；
--   MASKSX 读回的真实结构是
--     { What="Mask/RangeMask", CorrectionRangeMask={Invert=false,SampleType=2,Type=0,Version=3}, ... }
--   ⇒ 加 `C:` 标签：往「当前这片蒙版」里塞 CorrectionRangeMask。
local function _parse_groups(payload)
  local groups, cur, curmask = {}, nil, nil
  for seg in payload:gmatch('[^|]+') do
    local tag, body = seg:match('^([GMC])%s*:(.*)$')
    if tag == 'G' then
      cur = _kv(body)
      cur.CorrectionMasks = {}
      curmask = nil
      groups[#groups + 1] = cur
    elseif tag == 'M' and cur then
      curmask = _kv(body)
      cur.CorrectionMasks[#cur.CorrectionMasks + 1] = curmask
    elseif tag == 'C' and curmask then
      curmask.CorrectionRangeMask = _kv(body)
    end
  end
  return groups
end

local function set_masks(path, payload)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('SETMASKS target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local groups = _parse_groups(payload)
  if #groups == 0 then return 'SETMASKS parse-empty' end
  local ng, nm = #groups, 0
  for _, g in ipairs(groups) do nm = nm + #g.CorrectionMasks end
  local inner_err = nil
  catalog:withWriteAccessDo('svFilm masks set', function()
    local ok, e = LrTasks.pcall(function()
      photo:applyDevelopSettings({ MaskGroupBasedCorrections = groups }, 'svFilm')
    end)
    if not ok then inner_err = tostring(e) end
  end, { timeout = 60 })
  if inner_err then error('setmasks failed: ' .. inner_err) end
  LrTasks.sleep(1.2)
  local o, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  local m = (o and type(s) == 'table') and s.MaskGroupBasedCorrections or nil
  local rg, rt = -1, -1
  if type(m) == 'table' then
    rg = #m
    rt = 0
    for _, g in ipairs(m) do
      local mk = g.CorrectionMasks or g.Masks
      if type(mk) == 'table' then rt = rt + #mk end
    end
  end
  local le = 'nil'
  if type(m) == 'table' and m[1] and type(m[1].CorrectionMasks) == 'table'
     and m[1].CorrectionMasks[1] then
    le = tostring(m[1].CorrectionMasks[1].LocalExposure2012)
  end
  return string.format('SETMASKS wrote %dG/%dM readback %dG/%dM localExpo=%s',
    ng, nm, rg, rt, le)
end

-- ★ 线性渐变走**另一个顶层键** `GradientBasedCorrections`（不在 MaskGroupBasedCorrections 里）。
--   实测：往 MaskGroup 里写 What="Mask/Gradient" 或 "Mask/LinearGradient" 都会被丢掉
--   （组进了 1G，但蒙版片数是 0）。线性渐变在 LR 里是独立的旧式滤镜数组。
--   payload：`S:ZeroX=0.5,ZeroY=0.0,FullX=0.5,FullY=1.0,LocalExposure2012=1.0|S:...`
local function set_grad(path, payload)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    return string.format('SETGRAD target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local segs = {}
  for seg in payload:gmatch('[^|]+') do
    local body = seg:match('^S%s*:(.*)$')
    if body then
      local t = _kv(body)
      t.What = t.What or 'Gradient'
      segs[#segs + 1] = t
    end
  end
  if #segs == 0 then return 'SETGRAD parse-empty' end
  local inner_err = nil
  catalog:withWriteAccessDo('svFilm grad set', function()
    local ok, e = LrTasks.pcall(function()
      photo:applyDevelopSettings({ GradientBasedCorrections = segs }, 'svFilm')
    end)
    if not ok then inner_err = tostring(e) end
  end, { timeout = 60 })
  if inner_err then error('setgrad failed: ' .. inner_err) end
  LrTasks.sleep(1.2)
  local o, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
  local g = (o and type(s) == 'table') and s.GradientBasedCorrections or nil
  local n = (type(g) == 'table') and #g or -1
  local zx = 'nil'
  if type(g) == 'table' and g[1] then zx = tostring(g[1].ZeroX) .. ',' .. tostring(g[1].ZeroY) end
  return string.format('SETGRAD wrote %d readback=%d zero=%s', #segs, n, zx)
end

-- 清掉整张图的 develop 设置（含所有蒙版）。SDK 没有"删单个蒙版"的接口，
-- 重复跑同一张图前必须先归零，否则蒙版会叠加、参数会写到上一个蒙版上。
local function reset_develop(path)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit, want, got = target_matches(photo, tpath)
  if not hit then
    -- 重置是破坏性操作，目标不对就绝不能干（会清掉别的照片的 develop）
    return string.format('RESET target=MISMATCH want=%s got=%s (skipped)',
      LrPathUtils.leafName(want or '?'), got and LrPathUtils.leafName(got) or 'nil')
  end
  local before = masks_count()
  local ok, err = LrTasks.pcall(function()
    LrDevelopController.resetAllDevelopAdjustments()
  end)
  LrTasks.sleep(0.6)
  local after = masks_count()
  return string.format('RESET ok=%s masks %s->%s%s', tostring(ok),
    tostring(before or -1), tostring(after or -1), ok and '' or (' err=' .. tostring(err)))
end

-- 删除某路径在目录里的所有记录（不删磁盘文件）。
-- 重复导入会让同一路径出现多条 photo 记录，插件写 A、导出读 B，
-- 表现为"返回成功但图没变"——这是最难查的一类假象，必须先清掉。
local function remove_path(path)
  local catalog = LrApplication.activeCatalog()
  local victims = {}
  for _, photo in ipairs(catalog:getAllPhotos()) do
    local p = photo:getRawMetadata('path')
    if p and p:lower() == path:lower() then victims[#victims + 1] = photo end
  end
  if #victims == 0 then error('photo not in catalog: ' .. path) end
  local n = 0
  local errs = {}
  -- 逐条删、每条一个独立事务：批量放在一个事务里，只要有一条失败整批回滚。
  for _, photo in ipairs(victims) do
    local o, e = LrTasks.pcall(function()
      catalog:withWriteAccessDo('svFilm remove dup', function()
        catalog:removePhoto(photo, false)  -- false = 只从目录移除，不删磁盘文件
      end, { timeout = 60 })
    end)
    if o then n = n + 1 else errs[#errs + 1] = tostring(e):sub(1, 60) end
  end
  return string.format('REMOVED %s/%s%s', tostring(n), tostring(#victims),
    #errs > 0 and (' err=' .. table.concat(errs, ' | ')) or '')
end

-- ★★ addPhoto / JPEG 导出都必须在 **库模块** 下：在 develop 模块里 addPhoto 恒返回
--    AgErrorText「以下文件未导入…」，`LrExportSession` 则静默导 0 个文件。
--    实测 `switchToModule('library')` 从 develop 切**不回去**，必须用库的视图命令 `gridView()`。
local function add_photo(path)
  local catalog = LrApplication.activeCatalog()
  local was = cur_module()
  local tries = {}
  if was ~= nil and was ~= 'library' then
    -- 依次试三种切库方式，每步都把**返回值和切换后的模块名**记下来：
    -- 只写 `pcall(...)` 会把"调用报错"和"调用了但没切过去"混成同一种沉默。
    local function attempt(name, fn)
      local ok, e = LrTasks.pcall(fn)
      LrTasks.sleep(1.2)
      local now = cur_module()
      tries[#tries + 1] = string.format('%s=%s/%s', name,
        ok and 'ok' or ('ERR:' .. tostring(e):sub(1, 30)), tostring(now))
      return now == 'library'
    end
    if not attempt('gridView', function() LrApplicationView.gridView() end) then
      if not attempt('loupeView', function() LrApplicationView.loupeView() end) then
        attempt('switchToModule', function() LrApplicationView.switchToModule('library') end)
      end
    end
  end
  local photo, err
  catalog:withWriteAccessDo('svFilm add photo', function()
    local o, p = LrTasks.pcall(function() return catalog:addPhoto(path) end)
    if o then photo = p else err = tostring(p) end
  end, { timeout = 60 })
  if was ~= nil and was ~= 'library' then
    LrTasks.pcall(function() LrApplicationView.switchToModule(was) end)
    LrTasks.sleep(0.8)
  end
  if err then
    error('addPhoto failed (was=' .. tostring(was) .. ' tries=' .. table.concat(tries, ' ')
      .. ' now=' .. tostring(cur_module()) .. '): ' .. err)
  end
  if photo == nil then error('addPhoto returned nil: ' .. tostring(path)) end
  return 'ADDED ' .. tostring(photo:getRawMetadata('path') or path)
end

local function mask_dump(path)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  local parts = {}
  -- getAllMasks 读的是"开发模块当前照片"，不是 path 那张；没切过去时这个数不是它的。
  parts[#parts + 1] = hit and 'target=ok' or 'target=MISMATCH'
  local n = masks_count()
  parts[#parts + 1] = 'api=' .. tostring(n or 'n/a')
  local s = photo:getDevelopSettings()
  local m = s.MaskGroupBasedCorrections
  if type(m) == 'table' then
    parts[#parts + 1] = 'cfg=' .. tostring(#m)
    for i, g in ipairs(m) do
      local nm, cnt, sub = '?', 0, ''
      if type(g) == 'table' then
        nm = tostring(g.CorrectionName or g.Name or g.name or g.What or '?')
        local mk = g.CorrectionMasks or g.Masks
        if type(mk) == 'table' then
          cnt = #mk
          local s2 = {}
          for _, mm in ipairs(mk) do
            if type(mm) == 'table' then
              s2[#s2 + 1] = tostring(mm.MaskSubType or '?')
                .. (mm.MaskInverted and 'I' or '')
                .. '/' .. tostring(mm.What or '?')
            end
          end
          sub = table.concat(s2, '+')
        end
      end
      parts[#parts + 1] = string.format('%d:%s[n=%d %s]', i, nm, cnt, sub)
    end
  else
    parts[#parts + 1] = 'cfg=0'
  end
  return 'MASKS ' .. table.concat(parts, ' ')
end

-- ★ 深度 dump：把 MaskGroupBasedCorrections 整个结构打出来（键名 + 值）。
--   用途：判断 rangeMask 是"独立一组"还是"并进上一组的 Masks（相交）"，
--   以及看清亮度范围的参数到底叫什么键、当前值是多少 —— 猜不出来只能读出来。
local function _ser(v, depth, out)
  if depth > 5 then out[#out + 1] = '~'; return end
  local t = type(v)
  if t ~= 'table' then
    out[#out + 1] = (t == 'string') and ('"' .. tostring(v) .. '"') or tostring(v)
    return
  end
  if #v > 0 then
    out[#out + 1] = '('
    for i = 1, math.min(#v, 16) do
      _ser(v[i], depth + 1, out); out[#out + 1] = ','
    end
    out[#out + 1] = ')'
  else
    out[#out + 1] = '{'
    local ks = {}
    for k in pairs(v) do ks[#ks + 1] = k end
    table.sort(ks, function(a, b) return tostring(a) < tostring(b) end)
    for _, k in ipairs(ks) do
      out[#out + 1] = tostring(k) .. ':'
      _ser(v[k], depth + 1, out)
      out[#out + 1] = ' '
    end
    out[#out + 1] = '}'
  end
end

local function mask_deep(path)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  local s = photo:getDevelopSettings()
  local out = { (hit and 'target=ok' or 'target=MISMATCH') }
  out[#out + 1] = 'groups=' .. tostring(masks_count() or 'n/a')
  out[#out + 1] = 'tiles=' .. tostring(mask_total() or 'n/a')
  local m = s and s.MaskGroupBasedCorrections
  if type(m) == 'table' then
    _ser(m, 0, out)
  else
    out[#out + 1] = 'MaskGroupBasedCorrections=nil'
  end
  local txt = table.concat(out, '')
  if #txt > 3800 then txt = txt:sub(1, 3800) .. '…' end
  return 'MASKSX ' .. txt
end

-- ★ 一次性探针：几何蒙版到底要什么前置动作才建得出来。
--   `createNewMask('radialGradient')` 返回 true 但蒙版数不变（与 rangeMask 同款症状），
--   所以这里按几种可能的前置动作顺次试，**成功即停**（避免留下多个残留蒙版）。
local function mask_probe(path, mtype)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  local out = { (hit and 'target=ok' or 'target=MISMATCH'), 'type=' .. tostring(mtype) }
  local done = false
  local function attempt(tag, fn)
    if done then return end
    local before = mask_total() or 0
    local ok, err = LrTasks.pcall(fn)
    -- 蒙版数不是同步更新的：多等几拍，别把"还没落库"当成"没建出来"
    local after = before
    for _ = 1, 5 do
      LrTasks.sleep(0.8)
      after = mask_total() or after
      if after > before then break end
    end
    out[#out + 1] = string.format('%s ok=%s n=%d->%d%s', tag, tostring(ok), before, after,
      (not ok) and (' ERR=' .. tostring(err):sub(1, 110)) or '')
    if after > before then done = true end
  end
  attempt('A直接', function() LrDevelopController.createNewMask(mtype) end)
  attempt('B先开面板', function()
    LrDevelopController.goToDevelopRadialFilter()
    LrTasks.sleep(1.0)
    LrDevelopController.createNewMask(mtype)
  end)
  -- ★ 新版 SDK 的 selectTool 只认 loupe/crop/dust/redeye/masking/upright/point_color/
  --   local_point_color —— **没有 circularGradient/radial**（旧文档写的那套已作废）。
  --   所以"进蒙版模式"只能走 selectTool('masking')。
  attempt('E进蒙版模式', function()
    LrDevelopController.selectTool('masking')
    LrTasks.sleep(1.0)
    LrDevelopController.createNewMask(mtype)
  end)
  attempt('F面板+蒙版模式', function()
    LrDevelopController.goToDevelopRadialFilter()
    LrTasks.sleep(0.8)
    LrDevelopController.selectTool('masking')
    LrTasks.sleep(0.8)
    LrDevelopController.createNewMask(mtype)
  end)
  -- 最后一次 flush，避免"只在内存里"
  LrTasks.pcall(function()
    LrApplication.activeCatalog():withWriteAccessDo('probe flush', function() end, { timeout = 20 })
  end)
  LrTasks.sleep(0.6)
  out[#out + 1] = 'final_tiles=' .. tostring(mask_total() or -1)
  return 'MASKPROBE ' .. table.concat(out, ' | ')
end

-- ===========================================================================
-- ★★ 实时状态 / 实时数据（2026-10-21 加）：拨一根旋钮之后，能不能立刻读到
--    ① 这根旋钮的当前值（LrDevelopController.getValue / catalog 表）
--    ② 变化事件（addDevelopSettingsObserver，若 SDK 有）
--    ③ 当前画面（requestJpegThumbnail，免导出）
-- ===========================================================================
local PROBE_CTRL = {
  'getValue', 'setValue', 'getDevelopSettings', 'getAllDevelopSettings',
  'addDevelopSettingsObserver', 'removeDevelopSettingsObserver',
  'addDevelopChangeObserver', 'removeDevelopChangeObserver',
  'requestJpegPreview', 'getSliderValue', 'setSliderValue',
}
local PROBE_PHOTO = {
  'requestJpegThumbnail', 'getDevelopSettings', 'applyDevelopSettings',
  'requestJpegPreview', 'getRawMetadata',
}

local function api_probe(path)
  local _, photo = find_photo(path)
  local out = {}
  for _, m in ipairs(PROBE_CTRL) do
    out[#out + 1] = string.format('ctrl.%s=%s', m, type(LrDevelopController[m]))
  end
  for _, m in ipairs(PROBE_PHOTO) do
    out[#out + 1] = string.format('photo.%s=%s', m, type(photo[m]))
  end
  return 'APIPROBE ' .. table.concat(out, ' ')
end

local function get_vals(path, keys)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  local s = photo:getDevelopSettings()
  local out = {}
  for k in (keys or 'Exposure2012'):gmatch('[^,]+') do
    local ok, v = LrTasks.pcall(function() return LrDevelopController.getValue(k) end)
    out[#out + 1] = string.format('%s(ctrl=%s tab=%s)', k,
      ok and tostring(v) or 'ERR', tostring(s[k]))
  end
  return 'GETVAL ' .. (hit and 'target=ok' or 'target=MISMATCH') .. ' '
    .. table.concat(out, ' ')
end

-- 拨一根旋钮（setValue），然后立刻读回：回答"动一根能不能拿到状态"
local function set_one(path, key, value)
  local _, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  if not hit then return 'SETONE target=MISMATCH (skipped)' end
  local v = tonumber(value)
  if v == nil then
    if value == 'true' then v = true elseif value == 'false' then v = false else v = value end
  end
  local before = LrDevelopController.getValue(key)
  local ok, err = LrTasks.pcall(function() LrDevelopController.setValue(key, v) end)
  LrTasks.sleep(0.3)
  local after = LrDevelopController.getValue(key)
  local s = photo:getDevelopSettings()
  return string.format('SETONE %s set=%s ok=%s ctrl %s->%s tab=%s%s',
    key, tostring(value), tostring(ok), tostring(before), tostring(after),
    tostring(s[key]), ok and '' or (' err=' .. tostring(err)))
end

-- 当前画面（预览缓存里的 JPEG），免一次导出
local function thumb(path, outfile, size)
  local _, photo = find_photo(path)
  activate_develop(photo)
  local sz = tonumber(size) or 1024
  local data, reason, fired = nil, nil, false
  local t0 = os.clock()
  LrApplication.activeCatalog():withReadAccessDo(function()
    photo:requestJpegThumbnail(sz, sz, function(jpeg, r)
      data, reason, fired = jpeg, r, true
    end)
  end)
  local t = 0
  while not fired and t < 6 do
    LrTasks.sleep(0.02)
    t = t + 0.02
  end
  if not data then
    return string.format('THUMB failed fired=%s reason=%s', tostring(fired), tostring(reason))
  end
  local f = io.open(outfile, 'wb')
  if not f then return 'THUMB cannot write ' .. outfile end
  f:write(data)
  f:close()
  return string.format('THUMB ok bytes=%d wait=%.2fs -> %s', #data, t, outfile)
end

-- 变化观察器（若 SDK 有）：把每次变化的键与值追加到文件
local WATCH = { on = false, fh = nil, keys = nil }
local function watch_start(path, outfile, keys)
  local _, photo = find_photo(path)
  activate_develop(photo)
  if WATCH.on then
    LrTasks.pcall(function() LrDevelopController.removeDevelopSettingsObserver(WATCH.cb) end)
    WATCH.on = false
  end
  if type(LrDevelopController.addDevelopSettingsObserver) ~= 'function' then
    return 'WATCH unsupported (no addDevelopSettingsObserver)'
  end
  local names = {}
  for k in (keys or 'Exposure2012'):gmatch('[^,]+') do names[#names + 1] = k end
  local fh = io.open(outfile, 'w')
  if not fh then return 'WATCH cannot write ' .. outfile end
  WATCH.fh = fh
  WATCH.keys = names
  WATCH.cb = function(...)
    local a = { ... }
    local parts = {}
    for i = 1, #a do
      local v = a[i]
      if type(v) == 'table' then
        local ps = {}
        for k, vv in pairs(v) do ps[#ps + 1] = tostring(k) .. '=' .. tostring(vv) end
        parts[#parts + 1] = '{' .. table.concat(ps, ',') .. '}'
      else
        parts[#parts + 1] = tostring(v)
      end
    end
    WATCH.fh:write(string.format('%.2f %s\n', os.time(), table.concat(parts, ' ')))
    WATCH.fh:flush()
  end
  local ok, err = LrTasks.pcall(function()
    LrDevelopController.addDevelopSettingsObserver(WATCH.cb, names)
  end)
  WATCH.on = ok
  return string.format('WATCH ok=%s keys=%s%s -> %s', tostring(ok),
    table.concat(names, ','), ok and '' or (' err=' .. tostring(err)), outfile)
end

local function watch_stop()
  if WATCH.on and WATCH.cb then
    LrTasks.pcall(function() LrDevelopController.removeDevelopSettingsObserver(WATCH.cb) end)
  end
  if WATCH.fh then WATCH.fh:close() end
  WATCH.on, WATCH.fh, WATCH.cb = false, nil, nil
  return 'WATCH stopped'
end



local function export_tif(outdir, longedge)
  local catalog, photos = get_photos()
  LrFileUtils.createAllDirectories(outdir)
  local settings = {
    LR_export_destinationType = 'specificFolder',
    LR_export_destinationPathPrefix = outdir,
    LR_export_useSubfolder = false,
    LR_format = 'TIFF',
    LR_tiff_bitDepth = 16,
    -- LZW 无损：无压缩 2048 要写 16 MB，压掉约 40%，导出时间跟着下来。
    LR_tiff_compressionMethod = 'compressionMethod_LZW',
    -- ★★ 长边可选：判读用 1024（快 4 倍，直方图区间占比和肤色 C* 不受影响），
    --    终验/交付用全尺寸。不传 = 不限制（原尺寸出）。
    LR_size_doConstrain = (longedge ~= nil),
    LR_size_resizeType = 'longEdge',
    LR_size_longEdge = longedge or 2048,
    LR_size_units = 'pixels',
    LR_size_dontEnlarge = true,
    LR_outputSharpeningOn = false,
    -- ★★ 必须 sRGB，不能 ProPhotoRGB：测出来的数要和参照物（鹿井成片 / 同场景机内 JPG）
    --    在同一色彩空间里比。导 ProPhoto 却按 sRGB 读，实测 p50 差 6 码、C* 差 5.7（10-22）。
    LR_export_colorSpace = 'sRGB',
    LR_export_overwriteWithoutPrompting = true,
  }
  -- ★ 先删同名导出：否则 LR 弹「文件已存在」模态框，桥会永久卡住（10-21 真踩）
  for _, p in ipairs(photos) do
    local path = p:getRawMetadata('path')
    if path then
      local base = LrPathUtils.removeExtension(LrPathUtils.leafName(path))
      for _, ext in ipairs({ '.tif', '.tiff', '.TIF' }) do
        local cand = LrPathUtils.child(outdir, base .. ext)
        if LrFileUtils.exists(cand) then LrFileUtils.delete(cand) end
      end
    end
  end
  local session = LrExportSession({ photosToExport = photos, exportSettings = settings })
  session:doExportOnCurrentTask()
  local n = 0
  for _, p in ipairs(photos) do
    local path = p:getRawMetadata('path')
    local base = LrPathUtils.removeExtension(LrPathUtils.leafName(path))
    for _, ext in ipairs({ '.tif', '.tiff', '.TIF' }) do
      if LrFileUtils.exists(LrPathUtils.child(outdir, base .. ext)) then n = n + 1 break end
    end
  end
  return n
end




-- MASKPARAM <path> <key> <value> [index]：读表 -> 只改一个局部参数 -> 整表写回（保住 AI 蒙版字段）
local function mask_param(path, key, value, idx)
  local catalog, photo = find_photo(path)
  local tpath = activate_develop(photo)
  local hit = target_matches(photo, tpath)
  if not hit then return 'MASKPARAM target=MISMATCH' end
  local i = tonumber(idx) or 1
  local v = tonumber(value)
  if v == nil then v = (value == 'true') or value end
  local err = nil
  catalog:withWriteAccessDo('svFilm mask param', function()
    local ok, e = LrTasks.pcall(function()
      local st = photo:getDevelopSettings()
      local gs = st.MaskGroupBasedCorrections
      if type(gs) ~= 'table' or not gs[1] or not gs[1].CorrectionMasks[1] then
        error('no mask')
      end
      gs[1][key] = v   -- ★ 局部参数挂在【组】上，不是挂在蒙版条目上
      photo:applyDevelopSettings({ MaskGroupBasedCorrections = gs }, 'svFilm')
    end)
    if not ok then err = tostring(e) end
  end, { timeout = 60 })
  LrTasks.sleep(1.0)
  local st2 = photo:getDevelopSettings()
  local got = nil
  if type(st2.MaskGroupBasedCorrections) == 'table' and st2.MaskGroupBasedCorrections[1]
     and st2.MaskGroupBasedCorrections[1].CorrectionMasks[1] then
    got = st2.MaskGroupBasedCorrections[1][key]
  end
  return string.format('MASKPARAM %s=%s idx=%d readback=%s%s', key, tostring(v), i,
    tostring(got), err and (' err=' .. err) or '')
end

local function handle(cmd)
  local verb, rest = cmd:match('^(%S+)%s*(.*)$')
  if not verb then error('empty command') end
  verb = verb:upper()
  if verb == 'PING' then return 'PONG' end
  if verb == 'MASKPARAM' then
    local p, k, v, i = rest:match('^(%S+)%s+(%S+)%s+(%S+)%s*(%S*)$')
    if not p then error('MASKPARAM needs <path> <key> <value> [idx]') end
    return mask_param(p, k, v, i ~= '' and i or nil)
  end
  if verb == 'CURVE' then
    local p, k, v = rest:match('^(%S+)%s+(%S+)%s+(%S+)$')
    if not p then error('CURVE needs <path> <key> <x,y,...>') end
    return set_curve(p, k, v)
  end
  if verb == 'READMETA' then
    return read_meta(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'SETCURVE' then
    local p, k, v = rest:match('^(%S+)%s+(%S+)%s+(%S+)$')
    if not p then error('SETCURVE needs <path> <key> <x,y,...>') end
    return set_curve_live(p, k, v)
  end
  if verb == 'CURVEP' then
    local p, k, v = rest:match('^(%S+)%s+(%S+)%s+(%S+)$')
    if not p then error('CURVEP needs <path> <key> <x,y,...>') end
    return set_curve_partial(p, k, v)
  end
  if verb == 'DUMPCURVE' then
    local p, k = rest:match('^(%S+)%s*(%S*)$')
    if not p then error('DUMPCURVE needs <path> [key]') end
    return dump_curve(p, (k ~= '' and k) or 'ToneCurvePV2012')
  end
  if verb == 'CURVEFMT' then
    local p, k, v, m = rest:match('^(%S+)%s+(%S+)%s+(%S+)%s*(%S*)$')
    if not p then error('CURVEFMT needs <path> <key> <x,y,...> [xy|pos|norm|flat]') end
    return set_curve_fmt(p, k, v, (m ~= '' and m) or 'xy')
  end
  if verb == 'CURVES' then
    local p, spec = rest:match('^(%S+)%s+(.+)$')
    if not p then error('CURVES needs <path> Key1:x,y,...;Key2:...') end
    return set_curves_multi(p, spec)
  end
  if verb == 'EXPORTTIF' then
    -- EXPORTTIF <outdir> [longEdge]
    local od, edge = rest:match('^(.-)%s+(%d+)%s*$')
    if od and od ~= '' then return 'EXPORTED_TIF ' .. tostring(export_tif(od, tonumber(edge))) end
    return 'EXPORTED_TIF ' .. tostring(export_tif(rest))
  end
  if verb == 'APIPROBE' then
    return api_probe(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'GETVAL' then
    local path, keys = rest:match('^(%S+)%s*(%S*)$')
    return get_vals(path, keys ~= '' and keys or nil)
  end
  if verb == 'SETONE' then
    local path, key, val = rest:match('^(%S+)%s+(%S+)%s+(%S+)$')
    if not path then error('SETONE needs <path> <key> <value>') end
    return set_one(path, key, val)
  end
  if verb == 'THUMB' then
    local path, out, sz = rest:match('^(%S+)%s+(%S+)%s*(%S*)$')
    if not path or not out then error('THUMB needs <path> <outfile> [size]') end
    return thumb(path, out, sz ~= '' and sz or nil)
  end
  if verb == 'WATCH' then
    local path, out, keys = rest:match('^(%S+)%s+(%S+)%s*(%S*)$')
    if not path or not out then error('WATCH needs <path> <outfile> [keys]') end
    return watch_start(path, out, keys ~= '' and keys or nil)
  end
  if verb == 'WATCHSTOP' then
    return watch_stop()
  end
  if verb == 'SELECT' then
    local p = select_path(rest)
    return 'SELECTED ' .. (p:getRawMetadata('path') or '')
  end
  if verb == 'EXPORT' then
    return 'EXPORTED ' .. tostring(export_photos(rest))
  end
  if verb == 'DUMP' then
    return 'DUMP ' .. dump_path(rest)
  end
  if verb == 'ONE' then
    local path, outdir = rest:match('^(%S+)%s+(.+)$')
    if not path or not outdir then error('ONE needs <path> <outdir>') end
    local _, photo = find_photo(path)
    apply_one(photo)
    local target = export_one(photo, outdir)
    return 'ONE ' .. target
  end
  if verb == 'KEYS' then
    local _, photo = find_photo(rest)
    local s = photo:getDevelopSettings()
    local keys = {}
    for k in pairs(s) do
      if k:match('Glow') or k:match('Halation') or k:match('Bloom')
         or k:match('Grain') or k:match('Texture') or k:match('Clarity')
         or k:match('Dehaze') or k:match('Diffusion') then
        keys[#keys + 1] = k .. '=' .. tostring(s[k])
      end
    end
    table.sort(keys)
    return 'KEYS ' .. table.concat(keys, '; ')
  end
  if verb == 'MASK' then
    local path, tail = rest:match('^(%S+)%s*(.*)$')
    if not path then error('MASK needs <path> [type] [k=v,...]') end
    local mtype, adj = tail:match('^(%a*)%s*(.*)$')
    return add_mask(path, mtype or '', adj)
  end
  if verb == 'SETGRAD' then
    local path, payload = rest:match('^(%S+)%s*(%S*)$')
    if not path then error('SETGRAD needs <path> <payload>') end
    return set_grad(path, payload)
  end
  if verb == 'SETMASKS' then
    local path, payload = rest:match('^(%S+)%s*(%S*)$')
    if not path then error('SETMASKS needs <path> <payload>') end
    return set_masks(path, payload)
  end
  if verb == 'LOCAL' then
    local path, spec = rest:match('^(%S+)%s+(.+)$')
    if not path or not spec then error('LOCAL needs <path> <local_Key=value,...>') end
    return set_local(path, spec)
  end
  if verb == 'MASKS' then
    return mask_dump(rest)
  end
  if verb == 'MASKSX' then    return mask_deep(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'MASKPROBE' then
    local path, mtype = rest:match('^(%S+)%s*(%S*)$')
    if not path then error('MASKPROBE needs <path> [type]') end
    return mask_probe(path, (mtype ~= '' and mtype) or 'radialGradient')
  end
  if verb == 'RESET' then
    return reset_develop(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'CLEARMASKS' then
    return clear_masks(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'ADD' then
    return add_photo(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'REMOVE' then
    return remove_path(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'APPLY' then
    return apply_path(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'PRESET' then
    return apply_preset(rest:gsub('^%s+', ''):gsub('%s+$', ''))
  end
  if verb == 'LIVE' then
    local path, spec = rest:match('^(%S+)%s+(.+)$')
    if not path or not spec then error('LIVE needs <path> <Key=value,...>') end
    return set_live(path, spec)
  end
  if verb == 'GLOBAL' then
    local path, spec = rest:match('^(%S+)%s+(.+)$')
    if not path or not spec then error('GLOBAL needs <path> <Key=value,...>') end
    return set_global(path, spec)
  end
  if verb == 'APPLY_EXPORT' then
    local a, m, f = apply_selected()
    local n = export_photos(rest)
    return string.format('APPLIED %d MISSING %d FAILED %d EXPORTED %d', a, m, f, n)
  end
  error('unknown command: ' .. verb)
end

local function bridge_loop()
    LrFileUtils.createAllDirectories(CMD_DIR)
    -- ★★ 启动时先清掉上一次残留的命令（10-22 踩的坑）。
    --    开机时 cmd.txt 里若还压着一条上轮没消费完的命令，会被当成本轮的第一条。
    if LrFileUtils.exists(CMD) then
      LrTasks.pcall(function() os.remove(CMD) end)
    end
    write_result('READY')
    blog('loop: enter')
    local tick = 0
    while true do
      tick = tick + 1
      -- 心跳：每 ~60 s 记一次（只用来确认循环没死）
      if tick % 120 == 0 then
        blog('alive tick=' .. tick)
      end
      -- ★ 循环体整段 pcall：任何一条命令把 task 弄死，都等于桥永久失联。
      --    宁可这一条返回 ERR，也不能让循环退出。
      local body_ok, body_err = LrTasks.pcall(function()
        local raw = read_cmd()
        if raw and raw:gsub('%s+', '') ~= '' then
          blog('cmd:' .. #raw .. ' ' .. tostring(raw):sub(1, 60):gsub('\n', ' '))
          remove_cmd()
          -- 可选命令 id：第一行 @id，结果回 "<id>|OK ..."；Python 只认自己的 id，
          -- 避免上一条命令的 result 被下一条误读（文件队列的经典竞态）。
          local id, cmd = raw:match('^@([%w%-_]+)\r?\n(.*)$')
          cmd = cmd or raw
          local ok, res = LrTasks.pcall(handle, cmd)
          blog('done ok=' .. tostring(ok))
          local prefix = id and (id .. '|') or ''
          write_result(prefix .. (ok and 'OK ' or 'ERR ') .. tostring(res))
        end
      end)
      if not body_ok then blog('BODY ERR ' .. tostring(body_err)) end
      LrTasks.sleep(0.5)
    end
end

local function start()
  if _G.__svfilm_bridge_running then return end
  _G.__svfilm_bridge_running = true
  -- ★★ 必须用 LrTasks.startAsyncTask，不能用 postAsyncTaskWithContext（10-22 踩死）：
  --    postAsyncTaskWithContext 的 task 绑在调用方那个 LrFunctionContext 上，
  --    PluginInit 的上下文一结束（start() 返回）task 就被掐掉——表现为日志里
  --    只有 "loop: enter"、READY 之后一条命令都不处理，心跳也不再打，但 LR 好好的。
  --    startAsyncTask 起的是独立协程，可以正常 yield，活到 LR 退出为止。
  -- 启动失败必须把标记清掉：否则后续每次初始化都被上面那行直接挡回去。
  local started_ok, started_err = LrTasks.pcall(function()
    LrTasks.startAsyncTask(bridge_loop, 'svFilmBridge')
  end)
  if not started_ok then
    _G.__svfilm_bridge_running = false
    write_result('START FAILED ' .. tostring(started_err))
    blog('START FAILED ' .. tostring(started_err))
  end
end

return { start = start }
