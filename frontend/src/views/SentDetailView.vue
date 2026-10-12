<script setup>
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NAlert, NButton, NIcon, NPopover, NSpin } from "naive-ui";
import { ArrowBackOutlined, ArrowDropDownOutlined, DeleteOutlined, PrintOutlined } from "@vicons/material";
import IconBtn from "../components/IconBtn.vue";
import MailBodyFrame from "../components/MailBodyFrame.vue";
import { get, post } from "../api";
import { confirm, run } from "../ui";
import { avatarColor, dateTime, fullDate, initial, longDate } from "../format";

const route = useRoute();
const router = useRouter();
const msg = ref(null);
const error = ref("");
const showImages = ref(false);
const me = computed(() => msg.value?.account_name || msg.value?.account_email || "");

async function load() {
  error.value = "";
  showImages.value = false;
  try {
    msg.value = await get(`/api/sent/${route.params.id}`);
    document.title = `${msg.value.subject || "(无主题)"} - Roundcube`;
  } catch (err) {
    msg.value = null;
    error.value = err.message;
  }
}
watch(() => route.params.id, (id) => id && load(), { immediate: true });

function printPage() {
  window.print();
}

function back() {
  router.push("/compose/sent");
}

async function remove() {
  if (!(await confirm("确定删除这条发件记录吗？", { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/sent/${msg.value.id}/delete`), "已删除")) back();
}
</script>

<template>
  <div class="gm-panel gm-detail">
    <div class="gm-toolbar no-print">
      <div class="gm-toolbar-start">
        <IconBtn :icon="ArrowBackOutlined" title="返回发件记录" @click="back" />
        <IconBtn v-if="msg" :icon="DeleteOutlined" title="删除记录" @click="remove" />
      </div>
    </div>
    <div class="gm-detail-scroll">
      <div v-if="error" class="gm-empty">
        <div class="gm-empty-title">{{ error }}</div>
        <NButton @click="back">返回发件记录</NButton>
      </div>
      <NSpin v-else-if="!msg" class="gm-detail-loading" />
      <div v-else class="gm-thread">
        <div class="gm-subject-row">
          <h2 class="gm-subject">
            <span class="gm-subject-text">{{ msg.subject || "(无主题)" }}</span>
            <span v-if="msg.status === 'sent' || msg.status === 'partial'" class="gm-chip">已发送</span>
            <span v-else class="gm-chip is-junk">发送失败</span>
          </h2>
          <IconBtn class="no-print" :icon="PrintOutlined" title="打印" @click="printPage" />
        </div>

        <NAlert v-if="msg.error && msg.status === 'partial'" type="warning" class="gm-msg-alert">{{ msg.error }}</NAlert>
        <NAlert v-else-if="msg.error && msg.status !== 'sent'" type="error" class="gm-msg-alert">发送失败：{{ msg.error }}</NAlert>

        <div class="gm-msg-head">
          <span class="gm-avatar is-lg" :style="{ background: avatarColor(me) }" aria-hidden="true">{{ initial(me) }}</span>
          <div class="gm-msg-from">
            <div>
              <span class="gm-msg-name">{{ me }}</span>
              <span class="gm-msg-email">&lt;{{ msg.account_email }}&gt;</span>
            </div>
            <NPopover trigger="click" placement="bottom-start" :show-arrow="false">
              <template #trigger>
                <button type="button" class="gm-msg-to-btn" title="显示详细信息">
                  发送至 {{ msg.to_short }}<template v-if="msg.cc_short">、{{ msg.cc_short }}</template>
                  <NIcon :component="ArrowDropDownOutlined" :size="18" />
                </button>
              </template>
              <table class="gm-msg-details">
                <tr><th>发件人：</th><td>{{ msg.account_email }}</td></tr>
                <tr><th>收件人：</th><td>{{ msg.to_full }}</td></tr>
                <tr v-if="msg.cc_full"><th>抄送：</th><td>{{ msg.cc_full }}</td></tr>
                <tr v-if="msg.bcc_full"><th>密送：</th><td>{{ msg.bcc_full }}</td></tr>
                <tr><th>日期：</th><td>{{ longDate(msg.time) }}</td></tr>
                <tr><th>主题：</th><td>{{ msg.subject || "(无主题)" }}</td></tr>
              </table>
            </NPopover>
          </div>
          <span class="gm-msg-date" :title="dateTime(msg.time, true)">{{ fullDate(msg.time) }}</span>
        </div>

        <div class="gm-msg-content">
          <NAlert v-if="msg.remote_images && !showImages" type="info" :show-icon="false" class="gm-images-banner no-print">
            为保护隐私，下方的外部图片未显示。
            <a href="#" @click.prevent="showImages = true">显示图片</a>
          </NAlert>
          <MailBodyFrame :src="`/compose/sent/${msg.id}/body${showImages ? '?images=1' : ''}`" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>

.gm-detail-scroll { flex: 1 1 auto; overflow-y: auto; min-height: 0; }
.gm-detail-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-thread { padding: 16px 24px 32px; max-width: 1100px; }
.gm-subject-row { display: flex; align-items: flex-start; gap: 8px; margin: 12px 0 16px; }
.gm-subject { flex: 1; margin: 0; font-size: 20px; font-weight: 600; line-height: 1.4; word-break: break-word; }
.gm-subject-text { margin-right: 12px; }
.gm-subject .gm-chip { vertical-align: 3px; }
.gm-msg-alert { margin-bottom: 12px; }
.gm-msg-head { display: flex; align-items: flex-start; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--gm-divider); margin-bottom: 12px; }
.gm-msg-from { flex: 1; min-width: 0; }
.gm-msg-name { font-weight: 700; margin-right: 4px; }
.gm-msg-email { font-size: 12px; color: var(--gm-text-3); }
.gm-msg-to-btn { display: inline-flex; align-items: center; border: 0; background: none; padding: 2px 0; color: var(--gm-text-3); font-size: 12px; cursor: pointer; }
.gm-msg-details { font-size: 13px; border-collapse: collapse; max-width: 520px; }
.gm-msg-details th { color: var(--gm-text-3); font-weight: 400; text-align: right; padding: 2px 8px 2px 0; white-space: nowrap; vertical-align: top; }
.gm-msg-details td { padding: 2px 0; word-break: break-all; }
.gm-msg-date { font-size: 12px; color: var(--gm-text-2); white-space: nowrap; }
.gm-images-banner { margin-bottom: 12px; }
@media (max-width: 768px) {
    .gm-thread { padding: 4px 16px 32px; }

}
</style>
