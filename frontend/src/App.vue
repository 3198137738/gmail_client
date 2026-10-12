<script setup>
import {
  NConfigProvider,
  NDialogProvider,
  NLoadingBarProvider,
  NMessageProvider,
  NNotificationProvider,
  dateZhCN,
  zhCN,
} from "naive-ui";
import { themeOverrides } from "./theme";
import UiBridge from "./components/UiBridge.vue";
import AppSnackbar from "./components/AppSnackbar.vue";
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides" abstract>
    <NLoadingBarProvider>
      <NDialogProvider>
        <NNotificationProvider placement="bottom-left">
          <NMessageProvider placement="top" :max="3">
            <UiBridge />
            <RouterView />
            <AppSnackbar />
          </NMessageProvider>
        </NNotificationProvider>
      </NDialogProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
