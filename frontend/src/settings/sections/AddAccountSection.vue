<script setup>
// 添加账号：Gmail（Google 授权或应用专用密码，可批量）、Outlook（只能 Microsoft 授权）。
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { NAlert, NButton, NCheckbox, NForm, NFormItem, NIcon, NInput, NTabPane, NTabs } from "naive-ui";
import { ArrowBackOutlined } from "@vicons/material";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { run } from "../../ui";
import { hardNavigate, refreshNav } from "../../composables/common";
import { useResource } from "../../composables/useResource";

const router = useRouter();
const res = useResource(() => get("/api/accounts"));
const data = res.data;

const single = reactive({ email: "", display_name: "", password: "", verify: false });
const batch = reactive({ text: "", verify: false });
const adding = ref(false);
const batchResult = ref(null);

// 与后端 mail_providers.OUTLOOK_DOMAINS 一致：这些地址不能用密码添加
const OUTLOOK_DOMAIN_RE = /@(outlook\.|hotmail\.|live\.|msn\.com$|windowslive\.com$|passport\.com$)/i;
const singleIsOutlook = computed(() => OUTLOOK_DOMAIN_RE.test(single.email.trim()));

async function addSingle() {
  if (!single.email || !single.password) return;
  adding.value = true;
  const result = await run(post("/api/accounts", { ...single }), `已添加 ${single.email}，正在同步邮件`);
  adding.value = false;
  if (result) {
    Object.assign(single, { email: "", display_name: "", password: "", verify: false });
    refreshNav();
    router.push("/settings/accounts");
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
    refreshNav();
  }
}

// 授权需要整页跳转到 Google / Microsoft 的授权页面，完成后回到账号列表
const oauthStart = () => hardNavigate("/accounts/oauth/start");
const msOauthStart = () => hardNavigate("/accounts/oauth/microsoft/start");
</script>

