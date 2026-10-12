<script setup>
import { computed } from "vue";
import {
  NConfigProvider,
  NDialogProvider,
  NLoadingBarProvider,
  NMessageProvider,
  NNotificationProvider,
  darkTheme,
  dateZhCN,
  useOsTheme,
  zhCN,
} from "naive-ui";
import { themeOverrides } from "./theme";
import { store } from "./store";
import UiBridge from "./components/UiBridge.vue";
import ThemeVars from "./components/ThemeVars.vue";
import AppSnackbar from "./components/AppSnackbar.vue";

const osTheme = useOsTheme();
const dark = computed(() => (store.themeMode === "auto" ? osTheme.value === "dark" : store.themeMode === "dark"));
</script>

<template>
  <NConfigProvider
    :theme="dark ? darkTheme : null"
    :locale="zhCN"
    :date-locale="dateZhCN"
    :theme-overrides="themeOverrides"
    abstract
  >
    <NLoadingBarProvider>
      <NDialogProvider>
        <NNotificationProvider placement="bottom-left">
          <NMessageProvider placement="top" :max="3">
            <ThemeVars :dark="dark" />
            <UiBridge />
            <RouterView />
            <AppSnackbar />
          </NMessageProvider>
        </NNotificationProvider>
      </NDialogProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
