<script setup>
// 账号：列表（状态、同步时间、出错提示），点开在抽屉里修改：显示名、分组、发信连接方式、应用专用密码、启用 / 删除。
// 签名、分组、通知在各自的分区里统一管理；添加账号在单独的页面。
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NAutoComplete,
  NButton,
  NCheckbox,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NPopover,
  NRadio,
  NRadioGroup,
  NTag,
} from "naive-ui";
import { AddOutlined, ChevronRightOutlined, SearchOutlined, SyncOutlined } from "@vicons/material";
import GmAvatar from "../../ui/GmAvatar.vue";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { confirm, run, toast } from "../../ui";
import { isMobile, refreshNav } from "../../composables/common";
import { useResource } from "../../composables/useResource";
import { syncAccount, syncAll } from "../../actions";
import { shortDateTime } from "../../format";
import { onRealtime } from "../../realtime";

const router = useRouter();
const res = useResource(() => get("/api/accounts"), { immediate: false });
const data = res.data;
const filter = ref("");
const selected = ref([]);

async function load(opts) {
  const result = await res.reload(opts);
  if (!result) return;
  const ids = new Set(result.accounts.map((a) => a.id));
  selected.value = selected.value.filter((id) => ids.has(id));
  // Google / Microsoft 授权回调后跳回这里时的结果提示
  if (result.flash) toast(result.flash.message, { tone: result.flash.level === "error" ? "error" : "normal", duration: 8000 });
}
load();

// 有账号正在同步时定时刷新状态（同步结束也会收到实时推送）
const anyBusy = computed(() => data.value?.accounts.some((a) => a.busy || a.backfilling));
let timer = null;
watch(anyBusy, (busy) => {
  clearInterval(timer);
  if (busy) timer = setInterval(() => load({ silent: true }), 4000);
});
const stopSyncDone = onRealtime("sync_done", () => load({ silent: true }));
onBeforeUnmount(() => {
  clearInterval(timer);
  stopSyncDone();
});

const isOutlook = (a) => a.provider === "outlook";
const accounts = computed(() => {
  const q = filter.value.trim().toLowerCase();
  const list = data.value?.accounts || [];
  return q ? list.filter((a) => `${a.email} ${a.display_name} ${a.group_name}`.toLowerCase().includes(q)) : list;
});
const errors = (a) => a.sync_states.filter((s) => s.last_error);
const lastSynced = (a) => {
  const times = a.sync_states.map((s) => s.last_synced_at).filter(Boolean).sort();
  return times.length ? shortDateTime(times[times.length - 1]) : "";
};

const allChecked = computed(() => !!accounts.value.length && accounts.value.every((a) => selected.value.includes(a.id)));
function toggleAll(v) {
  selected.value = v ? accounts.value.map((a) => a.id) : [];
}
function toggleOne(id, v) {
  selected.value = v ? [...selected.value, id] : selected.value.filter((x) => x !== id);
}

async function batchDelete() {
  if (!selected.value.length) return;
  if (!(await confirm(`确定删除选中的 ${selected.value.length} 个账号及其本地邮件吗？`, { danger: true, positiveText: "删除" }))) return;
  const result = await run(post("/api/accounts/batch-delete", { ids: selected.value }), (r) => `已删除 ${r.deleted} 个账号及其本地邮件`);
  if (result) {
    selected.value = [];
    load({ silent: true });
    refreshNav();
  }
}

// ---- 账号详情（抽屉） ----
const editing = ref(null);
const form = reactive({ display_name: "", password: "", group_name: "", smtp_starttls: false, sync_gmail_name: false });
const saving = ref(false);
const fetchingName = ref(false);
const groupOptions = computed(() => {
  const names = [...new Set((data.value?.accounts || []).map((a) => a.group_name).filter(Boolean))].sort();
  const typed = (form.group_name || "").trim();
  return names.filter((n) => !typed || n.includes(typed)).map((n) => ({ label: n, value: n }));
});

