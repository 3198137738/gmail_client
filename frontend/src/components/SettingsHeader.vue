<script setup>
// Gmail 式设置页标题与标签页
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NTabs, NTab } from "naive-ui";

const route = useRoute();
const router = useRouter();

const TABS = [
  { name: "accounts", label: "账号和导入", path: "/accounts/" },
  { name: "blocked", label: "过滤器和屏蔽的地址", path: "/messages/blocked-senders" },
  { name: "templates", label: "模板", path: "/accounts/templates" },
  { name: "security", label: "安全", path: "/accounts/security" },
  { name: "audit", label: "审计日志", path: "/accounts/security/audit" },
];

const current = computed(() => route.meta.settings);

function go(name) {
  const tab = TABS.find((t) => t.name === name);
  if (tab) router.push(tab.path);
}
</script>

<template>
  <div class="gm-settings-head">
    <h1 class="gm-settings-title">设置</h1>
    <NTabs :value="current" type="line" size="medium" @update:value="go">
      <NTab v-for="t in TABS" :key="t.name" :name="t.name">{{ t.label }}</NTab>
    </NTabs>
  </div>
</template>

<style scoped>
.gm-settings-head { padding: 16px 24px 0; background: var(--gm-surface); border-bottom: 1px solid var(--gm-divider); }
.gm-settings-title { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
@media (max-width: 768px) {
    .gm-settings-head { padding: 12px 16px 0; }
}
</style>
