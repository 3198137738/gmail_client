<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  NAlert,
  NAutoComplete,
  NButton,
  NCheckbox,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NPopover,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
} from "naive-ui";
import { LabelFilled, NotificationsOffOutlined, NotificationsOutlined, SyncOutlined } from "@vicons/material";
import SettingsHeader from "../../components/SettingsHeader.vue";
import RichEditor from "../../components/RichEditor.vue";
import { get, post } from "../../api";
import { confirm, run, ui } from "../../ui";
import { hardNavigate, refreshNav } from "../../composables/common";
import { syncAccount, syncAll } from "../../actions";
import { shortDateTime } from "../../format";
import { onRealtime } from "../../realtime";

const data = ref(null);
const selected = ref([]);
const editing = ref(null);
const editForm = reactive({ display_name: "", password: "", group_name: "", sync_gmail_name: false });
const fetchingName = ref(false);
// 已有的分组名，填写分组时作为候选
const groupOptions = computed(() => {
  const names = [...new Set((data.value?.accounts || []).map((a) => a.group_name).filter(Boolean))].sort();
  const typed = (editForm.group_name || "").trim();
  return names.filter((n) => !typed || n.includes(typed)).map((n) => ({ label: n, value: n }));
});
// 正在编辑的账号的签名编辑器（列表里同时只编辑一个账号）
let sigEditor = null;
const single = reactive({ email: "", display_name: "", password: "", verify: false });
const batch = reactive({ text: "", verify: false });
const adding = ref(false);
const batchResult = ref(null);

async function load() {
  const result = await run(get("/api/accounts"));
  if (!result) return;
  data.value = result;
  const ids = new Set(result.accounts.map((a) => a.id));
  selected.value = selected.value.filter((id) => ids.has(id));
  // Google 授权回调后跳回这里时的结果提示
  if (result.flash) {
    const type = ["success", "warning"].includes(result.flash.level) ? result.flash.level : "error";
    ui.message[type](result.flash.message, { duration: 6000 });
  }
}
onMounted(load);

// 有账号正在同步时定时刷新状态（同步结束也会收到实时推送）
const anyBusy = computed(() => data.value?.accounts.some((a) => a.busy || a.backfilling));
let timer = null;
watch(anyBusy, (busy) => {
  clearInterval(timer);
  if (busy) timer = setInterval(load, 4000);
});
const stopSyncDone = onRealtime("sync_done", load);
onBeforeUnmount(() => {
  clearInterval(timer);
  stopSyncDone();
});

const allChecked = computed(() => !!data.value?.accounts.length && selected.value.length === data.value.accounts.length);
function toggleAll(v) {
  selected.value = v ? data.value.accounts.map((a) => a.id) : [];
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
    load();
    refreshNav();
  }
}

function startEdit(a) {
  editing.value = editing.value === a.id ? null : a.id;
  editForm.display_name = a.display_name;
  editForm.group_name = a.group_name;
  editForm.password = "";
  editForm.sync_gmail_name = false;
}

const NAME_SOURCES = { gmail: "已从 Gmail 获取姓名", sent: "已按已发送邮件的发件人名称填入" };

const isOutlook = (a) => a.provider === "outlook";

async function fetchGmailName(a) {
  fetchingName.value = true;
  const result = await run(get(`/api/accounts/${a.id}/gmail-name`));
  fetchingName.value = false;
  if (!result) return;
  if (!result.name) {
    ui.message.warning(isOutlook(a) ? "没有取到姓名：还没有可参考的已发送邮件" : "没有取到姓名：Gmail 中未设置，也没有可参考的已发送邮件");
    return;
  }
  editForm.display_name = result.name;
  ui.message.success(NAME_SOURCES[result.source]);
}

async function saveEdit(a) {
  const body = { display_name: editForm.display_name, group_name: editForm.group_name || "" };
  if (a.oauth && !isOutlook(a) && editForm.sync_gmail_name) body.sync_gmail_name = true;
  if (editForm.password) body.password = editForm.password;
  if (sigEditor) body.signature_html = sigEditor.getInnerHtml();
  if (await run(post(`/api/accounts/${a.id}/update`, body), (r) => (r.gmail_name ? "已保存，Gmail 中的姓名已同步修改" : "已保存"))) {
    editing.value = null;
    load();
    refreshNav();
  }
}