function open(a) {
  editing.value = a;
  Object.assign(form, {
    display_name: a.display_name,
    group_name: a.group_name,
    smtp_starttls: !!a.smtp_starttls,
    password: "",
    sync_gmail_name: false,
  });
}

const NAME_SOURCES = { gmail: "已从 Gmail 获取姓名", sent: "已按已发送邮件的发件人名称填入" };
async function fetchName() {
  const a = editing.value;
  fetchingName.value = true;
  const result = await run(get(`/api/accounts/${a.id}/gmail-name`));
  fetchingName.value = false;
  if (!result) return;
  if (!result.name) {
    toast(isOutlook(a) ? "没有取到姓名：还没有可参考的已发送邮件" : "没有取到姓名：Gmail 中未设置，也没有可参考的已发送邮件", { tone: "error" });
    return;
  }
  form.display_name = result.name;
  toast(NAME_SOURCES[result.source]);
}

async function save() {
  const a = editing.value;
  const body = { display_name: form.display_name, group_name: form.group_name || "" };
  if (!isOutlook(a)) body.smtp_starttls = form.smtp_starttls;
  if (a.oauth && !isOutlook(a) && form.sync_gmail_name) body.sync_gmail_name = true;
  if (form.password) body.password = form.password;
  saving.value = true;
  const result = await run(post(`/api/accounts/${a.id}/update`, body), (r) => (r.gmail_name ? "已保存，Gmail 中的姓名已同步修改" : "已保存"));
  saving.value = false;
  if (result) {
    editing.value = null;
    load({ silent: true });
    refreshNav();
  }
}

async function toggleEnabled(a) {
  if (await run(post(`/api/accounts/${a.id}/toggle`), (r) => (r.enabled ? "已启用" : "已停用"))) {
    editing.value = null;
    load({ silent: true });
    refreshNav();
  }
}

