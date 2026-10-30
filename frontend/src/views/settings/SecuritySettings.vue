<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NInput, NSpin, NTag } from "naive-ui";
import SettingsHeader from "../../components/SettingsHeader.vue";
import { get, post } from "../../api";
import { confirm, run } from "../../ui";
import { resetAfterLogout } from "../../actions";
import { dateTime } from "../../format";

const router = useRouter();
const data = ref(null);
const enableCode = ref("");
const disableCode = ref("");
const banIp = ref("");

async function load() {
  const result = await run(get("/api/security"));
  if (result) data.value = result;
}
onMounted(load);

// 执行操作，成功后提示并刷新本页
async function act(url, body, { confirmText, danger = false } = {}) {
  if (confirmText && !(await confirm(confirmText, { danger }))) return null;
  const result = await run(post(url, body), (r) => r.message);
  if (result) await load();
  return result;
}

async function setup2fa() {
  await act("/api/security/2fa/setup");
}

async function enable2fa() {
  if (await act("/api/security/2fa/enable", { code: enableCode.value })) enableCode.value = "";
}

async function cancel2fa() {
  await act("/api/security/2fa/cancel");
}

async function disable2fa() {
  const result = await act("/api/security/2fa/disable", { code: disableCode.value }, {
    confirmText: "确定关闭两步验证吗？关闭后只需密码即可登录。",
    danger: true,
  });
  disableCode.value = "";
  return result;
}

function loggedOut() {
  resetAfterLogout();
  router.replace({ name: "login" });
}

async function revoke(s) {
  const text = s.is_current ? "确定登出本机吗？" : "确定登出该设备吗？";
  if (!(await confirm(text, { danger: true }))) return;
  const result = await run(post(`/api/security/sessions/${s.id}/revoke`), (r) => r.message);
  if (result?.logged_out) loggedOut();
  else if (result) load();
}

async function revokeOthers() {
  await act("/api/security/sessions/revoke-others", {}, { confirmText: "确定登出除本机以外的所有设备吗？", danger: true });
}

async function revokeAll() {
  if (!(await confirm("确定登出所有设备（包括本机）吗？", { danger: true }))) return;
  if (await run(post("/api/security/sessions/revoke-all"))) loggedOut();
}

async function ban(ip) {
  const result = await act("/api/security/ip-ban", { ip }, {
    confirmText: `确定永久拉黑 ${ip} 吗？该 IP 上的设备会立即登出，之后无法访问任何页面。`,
    danger: true,
  });
  if (result && ip === banIp.value) banIp.value = "";
}

async function unban(ip) {
  await act("/api/security/ip-unban", { ip }, { confirmText: `确定解除 ${ip} 的封禁吗？` });
}

async function backupNow() {
  await act("/api/security/backup");
}

const TARGET_NAMES = { telegram: "Telegram", webdav: "WebDAV" };

// ---- HTTPS 证书 ----
const cert = ref(null);
const certChecking = ref(false);
let certTimer = null;

const CERT_LEVELS = {
  ok: ["success", "正常"],
  warning: ["warning", "即将到期"],
  expired: ["error", "已过期"],
  invalid: ["error", "证书无效"],
  error: ["error", "检测失败"],
};
const RESULT_TYPES = { renewed: "success", not_due: "info", failed: "error" };
const certStatus = computed(() => cert.value?.status);
const renewal = computed(() => cert.value?.renewal);

async function loadCert() {
  const result = await run(get("/api/cert"));
  if (result) cert.value = result;
  // 续订请求等待执行时，每 3 秒刷新一次进度
  clearTimeout(certTimer);
  if (result?.renewal.pending) certTimer = setTimeout(loadCert, 3000);
}
onMounted(loadCert);
onBeforeUnmount(() => clearTimeout(certTimer));

async function checkCert() {
  certChecking.value = true;
  const result = await run(post("/api/cert/check"));
  certChecking.value = false;
  if (result) cert.value = result;
}

async function renewCert() {
  const days = certStatus.value?.days_left;
  // certbot 只在剩余 30 天内续订，更早需要强制重新签发
  const force = days != null && days > cert.value.renew_window_days;
  const text = force
    ? `证书还有 ${days} 天才到期，certbot 默认不会续订。确定强制重新签发吗？
Let's Encrypt 限制同一证书每周最多签发 5 次。`
    : "确定立即续订 HTTPS 证书吗？服务器会重新签发证书并重新加载 nginx。";
  if (!(await confirm(text, { title: force ? "强制续订证书" : "续订证书", positiveText: force ? "强制续订" : "续订" }))) return;
  const result = await run(post("/api/cert/renew", { force }), (r) => r.message);
  if (result) {
    cert.value = result;
    loadCert();
  }
}
</script>