async function toggle(a) {
  if (await run(post(`/api/accounts/${a.id}/toggle`), (r) => (r.enabled ? "已启用" : "已停用"))) load();
}

async function toggleNotify(a) {
  if (await run(post(`/api/accounts/${a.id}/toggle-notify`), (r) => (r.notify_enabled ? "已开启通知" : "已静音"))) load();
}

async function remove(a) {
  if (!(await confirm(`确定删除 ${a.email} 及其本地邮件吗？`, { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/accounts/${a.id}/delete`), "已删除账号")) {
    load();
    refreshNav();
  }
}

async function addSingle() {
  if (!single.email || !single.password) return;
  adding.value = true;
  const result = await run(post("/api/accounts", { ...single }), `已添加 ${single.email}，正在同步邮件`);
  adding.value = false;
  if (result) {
    Object.assign(single, { email: "", display_name: "", password: "", verify: false });
    load();
    refreshNav();
  }
}

async function addBatch() {
  if (!batch.text.trim()) return;
  adding.value = true;
  const result = await run(post("/api/accounts/batch", { accounts_text: batch.text, verify: batch.verify }));
  adding.value = false;
  if (result) {
    batchResult.value = result;
    if (result.added) batch.text = "";
    load();
    refreshNav();
  }
}

async function telegramTest() {
  const result = await run(post("/api/accounts/telegram-test"));
  if (result) ui.message[result.ok ? "success" : "error"](result.message);
}

function oauthStart() {
  // 需要整页跳转到 Google 授权页面
  hardNavigate("/accounts/oauth/start");
}

function msOauthStart() {
  // 需要整页跳转到 Microsoft 授权页面
  hardNavigate("/accounts/oauth/microsoft/start");
}
</script>

<template>
  <div class="gm-panel gm-settings-page">
    <SettingsHeader />
    <div class="gm-settings">
      <NSpin v-if="!data" class="gm-settings-loading" />
      <template v-else>
        <!-- 已配置的账号 -->
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            邮箱账号：
            <div class="gm-settings-hint">通过 IMAP / SMTP 收发 Gmail 和 Outlook 邮件</div>
          </div>
          <div class="gm-settings-content">
            <div class="gm-settings-actions">
              <NCheckbox :checked="allChecked" :disabled="!data.accounts.length" @update:checked="toggleAll">全选</NCheckbox>
              <NButton size="small" type="error" secondary :disabled="!selected.length" @click="batchDelete">
                删除选中 ({{ selected.length }})
              </NButton>
              <NButton size="small" secondary @click="syncAll">
                <template #icon><NIcon :component="SyncOutlined" /></template>立即同步全部
              </NButton>
            </div>

            <div v-for="a in data.accounts" :key="a.id" class="gm-acct" :class="{ 'is-disabled': !a.enabled }">
              <NCheckbox
                :checked="selected.includes(a.id)"
                :aria-label="`选择 ${a.email}`"
                @update:checked="(v) => toggleOne(a.id, v)"
              />
              <NIcon :component="LabelFilled" :color="a.color" :size="20" class="gm-acct-dot" />
              <div class="gm-acct-main">
                <div class="gm-acct-name">
                  <b>{{ a.display_name || a.email }}</b>
                  <span v-if="a.display_name" class="gm-acct-email">&lt;{{ a.email }}&gt;</span>
                </div>
                <div class="gm-acct-badges">
                  <NTag v-if="a.group_name" size="small" type="info" :bordered="false">{{ a.group_name }}</NTag>
                  <NTag v-if="!a.enabled" size="small" :bordered="false">已停用</NTag>
                  <NTag v-if="isOutlook(a)" size="small" type="info" :bordered="false" title="Outlook / Hotmail / Microsoft 365 邮箱">Outlook</NTag>
                  <NTag v-if="isOutlook(a)" size="small" type="success" :bordered="false" title="通过 Microsoft 账号授权登录（OAuth2）">Microsoft 授权</NTag>
                  <NTag v-else-if="a.oauth" size="small" type="success" :bordered="false" title="通过 Google 账号授权登录（OAuth2）">Google 授权</NTag>
                  <NTag v-else size="small" :bordered="false" title="使用应用专用密码登录">应用专用密码</NTag>
                  <NTag v-if="a.busy" size="small" type="info" :bordered="false">同步中…</NTag>
                  <NTag v-if="a.backfilling" size="small" type="warning" :bordered="false" title="正在后台同步更早的历史邮件">同步历史邮件中…</NTag>
                  <NTag size="small" :bordered="false" title="本地已同步的邮件数">{{ a.message_count }} 封邮件</NTag>
                  <NTag v-if="a.notify_enabled" size="small" :bordered="false" title="新邮件会推送到 Telegram">
                    <template #icon><NIcon :component="NotificationsOutlined" /></template>通知
                  </NTag>
                  <NTag v-else size="small" :bordered="false" title="该账号不推送 Telegram 通知">
                    <template #icon><NIcon :component="NotificationsOffOutlined" /></template>静音
                  </NTag>
                </div>
                <div class="gm-acct-sync">
                  <span v-for="s in a.sync_states" :key="s.folder" class="gm-acct-sync-item">
                    {{ s.folder.length > 28 ? s.folder.slice(0, 27) + "…" : s.folder }}：
                    <NPopover v-if="s.last_error" trigger="click">
                      <template #trigger>
                        <NTag size="small" type="error" :bordered="false" class="gm-err-tag">同步出错</NTag>
                      </template>
                      <div class="gm-err-text">{{ s.last_error }}</div>
                    </NPopover>
                    <template v-else>{{ shortDateTime(s.last_synced_at) || "—" }}</template>
                  </span>
                  <span v-if="!a.sync_states.length" class="gm-acct-sync-item">尚未同步</span>
                </div>
                <NForm v-if="editing === a.id" class="gm-acct-edit" label-placement="top" @submit.prevent="saveEdit(a)">
                  <NFormItem label="分组（可选，侧栏按分组折叠显示）">
                    <NAutoComplete v-model:value="editForm.group_name" :options="groupOptions" placeholder="如：工作、注册用" clearable />
                  </NFormItem>
                  <NFormItem label="显示名（收件人看到的发件人名称）">
                    <div class="gm-name-field">
                      <div class="gm-name-row">
                        <NInput v-model:value="editForm.display_name" :maxlength="255" />
                        <NButton
                          secondary
                          :loading="fetchingName"
                          :title="isOutlook(a) ? '按已发送邮件的发件人名称填入' : '读取 Gmail 中设置的姓名'"
                          @click="fetchGmailName(a)"
                        >{{ isOutlook(a) ? "从已发送邮件获取" : "从 Gmail 获取" }}</NButton>
                      </div>
                      <div v-if="isOutlook(a)" class="gm-settings-hint">Outlook 中的姓名请在 Microsoft 账号中修改</div>
                      <NCheckbox v-else-if="a.oauth" v-model:checked="editForm.sync_gmail_name" :disabled="!editForm.display_name.trim()">
                        保存时同时修改 Gmail 中的姓名
                      </NCheckbox>
                      <div v-else class="gm-settings-hint">应用专用密码账号无法修改 Gmail 中的姓名，改为 Google 授权登录后可修改</div>
                    </div>
                  </NFormItem>
                  <NFormItem v-if="!isOutlook(a)" label="新应用专用密码（留空则不修改）">
                    <NInput v-model:value="editForm.password" type="password" show-password-on="click" :input-props="{ autocomplete: 'new-password' }" />
                  </NFormItem>
                  <NFormItem label="签名（写邮件时自动添加，留空则不添加）">
                    <div class="gm-sig-editor">
                      <RichEditor :ref="(el) => (sigEditor = el)" :initial-html="a.signature_html" show-format-bar />
                    </div>
                  </NFormItem>
                  <div class="gm-acct-edit-actions">
                    <NButton size="small" @click="editing = null">取消</NButton>
                    <NButton size="small" type="primary" attr-type="submit">保存更改</NButton>
                  </div>
                </NForm>
              </div>
              <div class="gm-acct-links">
                <NButton text type="primary" @click="syncAccount(a.id)">同步</NButton>
                <NButton text type="primary" @click="startEdit(a)">修改信息</NButton>
                <NButton text type="primary" @click="toggle(a)">{{ a.enabled ? "停用" : "启用" }}</NButton>
                <NButton text type="primary" title="切换该账号的 Telegram 通知" @click="toggleNotify(a)">
                  {{ a.notify_enabled ? "静音" : "开启通知" }}
                </NButton>
                <NButton text type="error" @click="remove(a)">删除</NButton>
              </div>
            </div>
            <div v-if="!data.accounts.length" class="gm-settings-empty">还没有账号，请在下方添加。</div>
          </div>
        </section>

        <!-- 添加账号 -->
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            添加邮箱账号：
            <div class="gm-settings-hint">
              应用专用密码在
              <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Google 账号 › 应用专用密码</a>
              生成（需先开启两步验证）。Outlook 邮箱已不支持密码登录，请用上方的 Microsoft 授权添加
            </div>
          </div>
          <div class="gm-settings-content">
            <div class="gm-oauth-box">
              <template v-if="data.oauth_configured">
                <NButton type="primary" @click="oauthStart">使用 Google 账号授权添加（推荐）</NButton>
                <div class="gm-settings-hint">无需应用专用密码；已有的账号再授权一次即可改为授权登录，随时可在 Google 账号中撤销。</div>
              </template>
              <div v-else class="gm-settings-hint">
                想用 Google 账号授权（OAuth2）代替应用专用密码？在 <code>.env</code> 中设置
                <code>GOOGLE_OAUTH_CLIENT_ID</code> 与 <code>GOOGLE_OAUTH_CLIENT_SECRET</code> 后重启。
              </div>
              <template v-if="data.ms_oauth_configured">
                <NButton type="primary" secondary @click="msOauthStart">使用 Microsoft 账号授权添加 Outlook 邮箱</NButton>
                <div class="gm-settings-hint">支持 Outlook.com、Hotmail、Live 和 Microsoft 365 邮箱；已有的 Outlook 账号授权失效时再授权一次即可。</div>
              </template>
              <div v-else class="gm-settings-hint">
                添加 Outlook / Hotmail 邮箱需要 Microsoft 账号授权：在 <code>.env</code> 中设置
                <code>MICROSOFT_OAUTH_CLIENT_ID</code> 与 <code>MICROSOFT_OAUTH_CLIENT_SECRET</code> 后重启。
              </div>
            </div>
            <NTabs type="segment" size="small" class="gm-add-tabs">
              <NTabPane name="single" tab="单个添加">
                <NForm class="gm-form" label-placement="top" @submit.prevent="addSingle">
                  <NFormItem label="Gmail 地址">
                    <NInput v-model:value="single.email" placeholder="you@gmail.com" :input-props="{ type: 'email' }" />
                  </NFormItem>
                  <NFormItem label="显示名称（可选）">
                    <NInput v-model:value="single.display_name" placeholder="收件人看到的发件人名称" />
                  </NFormItem>
                  <NFormItem label="应用专用密码">
                    <NInput
                      v-model:value="single.password"
                      type="password"
                      show-password-on="click"
                      :input-props="{ autocomplete: 'new-password' }"
                    />
                  </NFormItem>
                  <NCheckbox v-model:checked="single.verify">添加前通过 IMAP 验证一次（约 3–5 秒）</NCheckbox>
                  <div class="gm-form-actions">
                    <NButton type="primary" attr-type="submit" :loading="adding" :disabled="!single.email || !single.password">添加账号</NButton>
                  </div>
                </NForm>
              </NTabPane>
              <NTabPane name="batch" tab="批量添加">
                <NForm class="gm-form" label-placement="top" @submit.prevent="addBatch">
                  <NFormItem label="每行一个账号">
                    <NInput
                      v-model:value="batch.text"
                      type="textarea"
                      :rows="7"
                      placeholder="邮箱----应用密码&#10;邮箱----应用密码----显示名&#10;&#10;示例：&#10;a@gmail.com----abcdefghijklmnop&#10;b@gmail.com,abcdefghijklmnop,小明"
                    />
                  </NFormItem>
                  <div class="gm-settings-hint">
                    分隔符支持 <code>----</code>、逗号、分号、竖线、冒号、制表符或空格；第三列为可选显示名；以 <code>#</code> 开头的行会被忽略。
                  </div>
                  <NCheckbox v-model:checked="batch.verify">逐个通过 IMAP 验证（账号较多时会很慢）</NCheckbox>
                  <NAlert
                    v-if="batchResult"
                    :type="batchResult.level"
                    closable
                    class="gm-batch-result"
                    @close="batchResult = null"
                  >{{ batchResult.message }}</NAlert>
                  <div class="gm-form-actions">
                    <NButton type="primary" attr-type="submit" :loading="adding" :disabled="!batch.text.trim()">批量添加</NButton>
                  </div>
                </NForm>
              </NTabPane>
            </NTabs>
          </div>
        </section>

        <!-- Telegram -->
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            Telegram 新邮件通知：
            <div class="gm-settings-hint">收到新邮件时推送到 Telegram，并可在消息里直接标为已读或删除</div>
          </div>
          <div class="gm-settings-content">
            <div class="gm-settings-status">
              <NTag v-if="data.telegram.enabled" type="success" :bordered="false">已启用</NTag>
              <NTag v-else-if="data.telegram.configured" :bordered="false">已配置 · 未启用</NTag>
              <NTag v-else type="warning" :bordered="false">未配置</NTag>
            </div>
            <template v-if="data.telegram.configured">
              <p>目标 Chat ID：<code>{{ data.telegram.chat_id }}</code>　·　已发送邮件通知：{{ data.telegram.notify_sent ? "开启" : "关闭" }}</p>
              <p class="gm-settings-hint">在机器人对话中发送 <code>/unread</code>、<code>/status</code>、<code>/help</code> 可远程查询。</p>
              <NButton size="small" secondary @click="telegramTest">发送测试消息</NButton>
            </template>
            <p v-else class="gm-settings-hint">
              在 <code>.env</code> 中设置 <code>TELEGRAM_BOT_TOKEN</code> 与 <code>TELEGRAM_CHAT_ID</code> 后重启应用即可启用。
            </p>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-settings-page { overflow-y: auto; background: var(--gm-bg); }
.gm-settings-loading { display: flex; justify-content: center; padding: 48px 0; }
.gm-settings-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
.gm-acct {
    display: grid;
    grid-template-columns: auto auto 1fr auto;
    align-items: start;
    gap: 4px 12px;
    padding: 12px 0;
    border-bottom: 1px solid var(--gm-divider);
}
.gm-acct:last-of-type { border-bottom: 0; }
.gm-acct.is-disabled .gm-acct-main { opacity: .6; }
.gm-acct-dot { margin-top: 2px; }
.gm-acct-main { min-width: 0; }
.gm-acct-name { word-break: break-all; }
.gm-acct-email { color: var(--gm-text-3); margin-left: 4px; }
.gm-acct-badges { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.gm-acct-sync { display: flex; flex-wrap: wrap; gap: 4px 16px; color: var(--gm-text-3); font-size: 12px; }
.gm-acct-sync-item { display: inline-flex; align-items: center; }
.gm-err-tag { cursor: pointer; }
.gm-err-text { max-width: 420px; word-break: break-word; font-size: 13px; }
.gm-acct-links { display: flex; flex-wrap: wrap; gap: 12px; justify-content: flex-end; }
.gm-acct-edit { margin-top: 12px; max-width: 420px; }
.gm-name-field { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.gm-name-row { display: flex; gap: 8px; }
.gm-acct-edit-actions { display: flex; gap: 8px; }
.gm-sig-editor { display: flex; flex-direction: column; width: 100%; min-height: 120px; padding: 0 10px; border: 1px solid var(--gm-border); border-radius: var(--gm-radius); }
.gm-sig-editor :deep(.gm-editor) { min-height: 80px; }
.gm-oauth-box { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom: 16px; }
.gm-add-tabs { max-width: 520px; }
.gm-form { padding-top: 12px; }
.gm-form-actions { margin-top: 16px; }
.gm-batch-result { margin-top: 12px; }
.gm-settings-status { margin-bottom: 8px; }
@media (max-width: 768px) {
    .gm-settings-page { border-radius: 0; }
    .gm-acct { grid-template-columns: auto auto 1fr; }
    .gm-acct-links { grid-column: 3; justify-content: flex-start; }
}
</style>
