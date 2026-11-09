<script setup>
// 通知：本机的桌面通知、Telegram 推送（网页上保存的设置优先，没填的沿用 .env）、各账号是否推送。
import { computed, reactive, ref, watch } from "vue";
import { NButton, NForm, NFormItem, NInput, NSwitch, NTag } from "naive-ui";
import GmAvatar from "../../ui/GmAvatar.vue";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { confirm, run, toast } from "../../ui";
import { desktopNotify, setDesktopNotify } from "../../desktopNotify";
import { useResource } from "../../composables/useResource";

const res = useResource(() => get("/api/accounts"));
const data = res.data;
const accounts = computed(() => data.value?.accounts || []);

async function toggleNotify(a) {
  const result = await run(post(`/api/accounts/${a.id}/toggle-notify`));
  if (result) a.notify_enabled = result.notify_enabled;
}

// ---- Telegram ----
const tgRes = useResource(() => get("/api/telegram/settings"));
const tg = tgRes.data;
const form = reactive({ bot_token: "", chat_ids: "", notify_enabled: false, notify_sent: false, notify_junk: true, commands_enabled: true });
const saving = ref(false);
const testing = ref(false);

function fillForm(state) {
  if (!state) return;
  Object.assign(form, {
    bot_token: "",
    chat_ids: state.chat_ids.join(", "),
    notify_enabled: state.notify_enabled,
    notify_sent: state.notify_sent,
    notify_junk: state.notify_junk,
    commands_enabled: state.commands_enabled,
  });
}
watch(tg, fillForm, { immediate: true });

const fromWeb = (key) => tg.value?.overridden.includes(key);
const dirty = computed(() => {
  const t = tg.value;
  if (!t) return false;
  return (
    !!form.bot_token.trim() ||
    form.chat_ids.split(/[,，\s]+/).filter(Boolean).join(",") !== t.chat_ids.join(",") ||
    ["notify_enabled", "notify_sent", "notify_junk", "commands_enabled"].some((k) => form[k] !== t[k])
  );
});

async function saveTelegram(extra = {}) {
  saving.value = true;
  const result = await run(post("/api/telegram/settings", { ...form, ...extra }), (r) => r.message);
  saving.value = false;
  if (result) {
    tg.value = result;
    res.reload({ silent: true });
  }
}

async function clearToken() {
  if (!(await confirm("清除网页上保存的机器人令牌？之后使用 .env 中的 TELEGRAM_BOT_TOKEN（没有则无法推送）。", { positiveText: "清除" }))) return;
  await saveTelegram({ bot_token: "", clear_token: true });
}

async function resetTelegram() {
  if (!(await confirm("清除网页上保存的全部 Telegram 设置，恢复为 .env 中的配置？", { positiveText: "恢复" }))) return;
  await saveTelegram({ reset: true });
}

