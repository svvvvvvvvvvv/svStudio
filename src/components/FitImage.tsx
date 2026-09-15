/**
 * 大图（一张图，永远「适应」）。
 *
 * ## 存在的理由（**别把它改回 `maxWidth/maxHeight:100%`**）
 * `max-width/max-height: 100%` 里的百分比**依赖父元素高度是否"确定"**，
 * 一旦某条 flex 链上高度不确定，百分比就被当成 `none` ⇒ 图按**原尺寸**渲染
 * ⇒ 溢出后被外层 `overflow:hidden` **裁掉**（SV 09-15 报的「小窗下两张图被裁」）。
 * 现在改成：图区 `position:relative`，图 `position:absolute` + `calc(100% - 2*pad)`
 * + `object-fit:contain` —— 尺寸**确定**、比例**一定保持**、永远不裁。
 *
 * ## 为什么**没有**缩放了（★★ 09-15 SV 定：「两图总用自适应，删除其他的」）
 * 09-15 白天这里有过「滚轮缩放 + 适应/1:1 两档 + 双击切换 + 百分比徽标」（当时给"验收皮肤"
 * 用的），当晚 SV 拍板**全删**：调色台那两张图**一律适应**（其它缩放档一个不留）。
 * ⇒ 这个组件现在只干一件事：把一张图**完整地**放进父容器。
 * ⚠ 别再"顺手"加回滚轮/双击/倍率 —— 要加先问 SV（这是被明确删掉的功能，不是没做完）。
 */
const PAD = 8;

export function FitImage({
  src,
  alt,
  pad = PAD,
}: {
  src: string;
  /** ⚠ 同时是 `img` 的 alt —— 布局自检靠 `alt === '调色后'` / `'原图'` 认这一栏，别乱改 */
  alt: string;
  pad?: number;
}) {
  return (
    <div
      data-fit-img={alt}
      style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}
    >
      <img
        src={src}
        alt={alt}
        draggable={false}
        style={{
          position: 'absolute',
          top: pad,
          left: pad,
          width: `calc(100% - ${pad * 2}px)`,
          height: `calc(100% - ${pad * 2}px)`,
          objectFit: 'contain',
          display: 'block',
        }}
      />
    </div>
  );
}