<template>
  <div class="gm-panel gm-settings-page">
    <SettingsHeader />
    <div class="gm-settings">
      <NSpin v-if="!data" class="gm-settings-loading" />
      <template v-else>
        <!-- 两步验证 -->
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            两步验证：
            <div class="gm-settings-hint">
              登录时除了访问密码，还要输入手机认证器 App（Google Authenticator、Microsoft Authenticator 等）生成的 6 位验证码
            </div>
          </div>
          <div class="gm-settings-content">
            <p v-if="!data.auth_enabled" class="gm-settings-hint">未设置访问密码（WEB_AUTH_PASSWORD），无法开启两步验证。</p>
            <template v-else-if="data.two_factor.reset_active">
              <div class="gm-settings-status"><NTag type="warning" :bordered="false">应急模式</NTag></div>
              <p>已设置 <code>WEB_AUTH_2FA_RESET=true</code>，两步验证暂时停用，只需密码即可登录。</p>
              <p class="gm-settings-hint">请从 <code>.env</code> 中删除该变量并重启；如需重新绑定手机，在删除前先在这里关闭再重新开启。</p>
            </template>
            <template v-else-if="data.two_factor.enabled">
              <div class="gm-settings-status"><NTag type="success" :bordered="false">已开启</NTag></div>
              <form class="gm-inline-form" @submit.prevent="disable2fa">
                <NInput
                  v-model:value="disableCode"
                  placeholder="输入当前验证码以关闭"
                  :maxlength="7"
                  :input-props="{ inputmode: 'numeric', autocomplete: 'one-time-code' }"
                />
                <NButton attr-type="submit" secondary :disabled="!disableCode.trim()">关闭两步验证</NButton>
              </form>
              <p class="gm-settings-hint">手机丢失时：在服务器 <code>.env</code> 中设置 <code>WEB_AUTH_2FA_RESET=true</code> 并重启，即可只用密码登录。</p>
            </template>
            <template v-else-if="data.two_factor.setup_secret">
              <div class="gm-settings-status"><NTag :bordered="false">正在设置</NTag></div>
              <ol class="gm-2fa-steps">
                <li>
                  在手机认证器 App 中选择「输入设置密钥」（或在手机上 <a :href="data.two_factor.setup_uri">点此直接添加</a>），账号名随意，密钥填写：
                  <div class="gm-2fa-secret"><code>{{ data.two_factor.setup_secret }}</code></div>
                  类型选择「基于时间」。
                </li>
                <li>输入 App 中显示的 6 位验证码完成绑定：</li>
              </ol>
              <form class="gm-inline-form" @submit.prevent="enable2fa">
                <NInput
                  v-model:value="enableCode"
                  placeholder="6 位验证码"
                  :maxlength="7"
                  :input-props="{ inputmode: 'numeric', autocomplete: 'one-time-code', autofocus: true }"
                />
                <NButton attr-type="submit" type="primary" :disabled="!enableCode.trim()">验证并开启</NButton>
                <NButton quaternary @click="cancel2fa">取消</NButton>
              </form>
            </template>
            <template v-else>
              <div class="gm-settings-status"><NTag type="warning" :bordered="false">未开启</NTag></div>
              <p class="gm-settings-hint">开启后，即使访问密码泄露，没有你的手机也无法登录。</p>
              <NButton type="primary" @click="setup2fa">开启两步验证</NButton>
            </template>
          </div>
        </section>

        <!-- 登录设备 -->
        <section v-if="data.auth_enabled" class="gm-settings-row">
          <div class="gm-settings-label">
            登录设备：
            <div class="gm-settings-hint">登录过本客户端、尚未登出的设备。修改访问密码（.env 中的 WEB_AUTH_PASSWORD）并重启后，所有设备自动登出</div>
          </div>
          <div class="gm-settings-content">
            <div v-for="s in data.sessions" :key="s.id" class="gm-session">
              <div class="gm-session-main">
                <div class="gm-session-device">
                  {{ s.device }}
                  <NTag v-if="s.is_current" size="small" type="info" :bordered="false">本机</NTag>
                </div>
                <div class="gm-session-meta">
                  IP {{ s.ip_address || "未知" }} · 登录于 {{ dateTime(s.created_at) }} · 最近活动 {{ dateTime(s.last_seen_at) }}
                </div>
              </div>
              <NButton text type="error" @click="revoke(s)">登出</NButton>
              <NButton v-if="s.ip_address && s.ip_address !== data.current_ip" text type="error" @click="ban(s.ip_address)">拉黑 IP</NButton>
            </div>
            <div v-if="!data.sessions.length" class="gm-settings-empty">没有登录中的设备</div>
            <div class="gm-settings-actions">
              <NButton secondary @click="revokeOthers">登出其他所有设备</NButton>
              <NButton quaternary @click="revokeAll">全部登出（含本机）</NButton>
            </div>
          </div>
        </section>

        <!-- IP 黑名单 -->
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            IP 黑名单：
            <div class="gm-settings-hint">被永久拉黑的 IP 访问任何页面都返回 403，该 IP 上已登录的设备立即登出。你当前的 IP：{{ data.current_ip }}</div>
          </div>
          <div class="gm-settings-content">
            <div v-for="b in data.banned_ips" :key="b.ip_address" class="gm-session">
              <div class="gm-session-main">
                <div class="gm-session-device"><code>{{ b.ip_address }}</code></div>
                <div class="gm-session-meta">
                  <template v-if="b.from_env">来自 .env 的 WEB_AUTH_BLOCKED_IPS，需修改配置并重启才能解除</template>
                  <template v-else>封禁于 {{ dateTime(b.since) }}</template>
                </div>
              </div>
              <NButton v-if="!b.from_env" text type="primary" @click="unban(b.ip_address)">解除</NButton>
            </div>
            <div v-if="!data.banned_ips.length" class="gm-settings-empty">没有被拉黑的 IP</div>
            <form class="gm-inline-form" @submit.prevent="ban(banIp.trim())">
              <NInput v-model:value="banIp" placeholder="要拉黑的 IP，如 203.0.113.5" />
              <NButton attr-type="submit" secondary :disabled="!banIp.trim()">拉黑</NButton>
            </form>
          </div>
        </section>

        <!-- 备份 -->
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            备份：
            <div class="gm-settings-hint">每天自动备份数据库和密钥。设置公钥后先加密再保存，并自动传到服务器以外的地方</div>
          </div>
          <div class="gm-settings-content">
            <div class="gm-settings-status gm-tags">
              <NTag v-if="data.backup.encrypted" type="success" :bordered="false">已加密</NTag>
              <NTag v-else type="warning" :bordered="false">未加密</NTag>
              <NTag v-for="t in data.backup.targets" :key="t" type="info" :bordered="false">异地：{{ TARGET_NAMES[t] || t }}</NTag>
              <NTag v-if="!data.backup.targets.length" type="warning" :bordered="false">未配置异地备份</NTag>
            </div>
            <p v-if="!data.backup.encrypted" class="gm-settings-hint">
              备份里有解密账号密码用的密钥。请在服务器执行
              <code>docker compose exec gmail-client python -m app.services.backup_crypto keygen</code>，
              私钥保存在自己电脑上，公钥写入 <code>.env</code> 的 <code>BACKUP_PUBLIC_KEY</code> 后重启。未加密的备份不会上传到异地。
            </p>
            <p v-if="data.backup.status">
              上次备份：{{ data.backup.status.at }}
              <template v-if="data.backup.status.ok">
                · {{ data.backup.status.file }}（{{ ((data.backup.status.size || 0) / 1048576).toFixed(1) }} MB）
                <template v-for="(err, t) in data.backup.status.uploads || {}" :key="t">
                  · {{ t }}
                  <NTag v-if="err" size="small" type="error" :bordered="false" :title="err">上传失败</NTag>
                  <NTag v-else size="small" type="success" :bordered="false">已上传</NTag>
                </template>
              </template>
              <template v-else>
                · <NTag size="small" type="error" :bordered="false">失败</NTag> {{ data.backup.status.error }}
              </template>
            </p>
            <NButton secondary @click="backupNow">立即备份</NButton>
          </div>
        </section>

        <!-- HTTPS 证书 -->
        <section v-if="cert" class="gm-settings-row">
          <div class="gm-settings-label">
            HTTPS 证书：
            <div class="gm-settings-hint">
              每天检测网站实际使用的证书，剩余 {{ cert.warn_days }} 天以内、已过期或无效时用 Telegram 提醒。证书由服务器上的 certbot 续订
            </div>
          </div>
          <div class="gm-settings-content">
            <template v-if="certStatus">
              <div class="gm-settings-status gm-tags">
                <NTag :type="CERT_LEVELS[certStatus.level]?.[0]" :bordered="false">{{ CERT_LEVELS[certStatus.level]?.[1] }}</NTag>
                <NTag v-if="certStatus.days_left != null" :type="certStatus.level === 'ok' ? 'default' : CERT_LEVELS[certStatus.level][0]" :bordered="false">
                  {{ certStatus.days_left >= 0 ? `剩余 ${certStatus.days_left} 天` : `已过期 ${-certStatus.days_left} 天` }}
                </NTag>
              </div>
              <p v-if="certStatus.not_after" class="gm-cert-info">
                域名 <code>{{ certStatus.domain }}</code> · 有效期至 {{ dateTime(certStatus.not_after) }} · 签发者 {{ certStatus.issuer }}
              </p>
              <p v-if="certStatus.verify_error" class="gm-cert-error">校验失败：{{ certStatus.verify_error }}</p>
              <p v-if="certStatus.error" class="gm-cert-error">{{ certStatus.error }}</p>
              <p class="gm-settings-hint">上次检测：{{ dateTime(certStatus.checked_at) }}</p>
            </template>
            <p v-else class="gm-settings-hint">还没有检测过{{ cert.domain ? `（域名 ${cert.domain}）` : "" }}</p>

            <div class="gm-cert-renew">
              <template v-if="renewal.pending">
                <NTag type="info" :bordered="false">续订中</NTag>
                已提交{{ renewal.pending.forced ? "强制" : "" }}续订请求（{{ dateTime(renewal.pending.requested_at) }}），等待服务器执行…
              </template>
              <template v-else-if="renewal.last_result">
                上次续订：{{ dateTime(renewal.last_result.finished_at) }}
                {{ renewal.last_result.trigger === "manual" ? "手动" : "自动" }}{{ renewal.last_result.forced ? "（强制）" : "" }}
                <NTag size="small" :type="RESULT_TYPES[renewal.last_result.status] || 'default'" :bordered="false">{{ renewal.last_result.label }}</NTag>
                <details class="gm-cert-log">
                  <summary>查看输出</summary>
                  <pre>{{ renewal.last_result.output }}</pre>
                </details>
              </template>
            </div>
            <p v-if="!renewal.agent.ok" class="gm-settings-hint">
              {{ renewal.agent.seen_at ? `服务器上的续订脚本自 ${dateTime(renewal.agent.seen_at)} 起没有运行` : "服务器上还没有安装续订脚本" }}，
              网页无法发起续订。请在服务器项目目录中以 root 执行 <code>sh tools/cert_renew.sh install</code>（只需一次，之后每天也会自动续订）。
            </p>
            <div class="gm-settings-actions">
              <NButton secondary :loading="certChecking" @click="checkCert">立即检测</NButton>
              <NButton :disabled="!renewal.agent.ok || !!renewal.pending" @click="renewCert">立即续订</NButton>
            </div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-settings-page { overflow-y: auto; background: var(--gm-bg); }