<template>
  <div class="gm-settings">
    <NButton text class="gm-aa-back" @click="router.push('/settings/accounts')">
      <template #icon><NIcon :component="ArrowBackOutlined" /></template>返回账号列表
    </NButton>

    <GmEmptyState v-if="res.error.value" tone="error" title="加载失败" :hint="res.error.value" action-text="重试" @action="res.reload()" />
    <template v-else-if="data">
      <div class="gm-aa-cards">
        <section class="gm-aa-card">
          <div class="gm-aa-title"><span class="gm-aa-logo is-gmail">G</span>Gmail</div>
          <p class="gm-settings-hint">推荐用 Google 账号授权：无需应用专用密码，已有账号再授权一次即可改为授权登录。</p>
          <NButton type="primary" :disabled="!data.oauth_configured" @click="oauthStart">使用 Google 账号授权添加</NButton>
          <div v-if="!data.oauth_configured" class="gm-settings-hint gm-aa-setup">
            未配置 Google 授权，可用下方的应用专用密码添加。启用授权：在 <code>.env</code> 中设置
            <code>GOOGLE_OAUTH_CLIENT_ID</code> 与 <code>GOOGLE_OAUTH_CLIENT_SECRET</code> 后重启，重定向 URI 填
            <code class="gm-aa-copy">{{ data.oauth_redirect_uri }}</code>
          </div>
        </section>
        <section class="gm-aa-card">
          <div class="gm-aa-title"><span class="gm-aa-logo is-outlook">O</span>Outlook / Hotmail / Microsoft 365</div>
          <p class="gm-settings-hint">Outlook 邮箱只能用 Microsoft 账号授权添加；授权失效时再授权一次即可。</p>
          <NButton type="primary" :disabled="!data.ms_oauth_configured" @click="msOauthStart">使用 Microsoft 账号授权添加</NButton>
          <div v-if="data.ms_oauth_configured" class="gm-settings-hint gm-aa-setup">
            添加前先在 Outlook 网页版 设置 › 邮件 ›
            <a href="https://outlook.live.com/mail/0/options/mail/forwarding" target="_blank" rel="noopener noreferrer">转发和 IMAP</a>
            中开启 IMAP，否则无法同步。
          </div>
          <ol v-else class="gm-settings-hint gm-aa-steps">
            <li>
              在 <a href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade" target="_blank" rel="noopener noreferrer">Azure 门户 › 应用注册</a>
              新建应用（只需一次，所有 Outlook 邮箱共用），账户类型选「任何组织目录中的帐户和个人 Microsoft 帐户」
            </li>
            <li>平台选「Web」，重定向 URI 填 <code class="gm-aa-copy">{{ data.ms_oauth_redirect_uri }}</code></li>
            <li>在「证书和密码」中新建客户端密码</li>
            <li>在 <code>.env</code> 中设置 <code>MICROSOFT_OAUTH_CLIENT_ID</code>（应用程序 ID）与 <code>MICROSOFT_OAUTH_CLIENT_SECRET</code>（密码的值）后重启</li>
          </ol>
        </section>
      </div>

      <section class="gm-settings-row">
        <div class="gm-settings-label">
          用应用专用密码添加 Gmail
          <div class="gm-settings-hint">
            在 <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Google 账号 › 应用专用密码</a>
            生成（需先开启两步验证）
          </div>
        </div>
        <div class="gm-settings-content">
          <NTabs type="segment" size="small">
            <NTabPane name="single" tab="单个添加">
              <NForm class="gm-aa-form" label-placement="top" @submit.prevent="addSingle">
                <NFormItem label="Gmail 地址">
                  <NInput v-model:value="single.email" placeholder="you@gmail.com" :input-props="{ type: 'email' }" />
                </NFormItem>
                <NAlert v-if="singleIsOutlook" type="warning" :show-icon="false" class="gm-aa-warn">
                  Outlook / Hotmail 邮箱已不支持密码登录，请使用上方的「使用 Microsoft 账号授权添加」。
                </NAlert>
                <NFormItem label="显示名称（可选）">
                  <NInput v-model:value="single.display_name" placeholder="收件人看到的发件人名称" />
                </NFormItem>
                <NFormItem label="应用专用密码">
                  <NInput v-model:value="single.password" type="password" show-password-on="click" :input-props="{ autocomplete: 'new-password' }" />
                </NFormItem>
                <NCheckbox v-model:checked="single.verify">添加前通过 IMAP 验证一次（约 3–5 秒）</NCheckbox>
                <div class="gm-aa-actions">
                  <NButton type="primary" attr-type="submit" :loading="adding" :disabled="!single.email || !single.password || singleIsOutlook">添加账号</NButton>
                </div>
              </NForm>
            </NTabPane>
            <NTabPane name="batch" tab="批量添加">
              <NForm class="gm-aa-form" label-placement="top" @submit.prevent="addBatch">
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
                <NAlert v-if="batchResult" :type="batchResult.level" closable class="gm-aa-warn" @close="batchResult = null">{{ batchResult.message }}</NAlert>
                <div class="gm-aa-actions">
                  <NButton type="primary" attr-type="submit" :loading="adding" :disabled="!batch.text.trim()">批量添加</NButton>
                </div>
              </NForm>
            </NTabPane>
          </NTabs>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.gm-aa-back { margin-bottom: var(--sp-3); }
.gm-aa-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--sp-4); margin-bottom: var(--sp-4); }
.gm-aa-card { display: flex; flex-direction: column; align-items: flex-start; gap: var(--sp-2); padding: var(--sp-5); border: 1px solid var(--gm-border); border-radius: var(--r-lg); background: var(--gm-surface); }
.gm-aa-title { display: flex; align-items: center; gap: var(--sp-2); font-size: 15px; font-weight: 600; }
.gm-aa-logo { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: var(--r-sm); color: #fff; font-weight: 700; }
.gm-aa-logo.is-gmail { background: #d93025; }
.gm-aa-logo.is-outlook { background: #0078d4; }
.gm-aa-setup, .gm-aa-steps { margin: var(--sp-1) 0 0; }
.gm-aa-steps { padding-left: 18px; }
.gm-aa-copy { word-break: break-all; user-select: all; }
.gm-aa-form { max-width: 480px; margin-top: var(--sp-2); }
.gm-aa-warn { margin: var(--sp-2) 0; }
.gm-aa-actions { margin-top: var(--sp-3); }
</style>
