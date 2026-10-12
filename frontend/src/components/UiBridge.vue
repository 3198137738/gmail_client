<script setup>
// 把 Naive UI 的 message / dialog 等实例交给 ui.js，组件之外（如接口出错时）也能弹出提示
import { useDialog, useLoadingBar, useMessage, useNotification } from "naive-ui";
import { ui } from "../ui";

ui.message = useMessage();
ui.dialog = useDialog();
ui.notification = useNotification();
ui.loadingBar = useLoadingBar();
</script>

<template>
  <span hidden />
</template>
