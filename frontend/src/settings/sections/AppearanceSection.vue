<script setup>
// 外观与阅读：主题、列表密度、阅读窗格、操作后的去向、会话视图、手机滑动手势。
// 除会话视图（服务器记在 cookie 中）外都是本机设置：手机和电脑可以各用各的。
import { onMounted, ref } from "vue";
import { NIcon, NRadio, NRadioGroup, NSelect, NSwitch } from "naive-ui";
import { BrightnessAutoOutlined, DarkModeOutlined, LightModeOutlined } from "@vicons/material";
import { get, post } from "../../api";
import { prefs } from "../../prefs";
import { store } from "../../store";
import { run, toast } from "../../ui";
import { SWIPE_CHOICES } from "../../mail/actions";

const THEMES = [
  { value: "light", label: "浅色", icon: LightModeOutlined },
  { value: "dark", label: "深色", icon: DarkModeOutlined },
  { value: "auto", label: "跟随系统", icon: BrightnessAutoOutlined },
];

const conversation = ref(null);
onMounted(async () => {
  try {
    conversation.value = (await get("/api/messages/conversation-view")).conversation;
  } catch {
    conversation.value = null;
  }
});

async function setConversation(on) {
  if (await run(post("/api/messages/conversation-view", { on }))) {
    conversation.value = on;
    store.listVersion += 1;
    toast(on ? "已开启会话视图" : "已关闭会话视图");
  }
}
</script>

<template>
  <div class="gm-settings">
    <p class="gm-settings-intro">这里的设置保存在这台设备的浏览器里（会话视图除外），手机和电脑可以分别设置。</p>

    <section class="gm-settings-row">
      <div class="gm-settings-label">主题</div>
      <div class="gm-settings-content">
        <div class="gm-ap-themes" role="radiogroup" aria-label="主题">
          <button
            v-for="t in THEMES"
            :key="t.value"
            type="button"
            role="radio"
            :aria-checked="prefs.theme === t.value"
            class="gm-ap-theme"
            :class="{ 'is-on': prefs.theme === t.value }"
            @click="prefs.theme = t.value"
          >
            <span class="gm-ap-preview" :class="`is-${t.value}`"><span /><span /><span /></span>
            <span class="gm-ap-theme-label"><NIcon :component="t.icon" :size="16" />{{ t.label }}</span>
          </button>
        </div>
      </div>
    </section>

    <section class="gm-settings-row">
      <div class="gm-settings-label">
        邮件列表
        <div class="gm-settings-hint">紧凑密度一屏显示更多邮件</div>
      </div>
      <div class="gm-settings-content gm-ap-stack">
        <NRadioGroup v-model:value="prefs.density" name="density">
          <NRadio value="comfortable">舒适（显示摘要和附件）</NRadio>
          <NRadio value="compact">紧凑</NRadio>
        </NRadioGroup>
      </div>
    </section>

    <section class="gm-settings-row">
      <div class="gm-settings-label">
        阅读
        <div class="gm-settings-hint">窗口宽度不够时（小于 1024 像素）总是整页阅读</div>
      </div>
      <div class="gm-settings-content gm-ap-stack">
        <div class="gm-ap-line">
          <span class="gm-ap-name">阅读窗格</span>
          <NRadioGroup v-model:value="prefs.readingPane" name="pane">
            <NRadio value="right">在列表右侧</NRadio>
            <NRadio value="off">关闭（整页阅读）</NRadio>
          </NRadioGroup>
        </div>
        <div class="gm-ap-line">
          <span class="gm-ap-name">归档 / 删除 / 延后后</span>
          <NRadioGroup v-model:value="prefs.afterAction" name="after">
            <NRadio value="next">打开下一封</NRadio>
            <NRadio value="list">回到列表</NRadio>
          </NRadioGroup>
        </div>
        <div class="gm-ap-line">
          <span class="gm-ap-name">会话视图</span>
          <NSwitch :value="!!conversation" :disabled="conversation === null" @update:value="setConversation" />
          <span class="gm-settings-hint gm-ap-inline">同一会话的往来邮件合并为一行（所有设备通用）</span>
        </div>
      </div>
    </section>

    <section class="gm-settings-row">
      <div class="gm-settings-label">
        滑动手势
        <div class="gm-settings-hint">手机、平板上在邮件列表中左右滑动时执行的操作；删除、归档可在提示中撤销</div>
      </div>
      <div class="gm-settings-content gm-ap-stack">
        <div class="gm-ap-line">
          <span class="gm-ap-name">向右滑</span>
          <NSelect v-model:value="prefs.swipeRight" :options="SWIPE_CHOICES" class="gm-ap-select" />
        </div>
        <div class="gm-ap-line">
          <span class="gm-ap-name">向左滑</span>
          <NSelect v-model:value="prefs.swipeLeft" :options="SWIPE_CHOICES" class="gm-ap-select" />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.gm-ap-themes { display: flex; flex-wrap: wrap; gap: var(--sp-3); }
.gm-ap-theme {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
    padding: var(--sp-2);
    border: 2px solid var(--gm-border);
    border-radius: var(--r-lg);
    background: none;
    color: var(--gm-text-2);
    font: inherit;
    cursor: pointer;
}
.gm-ap-theme.is-on { border-color: var(--gm-primary); color: var(--gm-primary); }
.gm-ap-preview { display: grid; grid-template-columns: 14px 32px 1fr; gap: 4px; width: 132px; height: 72px; padding: 6px; border-radius: var(--r-md); }
.gm-ap-preview span { border-radius: 4px; }
.gm-ap-preview.is-light { background: #eef0f4; }
.gm-ap-preview.is-light span { background: #fff; }
.gm-ap-preview.is-dark { background: #0c0c0f; }
.gm-ap-preview.is-dark span { background: #26262b; }
.gm-ap-preview.is-auto { background: linear-gradient(120deg, #eef0f4 50%, #0c0c0f 50%); }
.gm-ap-preview.is-auto span { background: color-mix(in srgb, #fff 50%, #26262b); }
.gm-ap-theme-label { display: flex; align-items: center; justify-content: center; gap: 4px; font-weight: 500; }
.gm-ap-stack { display: flex; flex-direction: column; gap: var(--sp-4); }
.gm-ap-line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2) var(--sp-4); }
.gm-ap-name { min-width: 140px; color: var(--gm-text-2); }
.gm-ap-inline { margin-top: 0; }
.gm-ap-select { width: 200px; }
</style>