async function remove(a) {
  if (!(await confirm(`确定删除 ${a.email} 及其本地邮件吗？邮箱服务器上的邮件不受影响。`, { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/accounts/${a.id}/delete`), "已删除账号")) {
    editing.value = null;
    load({ silent: true });
    refreshNav();
  }
}
</script>

<template>
  <div class="gm-settings">
    <div class="gm-ac-bar">
      <NCheckbox :checked="allChecked" :disabled="!accounts.length" @update:checked="toggleAll">全选</NCheckbox>
      <NButton v-if="selected.length" size="small" type="error" secondary @click="batchDelete">删除选中 ({{ selected.length }})</NButton>
      <NInput v-model:value="filter" size="small" placeholder="查找账号" clearable class="gm-ac-filter">
        <template #prefix><NIcon :component="SearchOutlined" /></template>
      </NInput>
      <span class="gm-ac-spacer" />
      <NButton size="small" secondary @click="syncAll">
        <template #icon><NIcon :component="SyncOutlined" /></template>同步全部
      </NButton>
      <NButton size="small" type="primary" @click="router.push('/settings/accounts/add')">
        <template #icon><NIcon :component="AddOutlined" /></template>添加账号
      </NButton>
    </div>

    <GmEmptyState v-if="res.error.value" tone="error" title="账号列表加载失败" :hint="res.error.value" action-text="重试" @action="load()" />
    <div v-else-if="!data" class="gm-ac-loading">正在加载…</div>
    <GmEmptyState
      v-else-if="!data.accounts.length"
      title="还没有邮箱账号"
      hint="添加 Gmail 或 Outlook 账号后即可在这里收发邮件"
      action-text="添加账号"
      @action="router.push('/settings/accounts/add')"
    />
    <div v-else class="gm-ac-list" role="list">
      <div
        v-for="a in accounts"
        :key="a.id"
        class="gm-ac-row"
        :class="{ 'is-disabled': !a.enabled }"
        role="listitem"
        tabindex="0"
        @click="open(a)"
        @keydown.enter.self="open(a)"
      >
        <span @click.stop><NCheckbox :checked="selected.includes(a.id)" :aria-label="`选择 ${a.email}`" @update:checked="(v) => toggleOne(a.id, v)" /></span>
        <GmAvatar :name="a.display_name || a.email" :color="a.color" :size="36" />
        <div class="gm-ac-main">
          <div class="gm-ac-name">
            {{ a.display_name || a.email }}
            <span v-if="a.display_name" class="gm-ac-email">{{ a.email }}</span>
          </div>
          <div class="gm-ac-tags">
            <NTag v-if="!a.enabled" size="small" :bordered="false">已停用</NTag>
            <NTag v-if="isOutlook(a)" size="small" type="info" :bordered="false">Outlook</NTag>
            <NTag v-if="isOutlook(a)" size="small" type="success" :bordered="false">Microsoft 授权</NTag>
            <NTag v-else-if="a.oauth" size="small" type="success" :bordered="false">Google 授权</NTag>
            <NTag v-else size="small" :bordered="false">应用专用密码</NTag>
            <NTag v-if="a.group_name" size="small" :bordered="false">{{ a.group_name }}</NTag>
            <NTag v-if="a.busy" size="small" type="info" :bordered="false">同步中…</NTag>
            <NTag v-if="a.backfilling" size="small" type="warning" :bordered="false">同步历史邮件中…</NTag>
            <NPopover v-if="errors(a).length" trigger="click">
              <template #trigger>
                <NTag size="small" type="error" :bordered="false" class="gm-ac-err" @click.stop>同步出错</NTag>
              </template>
              <div class="gm-ac-errtext">
                <div v-for="s in errors(a)" :key="s.folder"><b>{{ s.folder }}</b>：{{ s.last_error }}</div>
              </div>
            </NPopover>
          </div>
        </div>
        <div class="gm-ac-meta">
          <span>{{ a.message_count }} 封</span>
          <span v-if="lastSynced(a)" class="gm-ac-synced">同步于 {{ lastSynced(a) }}</span>
        </div>
        <NIcon :component="ChevronRightOutlined" :size="20" class="gm-ac-chevron" />
      </div>
    </div>

    <NDrawer :show="!!editing" :width="isMobile ? '100%' : 480" placement="right" @update:show="(v) => !v && (editing = null)">
      <NDrawerContent v-if="editing" :title="editing.display_name || editing.email" closable :native-scrollbar="false">
        <div class="gm-ac-drawer-email">{{ editing.email }}</div>
        <div class="gm-ac-drawer-actions">
          <NButton size="small" secondary @click="syncAccount(editing.id)">
            <template #icon><NIcon :component="SyncOutlined" /></template>立即同步
          </NButton>
          <NButton size="small" secondary @click="toggleEnabled(editing)">{{ editing.enabled ? "停用账号" : "启用账号" }}</NButton>
        </div>
        <div class="gm-ac-sync">
          <div v-for="s in editing.sync_states" :key="s.folder" class="gm-ac-sync-row">
            <span class="gm-ac-sync-folder">{{ s.folder }}</span>
            <span v-if="s.last_error" class="gm-ac-sync-err">{{ s.last_error }}</span>
            <span v-else>{{ shortDateTime(s.last_synced_at) || "—" }}</span>
          </div>
          <div v-if="!editing.sync_states.length" class="gm-settings-hint">尚未同步</div>
        </div>

        <NForm label-placement="top" class="gm-ac-form" @submit.prevent="save">
          <NFormItem label="显示名（收件人看到的发件人名称）">
            <div class="gm-ac-namebox">
              <div class="gm-ac-namerow">
                <NInput v-model:value="form.display_name" :maxlength="255" />
                <NButton secondary :loading="fetchingName" @click="fetchName">{{ isOutlook(editing) ? "从已发送邮件获取" : "从 Gmail 获取" }}</NButton>
              </div>
              <div v-if="isOutlook(editing)" class="gm-settings-hint">Outlook 中的姓名请在 Microsoft 账号中修改</div>
              <NCheckbox v-else-if="editing.oauth" v-model:checked="form.sync_gmail_name" :disabled="!form.display_name.trim()">
                保存时同时修改 Gmail 中的姓名
              </NCheckbox>
              <div v-else class="gm-settings-hint">应用专用密码账号无法修改 Gmail 中的姓名，改为 Google 授权登录后可修改</div>
            </div>
          </NFormItem>
          <NFormItem label="分组（侧栏按分组折叠显示）">
            <NAutoComplete v-model:value="form.group_name" :options="groupOptions" placeholder="如：工作、注册用" clearable />
          </NFormItem>
          <NFormItem v-if="!isOutlook(editing)" label="发信连接方式">
            <NRadioGroup v-model:value="form.smtp_starttls">
              <NRadio :value="false">SSL（465 端口，推荐）</NRadio>
              <NRadio :value="true">STARTTLS（587 端口）</NRadio>
            </NRadioGroup>
          </NFormItem>
          <div v-else class="gm-settings-hint gm-ac-hint">Outlook 固定使用 STARTTLS（587 端口）发信。</div>
          <NFormItem v-if="!isOutlook(editing)" label="新应用专用密码（留空则不修改）">
            <NInput v-model:value="form.password" type="password" show-password-on="click" :input-props="{ autocomplete: 'new-password' }" />
          </NFormItem>
          <p class="gm-settings-hint">签名在「签名」分区修改；是否推送 Telegram 在「通知」分区设置。</p>
        </NForm>

        <template #footer>
          <div class="gm-ac-foot">
            <NButton type="error" quaternary @click="remove(editing)">删除账号</NButton>
            <span class="gm-ac-spacer" />
            <NButton @click="editing = null">取消</NButton>
            <NButton type="primary" :loading="saving" @click="save">保存</NButton>
          </div>
        </template>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.gm-ac-bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-3); }
.gm-ac-filter { width: 200px; }
.gm-ac-spacer { flex: 1; }
.gm-ac-loading { padding: var(--sp-6); color: var(--gm-text-3); }
.gm-ac-list { border: 1px solid var(--gm-border); border-radius: var(--r-lg); background: var(--gm-surface); overflow: hidden; }
.gm-ac-row {
    display: grid;
    grid-template-columns: auto 36px 1fr auto 20px;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-3) var(--sp-4);
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
    outline: none;
}
.gm-ac-row:last-child { border-bottom: 0; }
.gm-ac-row:hover { background: var(--gm-hover); }
.gm-ac-row:focus-visible { box-shadow: inset 3px 0 0 var(--gm-primary); }
.gm-ac-row.is-disabled { opacity: .6; }
.gm-ac-main { min-width: 0; }
.gm-ac-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-ac-email { margin-left: 6px; font-size: var(--fs-xs); font-weight: 400; color: var(--gm-text-3); }
.gm-ac-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.gm-ac-err { cursor: pointer; }
.gm-ac-errtext { max-width: 420px; font-size: var(--fs-sm); line-height: 1.6; word-break: break-all; }
.gm-ac-meta { display: flex; flex-direction: column; align-items: flex-end; font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; }
.gm-ac-chevron { color: var(--gm-text-4); }
.gm-ac-drawer-email { margin-top: -8px; margin-bottom: var(--sp-3); color: var(--gm-text-3); }
.gm-ac-drawer-actions { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin-bottom: var(--sp-3); }
.gm-ac-sync { margin-bottom: var(--sp-4); padding: var(--sp-2) var(--sp-3); border-radius: var(--r-md); background: var(--gm-surface-2); font-size: var(--fs-sm); }
.gm-ac-sync-row { display: flex; gap: var(--sp-3); padding: 3px 0; }
.gm-ac-sync-folder { flex: none; min-width: 120px; color: var(--gm-text-3); }
.gm-ac-sync-err { color: var(--gm-error); word-break: break-all; }
.gm-ac-namebox { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.gm-ac-namerow { display: flex; gap: var(--sp-2); }
.gm-ac-hint { margin: -8px 0 var(--sp-4); }
.gm-ac-foot { display: flex; align-items: center; gap: var(--sp-2); width: 100%; }
@media (max-width: 768px) {
    .gm-ac-filter { width: 100%; order: 10; }
    .gm-ac-row { grid-template-columns: auto 36px 1fr 20px; }
    .gm-ac-meta { display: none; }
}
</style>
