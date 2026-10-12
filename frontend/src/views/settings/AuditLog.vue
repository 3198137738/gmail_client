<script setup>
import { computed, h, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NDataTable, NTag } from "naive-ui";
import SettingsHeader from "../../components/SettingsHeader.vue";
import { get, post } from "../../api";
import { confirm, run } from "../../ui";
import { dateTime } from "../../format";

const route = useRoute();
const router = useRouter();
const data = ref(null);
const loading = ref(false);

const page = computed(() => Math.max(1, Number(route.query.page) || 1));

async function load() {
  loading.value = true;
  const result = await run(get("/api/security/audit", { page: page.value }));
  loading.value = false;
  if (result) data.value = result;
}
watch(page, load, { immediate: true });

function goPage(n) {
  router.push({ path: "/accounts/security/audit", query: n > 1 ? { page: n } : {} });
}

async function ban(ip) {
  const ok = await confirm(`确定永久拉黑 ${ip} 吗？该 IP 上的设备会立即登出，之后无法访问任何页面。`, { danger: true });
  if (ok && (await run(post("/api/security/ip-ban", { ip }), (r) => r.message))) load();
}

const columns = computed(() => [
  { title: "时间", key: "created_at", width: 170, render: (r) => dateTime(r.created_at, true) },
  { title: "事件", key: "event_label", width: 150 },
  { title: "来源", key: "source", width: 80 },
  {
    title: "IP",
    key: "ip_address",
    width: 210,
    render: (r) => {
      const ip = r.ip_address;
      if (!ip) return "";
      const extra = r.ip_banned
        ? h(NTag, { size: "small", type: "error", bordered: false }, () => "已拉黑")
        : ip !== "unknown" && ip !== data.value?.current_ip
          ? h(NButton, { text: true, type: "error", size: "small", onClick: () => ban(ip) }, () => "拉黑")
          : null;
      return h("span", { class: "gm-audit-ip" }, [h("span", ip), extra]);
    },
  },
  { title: "详情", key: "detail", minWidth: 240, ellipsis: { tooltip: { style: { maxWidth: "480px" } } } },
]);

const rowClassName = (row) => (row.warn ? "is-warn" : "");
</script>

<template>
  <div class="gm-panel gm-settings-page">
    <SettingsHeader />
    <div class="gm-settings">
      <p class="gm-settings-hint gm-audit-hint">
        登录、删信、发信、账号变更、备份等操作记录<template v-if="data">，共 {{ data.total }} 条</template>。新 IP 登录、IP 被封禁、访问密码修改时会通过 Telegram 提醒。
      </p>
      <NDataTable
        :columns="columns"
        :data="data?.rows || []"
        :loading="loading"
        :row-key="(r) => r.id"
        :row-class-name="rowClassName"
        :scroll-x="860"
        size="small"
        :bordered="false"
        :single-line="false"
      />
      <div v-if="data" class="gm-audit-pager">
        <NButton quaternary :disabled="page <= 1" @click="goPage(page - 1)">上一页</NButton>
        <span class="gm-settings-hint">第 {{ page }} 页</span>
        <NButton quaternary :disabled="!data.has_next" @click="goPage(page + 1)">下一页</NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-settings-page { overflow-y: auto; background: var(--gm-bg); }
.gm-audit-hint { margin: 16px 0 12px; }
.gm-audit-pager { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
:deep(.is-warn td) { background: #fef7e0 !important; }
:deep(.gm-audit-ip) { display: inline-flex; align-items: center; gap: 8px; }
@media (max-width: 768px) {
    .gm-settings-page { border-radius: 0; }
}
</style>
