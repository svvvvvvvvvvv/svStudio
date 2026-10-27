# sfFilm 与 LR 的分工：结合踩过的坑

> 一句话：**sfFilm 负责“这是什么胶片”，LR 负责“这张脸/这套光落在哪”。**  
> 前者是介质，后者是 placement + look。两边都碰同一个特征，就会互相顶——这是之前最大的坑。

---

## 1. 之前的坑，本质都是“分工重叠”

| 坑 | 重叠在哪 | 结果 |
|---|---|---|
| PE 追脸 / LR Exposure 追脸 | 两处都在做 placement | 脸 87.3 过曝，或者 PE 抬了 LR 又抬 |
| engine `amount` / LR Saturation | 两处都在管彩度 | 肤色 C 一高一低，互相抵消 |
| engine `scanner.black/white` / LR Whites/Blacks | 两处都在管端点 | 黑端被 engine 锁死，LR 再推就死黑 |
| engine `wb` / LR WB | 两处都在管白平衡 | 中性偏移，肤色 h 乱 |
| engine grain / LR Grain/Clarity | 两处都在补纹理 | 颗粒双补，1080 黑发白点 |
| engine `gamma` / LR 曲线/CG | 两处都在管形状/颜色 | 曲线和化学互相盖 |
| person_pe / LR 蒙版 | 两处都在做局部落点 | 840 行删了又议 |

**根因**：没有把“介质”和“placement/look”切开；engine 的旋钮和 LR 的旋钮都能动同一个特征。

---

## 2. 正确的分工

### sfFilm = 介质 + 胶片曝光（固定的物理底）

管：
- **卷 / 纸**（portra_400 + endura / gold_200 + endura）
- **化学**（film_chemistry / print_chemistry / couplers 的彩度分配）
- **颗粒**（rms / blur / dye）
- **光晕**（halation / diffusion）
- **扫描**（scan_unsharp / lens_blur / black/white scan）
- **画幅**（format_mm）
- **印相曝光**（print_exposure）——**固定在该介质上**，不追卡的脸
- **WB**——**胶片/扫描的白平衡**，不是场景 WB

不管：
- 脸落点、高光/阴影、白/黑端（LR 的六旋钮）
- 肤色 h/C（LR HSL）
- 阴影/高光染色（LR Split Toning / CG）
- 曲线形状（LR Tone Curve）

### LR = placement + look（卡的风格）

管：
- **Basic 六旋钮**：Exposure（脸 L）、Whites/Blacks（前景端点）、Highlights/Shadows（前景 p95/p5）、Contrast（前景 p25–p75）
- **Tone Curve**：形状（≤16 点；端点锁）
- **HSL**：橙/黄（肤色）、绿/蓝（背景）
- **Split Toning / Color Grading**：阴影/高光 a/b
- **Camera Calibration**：蓝原色（整体色偏）
- **Lens / Transform**：镜头/透视

不管：
- 卷/纸/化学/颗粒/光晕/扫描/画幅（engine）
- 印相曝光（engine 固定）
- 胶片 WB（engine 固定）

---

## 3. 一条边界测试

> **如果这个旋钮改变的是“胶片本身的物理响应”，归 sfFilm；**  
> **如果改变的是“这张图落在哪、看起来什么色调”，归 LR。**

- 颗粒、光晕、纸、化学、扫描、画幅 → sfFilm
- 曝光、对比、高光/阴影、白/黑端、曲线、HSL、Split Toning、相机校准 → LR

---

## 4. 为什么这样分，能避开之前的坑

1. **PE 不再追脸**：engine 的 `print_exposure` 固定在该介质上；脸由 LR 的 Exposure 管。不会再出现“PE 1.10 + LR 0 + 曲线 87.3”。
2. **amount 不再和 Saturation 抢**：engine 的 `couplers.amount` 是介质的彩度分配，固定；肤色的 C 由 LR 的 HSL 橙/黄管。
3. **黑/白端不再双锁**：engine 的 `scanner.black/white` 是扫描的端点，固定；LR 的 Whites/Blacks 是 grade 的端点。
4. **WB 不再双调**：engine 的 WB 是胶片/扫描的；LR 的 WB 是场景的；两者分工明确。
5. **grain 不再双补**：engine 出颗粒；LR 不加 Grain/Clarity。
6. **曲线/化学不再互相盖**：engine 的 gamma 固定；LR 的曲线只做形状。
7. **person_pe 不再回来**：局部落点在 LR 的蒙版/HSL，不在 engine 的第二遍印相。

---

## 5. 对当前 v2 的具体含义

- `卡38/preset.json` 的 `print_exposure` 0.694 是**介质固定值**，不是“卡 38 的 PE”；不再按卡调。
- `couplers.amount` 1.00、`scanner.black/white`、`wb` 也是介质固定值；卡 38 的 C/黑白端由 LR 管。
- 卡 38 的 target（脸 70.2–79.0、前景 p5–p95、shadow/high a/b、skin h/C）**全部是 LR 的靶**，不是 engine 的。
- 介质 target（grain/halation/paper/chemistry/format）是 engine 的靶；卡 38 的介质签名已经量了（`card_medium_38`）。
- 测量域：engine 出 1080 sRGB；LR 也在 1080 量；不要拿 2048 TIF 直接和卡比。

---

## 6. 还差的一步

- engine 的 `print_exposure` 固定值要**按介质标定**（不是 0.694 拍脑袋）；这属于介质标定，不属于卡。
- LR 的 **Camera Calibration 蓝原色**还没接；真键要先验证。
- HSL 要从“小步”变成“按卡反解”；这是 LR 的 owner，不是 engine。

---

## 7. 一句话汇报

**sfFilm 是“胶片”，LR 是“暗房”。**  
胶片决定颗粒、光晕、纸、化学、扫描、画幅；暗房决定脸落在哪、高光阴影、黑白端、曲线、肤色、染色。  
之前所有“头疼医头”的根源，都是让胶片去干暗房的活，或者让暗房去改胶片。分工切开，v2 才能稳。
