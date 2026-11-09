<script setup>
// 会话中的一封邮件：发件人行（点击展开 / 折叠）、收件人详情、提示（钓鱼、验证码、外部图片）、正文、附件。
// 发件记录也用它（没有回复等操作）。
import { NAlert, NButton, NDropdown, NIcon, NPopover } from "naive-ui";
import { ArrowDropDownOutlined, AttachFileOutlined, KeyOutlined, MoreVertOutlined, ReplyOutlined } from "@vicons/material";
import MailBodyFrame from "../components/MailBodyFrame.vue";
import AttachmentCards from "./AttachmentCards.vue";
import GmAvatar from "../ui/GmAvatar.vue";
import GmIconButton from "../ui/GmIconButton.vue";
import { copyText } from "../ui";
import { dateTime, fullDate, listDate, longDate } from "../format";

const props = defineProps({
  message: { type: Object, required: true },
  expanded: { type: Boolean, default: true },
  collapsible: { type: Boolean, default: false },
  bodySrc: { type: String, required: true },
  showImages: { type: Boolean, default: false },
  folderLabel: { type: String, default: "" },
  compactDate: { type: Boolean, default: false },
  actions: { type: Boolean, default: true },
});
const emit = defineEmits(["toggle", "reply", "more", "show-images"]);

const MORE = [
  { key: "reply", label: "回复" },
  { key: "all", label: "回复全部" },
  { key: "forward", label: "转发" },
  { type: "divider", key: "d" },
  { key: "print", label: "打印" },
  { key: "open", label: "在新窗口中打开正文" },
];

function onHead() {
  if (props.collapsible) emit("toggle");
}
</script>

<template>
  <article class="gm-tm" :class="{ 'is-open': expanded, 'is-collapsible': collapsible }">
    <div class="gm-tm-head" @click="onHead">
      <GmAvatar :name="message.from_name" :size="40" />
      <div class="gm-tm-from">
        <div class="gm-tm-line">
          <span class="gm-tm-name">{{ message.from_name || "(未知发件人)" }}</span>
          <span v-if="message.from_email && message.from_name !== message.from_email" class="gm-tm-email">{{ message.from_email }}</span>
          <span v-if="folderLabel" class="gm-tm-folder">{{ folderLabel }}</span>
        </div>
        <div v-if="!expanded" class="gm-tm-snippet">{{ message.snippet }}</div>
        <NPopover v-else trigger="click" placement="bottom-start" :show-arrow="false">
          <template #trigger>
            <button type="button" class="gm-tm-to" title="显示详细信息" @click.stop>
              发送至 {{ message.to_short }}<template v-if="message.cc_short">、{{ message.cc_short }}</template>
              <NIcon :component="ArrowDropDownOutlined" :size="18" />
            </button>
          </template>
          <table class="gm-tm-details">
            <tr><th>发件人</th><td>{{ message.from_full }}</td></tr>
            <tr><th>收件人</th><td>{{ message.to_full }}</td></tr>
            <tr v-if="message.cc_full"><th>抄送</th><td>{{ message.cc_full }}</td></tr>
            <tr v-if="message.bcc_full"><th>密送</th><td>{{ message.bcc_full }}</td></tr>
            <tr><th>日期</th><td>{{ longDate(message.date) }}</td></tr>
            <tr><th>主题</th><td>{{ message.subject || "(无主题)" }}</td></tr>
            <tr v-if="message.account_email"><th>账号</th><td>{{ message.account_email }}</td></tr>
          </table>
        </NPopover>
      </div>
      <div class="gm-tm-meta" @click.stop>
        <NIcon v-if="message.attachments.some((a) => !a.is_inline)" :component="AttachFileOutlined" :size="16" class="gm-tm-clip" />
        <span class="gm-tm-date" :title="dateTime(message.date, true)">{{ compactDate ? listDate(message.date) : fullDate(message.date) }}</span>
        <template v-if="actions && expanded">
          <GmIconButton class="no-print" :icon="ReplyOutlined" title="回复" :size="32" :icon-size="18" @click="emit('reply', 'reply')" />
          <NDropdown trigger="click" :options="MORE" placement="bottom-end" @select="(key) => emit('more', key)">
            <GmIconButton class="no-print" :icon="MoreVertOutlined" title="更多" :size="32" :icon-size="18" />
          </NDropdown>
        </template>
      </div>
    </div>

    <div v-show="expanded" class="gm-tm-content">
      <NAlert v-if="message.phishing?.length" type="warning" title="这封邮件可能是钓鱼邮件" class="gm-tm-alert">
        <ul class="gm-tm-reasons">
          <li v-for="w in message.phishing" :key="w">{{ w }}</li>
        </ul>
        请勿点击其中的链接或输入密码；如有疑问，直接打开官方网站或 App 核实。
      </NAlert>
      <div v-if="message.otp_code" class="gm-tm-otp">
        <NIcon :component="KeyOutlined" :size="18" />
        <span>验证码</span>
        <code class="gm-tm-otp-code">{{ message.otp_code }}</code>
        <NButton size="small" type="primary" secondary @click.stop="copyText(message.otp_code, `已复制验证码 ${message.otp_code}`)">复制</NButton>
      </div>
      <div v-if="message.remote_images && !showImages" class="gm-tm-images no-print">
        为保护隐私，外部图片未显示。
        <button type="button" @click="emit('show-images')">显示图片</button>
      </div>
      <MailBodyFrame :src="bodySrc" :active="expanded" />
      <AttachmentCards v-if="message.attachments.length" :attachments="message.attachments" :active="expanded" />
    </div>
  </article>
