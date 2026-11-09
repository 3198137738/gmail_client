// Naive UI 主题：靛蓝主色、较大圆角、略紧凑的控件。
// 页面自己的样式通过 CSS 变量（--gm-*，见 components/ThemeVars.vue）和设计令牌（ui/tokens.css）取值，切换深浅色时随之变化。
const font = 'system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "Helvetica Neue", Arial, sans-serif';

// 主色：浅色 / 深色模式各一套（深色背景上用更亮的蓝）
const PRIMARY = {
  light: { primaryColor: "#3b6cf6", primaryColorHover: "#5a85f8", primaryColorPressed: "#2c55d1", primaryColorSuppl: "#5a85f8" },
  dark: { primaryColor: "#7aa2ff", primaryColorHover: "#94b4ff", primaryColorPressed: "#6189ec", primaryColorSuppl: "#94b4ff" },
};

// 面板之间露出的画布颜色
export const CANVAS = { light: "#eef0f4", dark: "#0c0c0f" };

export function themeOverrides(dark) {
  return {
    common: {
      fontFamily: font,
      ...PRIMARY[dark ? "dark" : "light"],
      borderRadius: "8px",
      borderRadiusSmall: "6px",
      heightMedium: "34px",
      fontSizeMedium: "14px",
    },
    Button: { fontWeight: "500" },
    Card: { borderRadius: "14px" },
    Dialog: { borderRadius: "14px" },
    Dropdown: { optionHeightMedium: "34px", borderRadius: "10px" },
    Popover: { borderRadius: "10px" },
    Tag: { borderRadius: "6px" },
    Tooltip: { padding: "6px 10px" },
    Menu: { itemHeight: "34px" },
  };
}

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
