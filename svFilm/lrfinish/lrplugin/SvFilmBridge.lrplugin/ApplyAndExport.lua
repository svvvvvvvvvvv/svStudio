-- svFilm Bridge: 应用 <stem>.svfilm.lr.txt 的 settings，然后导出 JPEG 到源目录/lr_export。
-- 实验性：导出参数写死为 JPEG 92 / 长边 2048 / sRGB；要别的请改这里。

local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local LrProgressScope = import 'LrProgressScope'
local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'
local LrExportSession = import 'LrExportSession'

local function sidecar_for(photo)
  local path = photo:getRawMetadata('path')
  if not path then return nil end
  local dir = LrPathUtils.parent(path)
  local base = LrPathUtils.removeExtension(path)
  local stem = base
  if stem:sub(-5) == '_flat' then
    stem = stem:sub(1, -6)
  end
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
        for i = 1, #v, 2 do
          pts[#pts + 1] = { x = v[i], y = v[i + 1] }
        end
        full[k] = pts
      elseif type(existing) == 'table' and type(existing[1]) == 'string' then
        local pts = {}
        for i = 1, #v, 2 do
          pts[#pts + 1] = tostring(v[i]) .. ', ' .. tostring(v[i + 1])
        end
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

local function apply_to(photos, progress)
  local catalog = LrApplication.activeCatalog()
  local applied, missing, failed = 0, 0, 0
  local fulls = {}
  for i, photo in ipairs(photos) do
    local ok, s = LrTasks.pcall(function() return photo:getDevelopSettings() end)
    fulls[i] = ok and s or nil
  end
  catalog:withWriteAccessDo('svFilm apply metadata', function()
    for i, photo in ipairs(photos) do
      local sidecar = sidecar_for(photo)
      local settings = sidecar and read_settings(sidecar) or nil
      if settings and settings.ToneCurveName2012 ~= nil then
        local ok, err = LrTasks.pcall(function()
          apply_with_full_table(photo, settings, fulls[i])
        end)
        if ok then applied = applied + 1 else failed = failed + 1 end
      else
        missing = missing + 1
      end
      if progress then progress:setPortionComplete(i, #photos) end
    end
  end)
  return applied, missing, failed
end

LrTasks.startAsyncTask(function()
  local catalog = LrApplication.activeCatalog()
  local photos = catalog:getTargetPhotos()
  if #photos == 0 then
    LrDialogs.message('svFilm v1.0.2', '先在图库/胶片条里选中照片。')
    return
  end

  local first = photos[1]:getRawMetadata('path')
  if not first then
    LrDialogs.message('svFilm v1.0.2', '选中的照片没有磁盘路径。')
    return
  end
  local outDir = LrPathUtils.child(LrPathUtils.parent(first), 'lr_export')
  LrFileUtils.createAllDirectories(outDir)

  local progress = LrProgressScope({ title = 'svFilm: apply + export' })
  local applied, missing, failed = apply_to(photos, progress)
  progress:setPortionComplete(1, 1)

  local exportSettings = {
    LR_export_destinationType = 'specificFolder',
    LR_export_destinationPathPrefix = outDir,
    LR_export_useSubfolder = false,
    LR_format = 'JPEG',
    LR_jpeg_quality = 0.92,
    LR_size_doConstrain = true,
    LR_size_maxWidth = 2048,
    LR_size_maxHeight = 2048,
    LR_size_units = 'pixels',
    LR_outputSharpeningOn = false,
    LR_export_colorSpace = 'sRGB',
  }

  local session = LrExportSession({
    photosToExport = photos,
    exportSettings = exportSettings,
  })
  session:doExportOnCurrentTask()
  progress:done()

  LrDialogs.message('svFilm v1.0.2',
    string.format('应用 %d 张，缺元数据 %d 张，应用失败 %d 张。\n导出到:\n%s', applied, missing, failed, outDir))
end)