</template>

<style scoped>
.gm-tm {
    margin-bottom: var(--sp-3);
    border: 1px solid var(--gm-border);
    border-radius: var(--r-lg);
    background: var(--gm-surface);
    overflow: hidden;
}
.gm-tm-head { display: flex; align-items: flex-start; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); }
.gm-tm.is-collapsible .gm-tm-head { cursor: pointer; }
.gm-tm.is-collapsible:not(.is-open) { background: var(--gm-surface-2); }
.gm-tm.is-collapsible:not(.is-open):hover { background: var(--gm-hover); }
.gm-tm-from { flex: 1; min-width: 0; }
.gm-tm-line { display: flex; align-items: baseline; flex-wrap: wrap; gap: 2px var(--sp-2); }
.gm-tm-name { font-weight: 600; color: var(--gm-text); }
.gm-tm-email { font-size: var(--fs-xs); color: var(--gm-text-3); }
.gm-tm-folder {
    padding: 0 6px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-sm);
    font-size: 11px;
    line-height: 18px;
    color: var(--gm-text-3);
}
.gm-tm-snippet { margin-top: 2px; font-size: var(--fs-sm); color: var(--gm-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-tm-to {
    display: inline-flex;
    align-items: center;
    max-width: 100%;
    padding: 2px 0;
    border: 0;
    background: none;
    color: var(--gm-text-3);
    font: inherit;
    font-size: var(--fs-xs);
    cursor: pointer;
}
.gm-tm-to:hover { color: var(--gm-primary); }
.gm-tm-details { max-width: 520px; border-collapse: collapse; font-size: var(--fs-sm); }
.gm-tm-details th { padding: 3px 12px 3px 0; color: var(--gm-text-3); font-weight: 400; text-align: right; white-space: nowrap; vertical-align: top; }
.gm-tm-details td { padding: 3px 0; word-break: break-all; }
.gm-tm-meta { display: flex; align-items: center; gap: 2px; flex: none; color: var(--gm-text-3); font-size: var(--fs-xs); }
.gm-tm-date { padding: 0 var(--sp-1); white-space: nowrap; }
.gm-tm-clip { color: var(--gm-text-3); }
.gm-tm-content { padding: 0 var(--sp-4) var(--sp-4); }
.gm-tm-alert { margin-bottom: var(--sp-3); }
.gm-tm-reasons { margin: 4px 0 6px; padding-left: 20px; }
.gm-tm-reasons li { word-break: break-all; }
.gm-tm-otp {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    margin-bottom: var(--sp-3);
    padding: 10px 14px;
    border: 1px solid color-mix(in srgb, var(--gm-primary) 40%, transparent);
    border-radius: var(--r-md);
    background: var(--gm-primary-soft);
    color: var(--gm-text-2);
}
.gm-tm-otp-code { font: 700 22px/1.2 ui-monospace, Consolas, monospace; letter-spacing: 3px; color: var(--gm-primary); background: none; padding: 0; }
.gm-tm-images {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    margin-bottom: var(--sp-3);
    padding: var(--sp-2) var(--sp-3);
    border-radius: var(--r-md);
    background: var(--gm-surface-2);
    color: var(--gm-text-2);
    font-size: var(--fs-sm);
}
.gm-tm-images button { padding: 0; border: 0; background: none; color: var(--gm-primary); font: inherit; font-weight: 600; cursor: pointer; }

@media (max-width: 768px) {
    .gm-tm { border-radius: var(--r-md); }
    .gm-tm-head { padding: 10px 12px; gap: 10px; }
    .gm-tm-content { padding: 0 12px 12px; }
}
@media print {
    .gm-tm { border: 0; }
}
</style>
