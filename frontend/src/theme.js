// Naive UI 主题：使用组件库默认配色（浅色 / 深色），只统一字体。
// 页面自己的样式通过 CSS 变量（--gm-*）取主题颜色，由 components/ThemeVars.vue 写入，切换深浅色时随之变化。
const font = 'system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "Helvetica Neue", Arial, sans-serif';

export const themeOverrides = {
  common: {
    fontFamily: font,
  },
};

// "#18a058" / "rgb(24, 160, 88)" / "rgba(...)" → "rgba(r, g, b, alpha)"
export function withAlpha(color, alpha) {
  let r = 0;
  let g = 0;
  let b = 0;
  const hex = /^#([0-9a-f]{3,8})$/i.exec(color || "");
  if (hex) {
    let h = hex[1];
    if (h.length <= 4) h = h.split("").map((c) => c + c).join("");
    r = parseInt(h.slice(0, 2), 16);
    g = parseInt(h.slice(2, 4), 16);
    b = parseInt(h.slice(4, 6), 16);
  } else {
    const nums = (color || "").match(/[\d.]+/g) || [];
    [r, g, b] = nums.map(Number);
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
