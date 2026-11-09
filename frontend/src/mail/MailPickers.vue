<script setup>
// 邮件操作用到的全局选择器（目前是延后时间）
import TimePicker from "../components/TimePicker.vue";
import { settleTime, timePicker } from "./pickers";
</script>

<template>
  <TimePicker
    :show="timePicker.show"
    :title="timePicker.title"
    :confirm-text="timePicker.confirmText"
    @update:show="(v) => !v && settleTime(null)"
    @pick="(until) => settleTime(until)"
  />
</template>
