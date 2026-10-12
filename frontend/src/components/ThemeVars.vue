<script setup>
// 把 Naive UI 当前主题（浅色 / 深色）的颜色写成页面级 CSS 变量，供各组件的样式使用
import { watchEffect } from "vue";
import { useThemeVars } from "naive-ui";
import { withAlpha } from "../theme";

const props = defineProps({ dark: { type: Boolean, default: false } });
const vars = useThemeVars();

watchEffect(() => {
  const v = vars.value;
  const root = document.documentElement;
  const map = {
    "--gm-font": v.fontFamily,
    "--gm-bg": v.bodyColor,
    "--gm-surface": v.cardColor,
    "--gm-surface-2": v.actionColor,
    "--gm-popover": v.popoverColor,
    "--gm-text": v.textColor1,
    "--gm-text-2": v.textColor2,
    "--gm-text-3": v.textColor3,
    "--gm-text-4": v.textColorDisabled,
    "--gm-border": v.borderColor,
    "--gm-divider": v.dividerColor,
    "--gm-hover": v.hoverColor,
    "--gm-primary": v.primaryColor,
    "--gm-primary-hover": v.primaryColorHover,
    "--gm-primary-soft": withAlpha(v.primaryColor, 0.1),
    "--gm-selected": withAlpha(v.primaryColor, props.dark ? 0.22 : 0.14),
    "--gm-error": v.errorColor,
    "--gm-error-soft": withAlpha(v.errorColor, 0.12),
    "--gm-warning": v.warningColor,
    "--gm-warning-soft": withAlpha(v.warningColor, 0.14),
    "--gm-success": v.successColor,
    "--gm-info": v.infoColor,
    "--gm-star": v.warningColor,
    "--gm-tag": v.tagColor,
    "--gm-code": v.codeColor,
    "--gm-shadow-1": v.boxShadow1,
    "--gm-shadow-2": v.boxShadow2,
    "--gm-shadow-3": v.boxShadow3,
    "--gm-radius": v.borderRadius,
  };
  for (const [key, value] of Object.entries(map)) root.style.setProperty(key, value);
  root.dataset.theme = props.dark ? "dark" : "light";
  root.style.colorScheme = props.dark ? "dark" : "light";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", v.bodyColor);
});
</script>

<template>
  <span hidden />
</template>
