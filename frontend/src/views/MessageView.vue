<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NCheckbox, NCheckboxGroup, NDropdown, NIcon, NPopover, NSpin, NAlert } from "naive-ui";
import {
  ArrowBackOutlined,
  ArrowDropDownOutlined,
  AttachFileOutlined,
  BlockOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  DeleteOutlined,
  ForwardOutlined,
  LabelOutlined,
  MarkEmailUnreadOutlined,
  MoreVertOutlined,
  OpenInNewOutlined,
  PrintOutlined,
  ReplyAllOutlined,
  ReplyOutlined,
  ReportOffOutlined,
  ShieldOutlined,
  StarFilled,
  StarBorderOutlined,
} from "@vicons/material";
import IconBtn from "../components/IconBtn.vue";
import MailBodyFrame from "../components/MailBodyFrame.vue";
import AttachmentCards from "../components/AttachmentCards.vue";
import { get, post } from "../api";
import { openCompose, store } from "../store";
import { confirm, run, toast } from "../ui";
import { isMobile, refreshNav } from "../composables/common";
import { usePageShortcuts } from "../composables/shortcuts";
import { avatarColor, fullDate, initial, listDate, longDate, dateTime } from "../format";

const route = useRoute();
const router = useRouter();

const FOLDER_NAMES = { inbox: "收件箱", sent: "已发送", junk: "垃圾邮件" };
const DELETE_CONFIRM = "确定删除这封邮件吗？邮件会移到 Gmail 的「已删除邮件」，30 天后由 Gmail 自动清除。";

const msg = ref(null);
const error = ref("");
const loading = ref(false);
// 会话中每封邮件是否展开（折叠的邮件不加载正文）
const expanded = reactive({});
const showImages = ref(false);

const backUrl = computed(() => msg.value?.back_url || String(route.query.back || "/messages/"));
const lastMsg = computed(() => msg.value?.thread[msg.value.thread.length - 1]);
const threaded = computed(() => (msg.value?.thread.length || 0) > 1);

