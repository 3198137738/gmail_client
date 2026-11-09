<script setup>
// 通知：本机的桌面通知、Telegram 推送（服务器配置）、各账号是否推送。
import { computed } from "vue";
import { NButton, NSwitch, NTag } from "naive-ui";
import GmAvatar from "../../ui/GmAvatar.vue";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { run, toast } from "../../ui";
import { desktopNotify, setDesktopNotify } from "../../desktopNotify";
import { useResource } from "../../composables/useResource";

const res = useResource(() => get("/api/accounts"));
const data = res.data;
const accounts = computed(() => data.value?.accounts || []);

async function toggleNotify(a) {
  const result = await run(post(`/api/accounts/${a.id}/toggle-notify`));
  if (result) a.notify_enabled = result.notify_enabled;
}

async function telegramTest() {
  const result = await run(post("/api/accounts/telegram-test"));
  if (result) toast(result.message, { tone: result.ok ? "normal" : "error" });
}
</script>

<template>
  <div class="gm-settings">
    <section class="gm-settings-row">
      <div class="gm-settings-label">
        桌面通知
        <div class="gm-settings-hint">这台设备的浏览器不在前台时，新邮件弹出系统通知（含发件人、主题、验证码），点击直接打开</div>
      </div>
      <div class="gm-settings-content">
        <template v-if="desktopNotify.supported">
          <NSwitch :value="desktopNotify.enabled" @update:value="setDesktopNotify" />
          <span class="gm-nt-state">{{ desktopNotify.enabled ? "已开启" : "已关闭" }}</span>
        </template>
        <p v-else class="gm-settings-hint">这个浏览器不支持桌面通知。</p>
      </div>
    </section>

    <GmEmptyState v-if="res.error.value" tone="error" title="账号信息加载失败" :hint="res.error.value" action-text="重试" @action="res.reload()" />
    <template v-else-if="data">
      <section class="gm-settings-row">
        <div class="gm-settings-label">
          Telegram 推送
          <div class="gm-settings-hint">收到新邮件时推送到 Telegram，并可在消息里直接标为已读或删除</div>
        </div>
        <div class="gm-settings-content">
          <div class="gm-nt-status">
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

      <section v-if="accounts.length" class="gm-settings-row">
        <div class="gm-settings-label">
          推送哪些账号
          <div class="gm-settings-hint">关闭后该账号的新邮件不推送 Telegram（静音），仍会正常收取</div>
        </div>
        <div class="gm-settings-content">
          <div v-for="a in accounts" :key="a.id" class="gm-nt-acc">
            <GmAvatar :name="a.display_name || a.email" :color="a.color" :size="28" />
            <span class="gm-nt-acc-main">
              <span class="gm-nt-acc-name">{{ a.display_name || a.email }}</span>
              <span v-if="a.display_name" class="gm-nt-acc-email">{{ a.email }}</span>
            </span>
            <NSwitch :value="a.notify_enabled" size="small" :aria-label="`${a.email} 推送通知`" @update:value="toggleNotify(a)" />
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.gm-nt-state { margin-left: var(--sp-2); color: var(--gm-text-2); }
.gm-nt-status { margin-bottom: var(--sp-2); }
.gm-nt-acc { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) 0; border-bottom: 1px solid var(--gm-divider); }
.gm-nt-acc:last-child { border-bottom: 0; }
.gm-nt-acc-main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.gm-nt-acc-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-nt-acc-email { font-size: var(--fs-xs); color: var(--gm-text-3); }
</style>