.gm-settings-loading { display: flex; justify-content: center; padding: 48px 0; }
.gm-settings-status { margin-bottom: 8px; }
.gm-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.gm-inline-form { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; max-width: 520px; margin: 8px 0; }
.gm-inline-form :deep(.n-input) { flex: 1; min-width: 180px; }
.gm-2fa-steps { padding-left: 20px; line-height: 1.7; }
.gm-2fa-secret { margin: 6px 0; font-size: 16px; letter-spacing: 1px; }
.gm-session { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; padding: 10px 0; border-bottom: 1px solid var(--gm-divider); }
.gm-session-main { flex: 1; min-width: 200px; }
.gm-session-device { display: flex; align-items: center; gap: 8px; font-weight: 500; }
.gm-session-meta { color: var(--gm-text-3); font-size: 12px; margin-top: 2px; }
.gm-settings-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.gm-cert-info { word-break: break-all; }
.gm-cert-error { color: var(--gm-error); word-break: break-all; }
.gm-cert-renew { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 8px; }
.gm-cert-log { width: 100%; }
.gm-cert-log summary { cursor: pointer; color: var(--gm-text-3); font-size: 12px; }
.gm-cert-log pre {
    max-height: 240px;
    overflow: auto;
    margin: 6px 0 0;
    padding: 8px 10px;
    border-radius: var(--gm-radius);
    background: var(--gm-surface-2);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
}
@media (max-width: 768px) {
    .gm-settings-page { border-radius: 0; }
}
</style>