async function load() {
  loading.value = true;
  error.value = "";
  showImages.value = false;
  try {
    const data = await get(`/api/messages/${route.params.id}`, { back: route.query.back });
    msg.value = data;
    for (const key of Object.keys(expanded)) delete expanded[key];
    for (const m of data.thread) expanded[m.id] = m.expanded;
    labelSelection.value = data.thread_labels.map((lb) => lb.id);
    store.view.kind = data.folder_kind;
    const backAccount = new URLSearchParams(data.back_url.split("?")[1] || "").get("account_id");
    store.view.accountId = backAccount ? Number(backAccount) : null;
    document.title = `${data.subject || "(无主题)"} - Roundcube`;
    refreshNav();   // 打开邮件会把它标为已读
  } catch (err) {
    msg.value = null;
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

watch(() => route.params.id, (id) => id && load(), { immediate: true });

function goBack() {
  router.push(backUrl.value);
}

function openSibling(id) {
  if (id) router.push({ name: "message", params: { id }, query: { back: backUrl.value } });
}

function bodyUrl(m) {
  return `/messages/${m.id}/body${showImages.value ? "?images=1" : ""}`;
}

// ---- 操作 ----
async function remove() {
  if (!(await confirm(DELETE_CONFIRM, { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/messages/${msg.value.id}/delete`))) {
    toast("已删除邮件");
    refreshNav();
    goBack();
  }
}

async function markUnread() {
  if (await run(post(`/api/messages/${msg.value.id}/mark-unread`))) {
    refreshNav();
    goBack();
  }
}

async function toggleStar() {
  const value = !msg.value.is_starred;
  msg.value.is_starred = value;
  if (!(await run(post(`/api/messages/${msg.value.id}/star`, { starred: value })))) msg.value.is_starred = !value;
}

async function notSpam() {
  const who = msg.value.sender_email || "该发件人";
  const ok = await confirm(
    `这不是垃圾邮件？\n将把该发件人的垃圾邮件全部移回收件箱，并信任 ${who}：以后其邮件即使被 Gmail 判为垃圾邮件，也会自动移回收件箱。`,
  );
  if (!ok) return;
  const result = await run(post(`/api/messages/${msg.value.id}/not-spam`));
  if (result) {
    toast(result.message || "已移回收件箱");
    refreshNav();
    goBack();
  }
}

async function blockSender() {
  const email = msg.value.sender_email;
  const ok = await confirm(`永久屏蔽 ${email}？\n后续同步将自动忽略该发件人，并隐藏当前账号已有同发件人邮件。`, {
    danger: true,
    positiveText: "屏蔽",
  });
  if (!ok) return;
  const result = await run(post(`/api/messages/${msg.value.id}/block-sender`));
  if (result) {
    toast(`已屏蔽 ${email}${result.hidden ? `，隐藏了 ${result.hidden} 封邮件` : ""}`);
    refreshNav();
    goBack();
  }
}

function viewBlocked() {
  router.push({ path: "/messages/blocked-senders", query: { account_id: msg.value.account_id, q: msg.value.sender_email } });
}

// ---- 标签 ----
const labelSelection = ref([]);
const labelsOpen = ref(false);
async function applyLabels() {
  const result = await run(post(`/api/messages/${msg.value.id}/labels`, { label_ids: labelSelection.value }), "标签已更新");
  if (result) {
    msg.value.thread_labels = result.labels;
    labelsOpen.value = false;
    refreshNav();
  }
}

// ---- 回复 / 转发 ----
function reply(m, mode) {
  if (mode === "forward") openCompose({ forward: m.id });
  else openCompose({ reply_to: m.id, mode: mode === "all" ? "all" : undefined });
}

function printPage() {
  window.print();
}

function moreOptions() {
  return [
    { key: "reply", label: "回复" },
    { key: "all", label: "回复全部" },
    { key: "forward", label: "转发" },
    { type: "divider", key: "d" },
    { key: "print", label: "打印" },
    { key: "open", label: "在新窗口中打开正文" },
  ];
}

function onMore(m, key) {
  if (key === "print") printPage();
  else if (key === "open") window.open(`/messages/${m.id}/body?images=1`, "_blank", "noopener");
  else reply(m, key);
}

function toggleExpand(m) {
  if (threaded.value) expanded[m.id] = !expanded[m.id];
}

usePageShortcuts({
  u: goBack,
  k: () => openSibling(msg.value?.position.newer_id),
  j: () => openSibling(msg.value?.position.older_id),
  r: () => lastMsg.value && reply(lastMsg.value),
  a: () => lastMsg.value && reply(lastMsg.value, "all"),
  f: () => lastMsg.value && reply(lastMsg.value, "forward"),
  s: () => msg.value && toggleStar(),
  "#": () => msg.value && remove(),
  U: () => msg.value && markUnread(),
});
</script>

<template>
  <div class="gm-card gm-detail">
    <div class="gm-toolbar gm-detail-toolbar no-print">
      <div class="gm-toolbar-start">
        <IconBtn :icon="ArrowBackOutlined" title="返回列表（u）" @click="goBack" />
        <template v-if="msg">
          <span class="gm-toolbar-gap" />
          <IconBtn v-if="msg.folder_kind === 'junk'" :icon="ReportOffOutlined" title="不是垃圾邮件" @click="notSpam" />
          <IconBtn :icon="DeleteOutlined" title="删除（#）" @click="remove" />
          <span class="gm-toolbar-sep" />
          <IconBtn :icon="MarkEmailUnreadOutlined" title="标记为未读（Shift+U）" @click="markUnread" />
          <NPopover v-if="msg.account_labels.length" v-model:show="labelsOpen" trigger="click" placement="bottom-start">
            <template #trigger>
              <IconBtn :icon="LabelOutlined" title="标签" />
            </template>
            <div class="gm-label-menu">
              <div class="gm-label-menu-title">标签</div>
              <NCheckboxGroup v-model:value="labelSelection">
                <div v-for="lb in msg.account_labels" :key="lb.id" class="gm-label-menu-item">
                  <NCheckbox :value="lb.id" :label="lb.name" />
                </div>
              </NCheckboxGroup>
              <div class="gm-label-menu-actions">
                <NButton size="small" type="primary" quaternary @click="applyLabels">应用</NButton>
              </div>
            </div>
          </NPopover>
          <IconBtn
            :icon="msg.is_starred ? StarFilled : StarBorderOutlined"
            :color="msg.is_starred ? 'var(--gm-star)' : ''"
            :title="msg.is_starred ? '已加星标' : '未加星标'"
            @click="toggleStar"
          />
          <template v-if="msg.sender_email && msg.folder_kind !== 'sent'">
            <IconBtn v-if="msg.sender_blocked" :icon="ShieldOutlined" title="已屏蔽该发件人，查看屏蔽列表" @click="viewBlocked" />
            <IconBtn v-else :icon="BlockOutlined" :title="`屏蔽“${msg.thread[0]?.from_name || msg.sender_email}”`" @click="blockSender" />
          </template>
        </template>
      </div>
      <div v-if="msg" class="gm-toolbar-end">
        <span class="gm-pager-info">第 {{ msg.position.position }} 封，共 {{ msg.position.total }} 封</span>
        <IconBtn :icon="ChevronLeftOutlined" title="较新（k）" :disabled="!msg.position.newer_id" @click="openSibling(msg.position.newer_id)" />
        <IconBtn :icon="ChevronRightOutlined" title="较旧（j）" :disabled="!msg.position.older_id" @click="openSibling(msg.position.older_id)" />
      </div>
    </div>

    <div class="gm-detail-scroll">
      <NSpin v-if="loading && !msg" class="gm-detail-loading" />
      <div v-else-if="error" class="gm-empty">
        <div class="gm-empty-title">{{ error }}</div>
        <NButton @click="goBack">返回列表</NButton>
      </div>

      <div v-else-if="msg" class="gm-thread">
        <div class="gm-subject-row">
          <h2 class="gm-subject">
            <span class="gm-subject-text">{{ msg.subject || "(无主题)" }}</span>
            <span v-if="threaded" class="gm-thread-total" title="会话中的邮件数">{{ msg.thread.length }}</span>
            <RouterLink
              v-for="lb in msg.thread_labels"
              :key="lb.id"
              class="gm-chip is-label"
              :to="`/messages/?kind=label-${lb.id}&account_id=${msg.account_id}`"
              :title="lb.name"
            >{{ lb.name }}</RouterLink>
            <RouterLink
              class="gm-chip"
              :class="{ 'is-junk': msg.folder_kind === 'junk' }"
              :to="`/messages/?kind=${msg.folder_kind}&account_id=${msg.account_id}`"
            >{{ FOLDER_NAMES[msg.folder_kind] || msg.folder_kind }}</RouterLink>
          </h2>
          <div class="gm-subject-actions no-print">
            <IconBtn :icon="PrintOutlined" title="全部打印" @click="printPage" />
            <IconBtn :icon="OpenInNewOutlined" title="在新窗口中打开正文" @click="onMore(msg, 'open')" />
          </div>
        </div>

        <article
          v-for="m in msg.thread"
          :key="m.id"
          class="gm-msg"
          :class="{ 'is-expanded': expanded[m.id], 'is-threaded': threaded }"
        >
          <div class="gm-msg-head" @click="toggleExpand(m)">
            <span class="gm-avatar is-lg" :style="{ background: avatarColor(m.from_name) }" aria-hidden="true">{{ initial(m.from_name) }}</span>
            <div class="gm-msg-from">
              <div class="gm-msg-from-line">
                <span class="gm-msg-name">{{ m.from_name || "(未知发件人)" }}</span>
                <span v-if="m.from_email && m.from_name !== m.from_email" class="gm-msg-email">&lt;{{ m.from_email }}&gt;</span>
                <span v-if="threaded && m.folder_kind !== msg.folder_kind" class="gm-chip">{{ FOLDER_NAMES[m.folder_kind] || m.folder_kind }}</span>
              </div>
              <div v-if="!expanded[m.id]" class="gm-msg-snippet">{{ m.snippet }}</div>
              <NPopover v-else trigger="click" placement="bottom-start" :show-arrow="false">
                <template #trigger>
                  <button type="button" class="gm-msg-to-btn" title="显示详细信息" @click.stop>
                    发送至 {{ m.to_short }}<template v-if="m.cc_short">、{{ m.cc_short }}</template>
                    <NIcon :component="ArrowDropDownOutlined" :size="18" />
                  </button>
                </template>
                <table class="gm-msg-details">
                  <tr><th>发件人：</th><td>{{ m.from_full }}</td></tr>
                  <tr><th>收件人：</th><td>{{ m.to_full }}</td></tr>
                  <tr v-if="m.cc_full"><th>抄送：</th><td>{{ m.cc_full }}</td></tr>
                  <tr><th>日期：</th><td>{{ longDate(m.date) }}</td></tr>
                  <tr><th>主题：</th><td>{{ m.subject || "(无主题)" }}</td></tr>
                  <tr><th>账号：</th><td>{{ m.account_email }}</td></tr>
                </table>
              </NPopover>
            </div>
            <div class="gm-msg-meta" @click.stop>
              <NIcon v-if="m.attachments.some((a) => !a.is_inline)" :component="AttachFileOutlined" :size="18" class="gm-msg-clip" />
              <span class="gm-msg-date" :title="dateTime(m.date, true)">{{ isMobile ? listDate(m.date) : fullDate(m.date) }}</span>
              <template v-if="expanded[m.id] || !threaded">
                <IconBtn class="no-print" :icon="ReplyOutlined" title="回复" @click="reply(m)" />
                <NDropdown trigger="click" :options="moreOptions()" placement="bottom-end" @select="(key) => onMore(m, key)">
                  <IconBtn class="no-print" :icon="MoreVertOutlined" title="更多" />
                </NDropdown>
              </template>
            </div>
          </div>

          <div v-show="expanded[m.id] || !threaded" class="gm-msg-content">
            <NAlert v-if="m.remote_images && !showImages" type="info" :show-icon="false" class="gm-images-banner no-print">
              为保护隐私，下方的外部图片未显示。
              <a href="#" @click.prevent="showImages = true">显示图片</a>
            </NAlert>
            <MailBodyFrame :src="bodyUrl(m)" :active="!!(expanded[m.id] || !threaded)" />
            <AttachmentCards v-if="m.attachments.length" :attachments="m.attachments" :active="!!(expanded[m.id] || !threaded)" />
          </div>
        </article>

        <div class="gm-reply-row no-print">
          <NButton round @click="reply(lastMsg)">
            <template #icon><NIcon :component="ReplyOutlined" /></template>回复
          </NButton>
          <NButton round @click="reply(lastMsg, 'all')">
            <template #icon><NIcon :component="ReplyAllOutlined" /></template>回复全部
          </NButton>
          <NButton round @click="reply(lastMsg, 'forward')">
            <template #icon><NIcon :component="ForwardOutlined" /></template>转发
          </NButton>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-detail { display: flex; flex-direction: column; overflow: hidden; }
.gm-detail-scroll { flex: 1 1 auto; overflow-y: auto; min-height: 0; }
.gm-detail-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-toolbar-gap { width: 16px; }
.gm-thread { padding: 4px 24px 32px 72px; max-width: 1200px; }
.gm-subject-row { display: flex; align-items: flex-start; gap: 8px; margin: 12px 0 16px -56px; padding-left: 56px; }
.gm-subject { flex: 1; margin: 0; font: 400 22px/1.4 var(--gm-font); color: var(--gm-text); word-break: break-word; }
.gm-subject-text { margin-right: 12px; }
.gm-subject .gm-chip { font-size: 12px; vertical-align: 3px; }
.gm-thread-total { color: var(--gm-text-3); font-size: 14px; margin-right: 12px; }
.gm-subject-actions { display: flex; flex: none; }

.gm-msg { margin-left: -56px; border-bottom: 1px solid var(--gm-divider); }
.gm-msg:last-of-type { border-bottom: 0; }
.gm-msg-head { display: flex; align-items: flex-start; gap: 16px; padding: 12px 0 8px; }
.gm-msg.is-threaded .gm-msg-head { cursor: pointer; }
.gm-msg-from { flex: 1; min-width: 0; }
.gm-msg-from-line { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px; }
.gm-msg-name { font-weight: 700; font-size: 14px; color: var(--gm-text); }
.gm-msg-email { font-size: 12px; color: var(--gm-text-3); }
.gm-msg-snippet { color: var(--gm-text-3); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.gm-msg-to-btn {
    display: inline-flex;
    align-items: center;
    border: 0;
    background: none;
    padding: 2px 0;
    color: var(--gm-text-3);
    font-size: 12px;
    cursor: pointer;
    max-width: 100%;
}
.gm-msg-to-btn:hover { color: var(--gm-text); }
.gm-msg-details { font-size: 13px; border-collapse: collapse; max-width: 520px; }
.gm-msg-details th { color: var(--gm-text-3); font-weight: 400; text-align: right; padding: 2px 8px 2px 0; white-space: nowrap; vertical-align: top; }
.gm-msg-details td { padding: 2px 0; word-break: break-all; }
.gm-msg-meta { display: flex; align-items: center; gap: 2px; flex: none; color: var(--gm-text-2); font-size: 12px; }
.gm-msg-date { white-space: nowrap; padding: 0 4px; }
.gm-msg-clip { color: var(--gm-text-3); }
.gm-msg-content { padding: 0 0 16px 56px; }
.gm-images-banner { margin-bottom: 12px; }
.gm-reply-row { display: flex; flex-wrap: wrap; gap: 12px; padding: 16px 0 0; }

.gm-label-menu { min-width: 200px; max-height: 360px; overflow-y: auto; }
.gm-label-menu-title { font-size: 12px; color: var(--gm-text-3); padding: 0 0 6px; }
.gm-label-menu-item { padding: 4px 0; }
.gm-label-menu-actions { display: flex; justify-content: flex-end; border-top: 1px solid var(--gm-divider); margin-top: 6px; padding-top: 6px; }

@media (max-width: 768px) {
    .gm-detail { border-radius: 0; overflow: visible; }
    .gm-detail-scroll { overflow: visible; }
    .gm-thread { padding: 4px 16px 32px; }
    .gm-subject-row { margin-left: 0; padding-left: 0; }
    .gm-msg { margin-left: 0; }
    .gm-msg-head { gap: 12px; }
    .gm-msg-content { padding-left: 0; }
    .gm-toolbar { position: sticky; top: 0; z-index: 4; background: #fff; }
    .gm-pager-info { display: none; }
}

@media print {
    .gm-thread { padding: 0; }
    .gm-detail-scroll { overflow: visible; }
}
</style>