// 填了新令牌或改了 Chat ID 时用表单里的值试发（不保存），否则用已保存的设置
async function telegramTest() {
  testing.value = true;
  const body = dirty.value ? { bot_token: form.bot_token.trim() || null, chat_ids: form.chat_ids } : {};
  const result = await run(post("/api/accounts/telegram-test", body));
  testing.value = false;
  if (result) toast(result.message, { tone: result.ok ? "normal" : "error", duration: result.ok ? 4000 : 10000 });
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

    <GmEmptyState v-if="tgRes.error.value" tone="error" title="Telegram 设置加载失败" :hint="tgRes.error.value" action-text="重试" @action="tgRes.reload()" />
    <section v-else-if="tg" class="gm-settings-row">
      <div class="gm-settings-label">
        Telegram 推送
        <div class="gm-settings-hint">收到新邮件时推送到 Telegram，并可在消息里直接标为已读或删除；安全提醒、证书到期、备份也通过它发送</div>
        <div class="gm-nt-status">
          <NTag v-if="tg.enabled" type="success" :bordered="false">推送中</NTag>
          <NTag v-else-if="tg.configured" :bordered="false">已配置 · 推送已关闭</NTag>
          <NTag v-else type="warning" :bordered="false">未配置</NTag>
        </div>
      </div>
      <div class="gm-settings-content">
        <NForm label-placement="top" class="gm-nt-form" @submit.prevent="saveTelegram()">
          <NFormItem label="机器人令牌（Bot Token）" :show-feedback="false" class="gm-nt-item">
            <NInput
              v-model:value="form.bot_token"
              type="password"
              show-password-on="click"
              :placeholder="tg.token_masked ? `已设置 ${tg.token_masked}，留空不修改` : '在 Telegram 的 @BotFather 中创建机器人后获得，如 123456789:AAH…'"
              :input-props="{ autocomplete: 'off', spellcheck: false }"
            />
          </NFormItem>
          <div class="gm-settings-hint gm-nt-src">
            <template v-if="fromWeb('bot_token')">使用网页上保存的令牌 · <a href="#" @click.prevent="clearToken">清除</a></template>
            <template v-else-if="tg.token_masked">来自 .env 的 TELEGRAM_BOT_TOKEN</template>
          </div>
          <NFormItem label="Chat ID" :show-feedback="false" class="gm-nt-item">
            <NInput v-model:value="form.chat_ids" placeholder="接收通知的对话，多个用逗号分隔" :input-props="{ spellcheck: false }" />
          </NFormItem>
          <div class="gm-settings-hint gm-nt-src">
            个人对话填数字 ID（可向 @userinfobot 发消息查询）；群组 / 频道通常以 -100 开头，频道也可填 @用户名。填好后先在 Telegram 中给机器人发送 /start。
            <template v-if="!fromWeb('chat_ids') && tg.chat_ids.length"> 当前来自 .env 的 TELEGRAM_CHAT_ID。</template>
          </div>

          <div class="gm-nt-switches">
            <label class="gm-nt-switch"><NSwitch v-model:value="form.notify_enabled" /><span>推送新邮件</span></label>
            <label class="gm-nt-switch"><NSwitch v-model:value="form.notify_junk" :disabled="!form.notify_enabled" /><span>垃圾邮件也推送</span></label>
            <label class="gm-nt-switch"><NSwitch v-model:value="form.notify_sent" :disabled="!form.notify_enabled" /><span>已发送的邮件也推送</span></label>
            <label class="gm-nt-switch">
              <NSwitch v-model:value="form.commands_enabled" />
              <span>机器人按钮与命令<span class="gm-settings-hint">（消息里的「标记已读 / 删除」按钮，/unread、/status 等命令）</span></span>
            </label>
          </div>

          <div class="gm-settings-actions">
            <NButton type="primary" attr-type="submit" :loading="saving" :disabled="!dirty">保存</NButton>
            <NButton secondary :loading="testing" :disabled="!dirty && !tg.configured" @click="telegramTest">{{ dirty ? "用填写的设置发送测试消息" : "发送测试消息" }}</NButton>
            <NButton v-if="dirty" quaternary @click="fillForm(tg)">撤销修改</NButton>
            <NButton v-else-if="tg.overridden.length" quaternary @click="resetTelegram">恢复为 .env 配置</NButton>
          </div>
          <p class="gm-settings-hint gm-nt-tip">测试提示「屏蔽了机器人」时：在 Telegram 中打开与机器人的对话，点「取消屏蔽」或「重新启动」。</p>
        </NForm>
      </div>
    </section>

    <GmEmptyState v-if="res.error.value" tone="error" title="账号信息加载失败" :hint="res.error.value" action-text="重试" @action="res.reload()" />
    <template v-else-if="data">
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
.gm-nt-status { margin-top: var(--sp-2); }
.gm-nt-form { max-width: 520px; }
.gm-nt-item { margin-top: var(--sp-2); }
.gm-nt-src { margin: 4px 0 var(--sp-2); }
.gm-nt-switches { display: flex; flex-direction: column; gap: var(--sp-3); margin: var(--sp-4) 0 var(--sp-2); }
.gm-nt-switch { display: flex; align-items: center; gap: var(--sp-3); cursor: pointer; }
.gm-nt-tip { margin-top: var(--sp-3) !important; }
.gm-nt-acc { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) 0; border-bottom: 1px solid var(--gm-divider); }
.gm-nt-acc:last-child { border-bottom: 0; }
.gm-nt-acc-main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.gm-nt-acc-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-nt-acc-email { font-size: var(--fs-xs); color: var(--gm-text-3); }
</style>
