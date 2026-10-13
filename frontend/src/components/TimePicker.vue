<script setup>
// 选择一个未来的时间：常用预设（明天上午等）+ 自定义日期时间。定时发送、稍后提醒共用。
import { computed, ref, watch } from "vue";
import { NButton, NDatePicker, NModal } from "naive-ui";
import { cnTime } from "../format";

const props = defineProps({
  show: { type: Boolean, default: false },
  title: { type: String, default: "选择时间" },
  confirmText: { type: String, default: "确定" },
});
const emit = defineEmits(["update:show", "pick"]);

const WEEKDAYS = "日一二三四五六";
const custom = ref(null);

function at(daysAhead, hour, minute = 0) {
  const d = new Date();
  d.setDate(d.getDate() + daysAhead);
  d.setHours(hour, minute, 0, 0);
  return d;
}

function nextWeekday(weekday, hour) {
  const d = new Date();
  const diff = (weekday - d.getDay() + 7) % 7 || 7;
  return at(diff, hour);
}

function describe(d) {
  const today = new Date();
  const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((dayStart - at(0, 0)) / 86400000);
  const day = days === 0 ? "今天" : days === 1 ? "明天" : `${d.getMonth() + 1}月${d.getDate()}日周${WEEKDAYS[d.getDay()]}`;
  return `${day} ${cnTime(d)}` + (d.getFullYear() !== today.getFullYear() ? `（${d.getFullYear()}年）` : "");
}

const presets = computed(() => {
  if (!props.show) return [];
  const now = new Date();
  const items = [];
  if (now.getHours() < 17) items.push({ label: "今天晚些时候", when: at(0, 18) });
  items.push({ label: "明天上午", when: at(1, 8) });
  items.push({ label: "明天下午", when: at(1, 13) });
  if (now.getDay() >= 1 && now.getDay() <= 4) items.push({ label: "本周末", when: nextWeekday(6, 8) });
  items.push({ label: "下周一上午", when: nextWeekday(1, 8) });
  return items;
});

watch(
  () => props.show,
  (show) => {
    if (show) custom.value = at(1, 8).getTime();
  },
);

function pick(date) {
  emit("pick", date.getTime());
  emit("update:show", false);
}

function pickCustom() {
  if (!custom.value || custom.value <= Date.now()) return;
  pick(new Date(custom.value));
}

const disablePast = (ts) => ts < at(0, 0).getTime();
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="title"
    :bordered="false"
    style="width: min(420px, calc(100vw - 32px))"
    @update:show="(v) => emit('update:show', v)"
  >
    <div class="gm-time-presets">
      <button v-for="p in presets" :key="p.label" type="button" class="gm-time-preset" @click="pick(p.when)">
        <span>{{ p.label }}</span>
        <span class="gm-time-preset-when">{{ describe(p.when) }}</span>
      </button>
    </div>
    <div class="gm-time-custom">
      <div class="gm-time-custom-label">选择日期和时间</div>
      <NDatePicker
        v-model:value="custom"
        type="datetime"
        format="yyyy-MM-dd HH:mm"
        :is-date-disabled="disablePast"
        :actions="null"
        style="width: 100%"
      />
      <div v-if="custom && custom <= Date.now()" class="gm-time-error">请选择一个将来的时间</div>
    </div>
    <template #footer>
      <div class="gm-time-footer">
        <NButton @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :disabled="!custom || custom <= Date.now()" @click="pickCustom">{{ confirmText }}</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.gm-time-presets { display: flex; flex-direction: column; margin: -4px -8px 12px; }
.gm-time-preset {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    padding: 10px 8px;
    border: 0;
    border-radius: var(--gm-radius);
    background: none;
    color: var(--gm-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
}
.gm-time-preset:hover { background: var(--gm-hover); }
.gm-time-preset-when { color: var(--gm-text-3); }
.gm-time-custom { border-top: 1px solid var(--gm-divider); padding-top: 12px; }
.gm-time-custom-label { font-size: 13px; color: var(--gm-text-3); margin-bottom: 8px; }
.gm-time-error { margin-top: 6px; font-size: 12px; color: var(--gm-error); }
.gm-time-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
