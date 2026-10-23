-- svFilm Bridge: 读 <stem>.svfilm.lr.txt，把 lr.settings 应用到选中的照片。
-- 只做 apply，不导出。依赖 Lightroom Classic SDK 6.0+。

local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local LrProgressScope = import 'LrProgressScope'
local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'

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
  -- applyDevelopSettings 对“部分表”在部分 LR 版本上会失败；先从当前 develop
  -- settings 取全表，再覆盖我们的键，最后整表应用。
  if type(full) ~= 'table' then
    -- 拿不到完整表就退回部分表（可能失败，但至少不在写锁里 yield）
    photo:applyDevelopSettings(settings, 'svFilm')
    return
  end
  for k, v in pairs(settings) do
    if k:match('^ToneCurvePV') then
      local existing = full[k]
      if type(existing) == 'table' and type(existing[1]) == 'table' then
        -- LR 返回 [{x=..., y=...}, ...] 时，把扁平列表转回去
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
  local missing_names = {}
  local first_error = nil
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
        if ok then
          applied = applied + 1
        else
          failed = failed + 1
          if first_error == nil then first_error = tostring(err) end
        end
      else
        missing = missing + 1
        local p = photo:getRawMetadata('path') or '?'
        missing_names[#missing_names + 1] = LrPathUtils.leafName(p)
      end
      if progress then
        progress:setPortionComplete(i, #photos)
      end
    end
  end)

  return applied, missing, failed, missing_names, first_error
end

LrTasks.startAsyncTask(function()
  local catalog = LrApplication.activeCatalog()
  local photos = catalog:getTargetPhotos()
  if #photos == 0 then
    LrDialogs.message('svFilm v1.0.2', '先在图库/胶片条里选中照片。')
    return
  end

  local progress = LrProgressScope({ title = 'svFilm: apply metadata' })
  local applied, missing, failed, names, first_error = apply_to(photos, progress)
  progress:done()

  local msg = string.format('应用 %d 张；缺元数据 %d 张；应用失败 %d 张。', applied, missing, failed)
  if first_error then msg = msg .. '\n失败原因: ' .. first_error end
  if missing > 0 then
    local head = {}
    for i = 1, math.min(5, #names) do
      head[#head + 1] = names[i]
    end
    msg = msg .. '\n缺: ' .. table.concat(head, '、')
  end
  LrDialogs.message('svFilm v1.0.2', msg)
end)
