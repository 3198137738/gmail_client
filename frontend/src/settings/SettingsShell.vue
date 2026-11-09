<script setup>
// 设置：电脑端左侧分区导航 + 右侧内容；手机端先显示分区列表，点进去显示内容（顶部可返回）。
import { computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NIcon } from "naive-ui";
import { ArrowBackOutlined, ChevronRightOutlined } from "@vicons/material";
import GmIconButton from "../ui/GmIconButton.vue";
import { isMobile } from "../composables/common";
import { SECTION_GROUPS, sectionOf } from "./sections";

const route = useRoute();
const router = useRouter();
const current = computed(() => route.meta.settingsSection || null);
const currentSection = computed(() => sectionOf(current.value));

// 电脑端直接打开 /settings 时显示第一个分区
watch(
  () => [route.name, isMobile.value],
  ([name, mobile]) => {
    if (name === "settings" && !mobile) router.replace("/settings/accounts");
  },
  { immediate: true },
);
</script>

<template>
  <div class="gm-st" :class="{ 'is-mobile': isMobile, 'has-section': !!current }">
    <nav v-if="!isMobile || !current" class="gm-st-nav" aria-label="设置分区">
      <h2 class="gm-st-title">设置</h2>
      <section v-for="g in SECTION_GROUPS" :key="g.label" class="gm-st-group">
        <div class="gm-st-heading">{{ g.label }}</div>
        <RouterLink
          v-for="s in g.items"
          :key="s.key"
          :to="`/settings/${s.key}`"
          class="gm-st-item"
          :class="{ 'is-active': current === s.key || (s.key === 'accounts' && current === 'accounts-add') }"
        >
          <NIcon :component="s.icon" :size="20" />
          <span class="gm-st-text">
            <span class="gm-st-label">{{ s.label }}</span>
            <span v-if="isMobile" class="gm-st-hint">{{ s.hint }}</span>
          </span>
          <NIcon v-if="isMobile" :component="ChevronRightOutlined" :size="20" class="gm-st-chevron" />
        </RouterLink>
      </section>
    </nav>
    <main v-if="current" class="gm-st-main">
      <header v-if="isMobile" class="gm-st-back">
        <GmIconButton :icon="ArrowBackOutlined" title="返回设置" @click="router.push('/settings')" />
        <span>{{ currentSection?.label || "添加账号" }}</span>
      </header>
      <div class="gm-st-content">
        <RouterView />
      </div>
    </main>
  </div>
</template>

<style scoped>
.gm-st { display: flex; height: 100%; min-height: 0; background: var(--gm-surface); }
.gm-st-nav {
    flex: none;
    width: 232px;
    padding: var(--sp-4) var(--sp-2);
    border-right: 1px solid var(--gm-divider);
    overflow-y: auto;
}
.gm-st.is-mobile .gm-st-nav { width: 100%; border-right: 0; padding: var(--sp-3); }
.gm-st-title { margin: 0 0 var(--sp-2); padding: 0 10px; font-size: var(--fs-xl); font-weight: 600; }
.gm-st-group { margin-bottom: var(--sp-3); }
.gm-st-heading { padding: var(--sp-2) 10px var(--sp-1); font-size: var(--fs-xs); font-weight: 600; color: var(--gm-text-3); }
.gm-st-item {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 36px;
    padding: 0 10px;
    border-radius: var(--r-md);
    color: var(--gm-text-2);
}
.gm-st-item:hover { background: var(--gm-hover); color: var(--gm-text); }
.gm-st-item.is-active { background: var(--gm-selected); color: var(--gm-primary); font-weight: 600; }
.gm-st.is-mobile .gm-st-item { min-height: 56px; }
.gm-st-text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.gm-st-hint { font-size: var(--fs-xs); font-weight: 400; color: var(--gm-text-3); }
.gm-st-chevron { color: var(--gm-text-4); }
.gm-st-main { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }
.gm-st-back {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    min-height: 52px;
    padding: 0 var(--sp-2);
    border-bottom: 1px solid var(--gm-divider);
    font-weight: 600;
    flex: none;
}
.gm-st-content { flex: 1; min-height: 0; overflow-y: auto; background: var(--gm-bg); }
</style>
