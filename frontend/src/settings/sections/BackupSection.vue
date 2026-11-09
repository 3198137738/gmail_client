<script setup>
// 备份与证书：每日备份状态（加密、异地上传）与 HTTPS 证书检测 / 续订。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { NButton, NSpin, NTag } from "naive-ui";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { confirm, run } from "../../ui";
import { dateTime } from "../../format";

const data = ref(null);
const loadError = ref("");
async function load() {
  try {
    data.value = await get("/api/security");
    loadError.value = "";
  } catch (err) {
    loadError.value = err.message || "加载失败";
  }
}
onMounted(load);

const backingUp = ref(false);
async function backupNow() {
  backingUp.value = true;
  const result = await run(post("/api/security/backup"), (r) => r.message);
  backingUp.value = false;
  if (result) await load();
}

const TARGET_NAMES = { telegram: "Telegram", webdav: "WebDAV" };

// ---- HTTPS 证书 ----
const cert = ref(null);
const certError = ref("");
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
  let result = null;
  try {
    result = await get("/api/cert");
    cert.value = result;
    certError.value = "";
  } catch (err) {
    certError.value = err.message || "加载失败";
  }
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
  <div class="gm-set-page">
    <div class="gm-settings">
      <GmEmptyState v-if="loadError && !data" tone="error" title="加载失败" :hint="loadError" action-text="重试" @action="load" />
      <NSpin v-else-if="!data" class="gm-settings-loading" />
      <!-- 备份 -->
      <section v-else class="gm-settings-row">
        <div class="gm-settings-label">
          备份
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
          <NButton secondary :loading="backingUp" @click="backupNow">立即备份</NButton>
        </div>
      </section>

      <!-- HTTPS 证书 -->
      <GmEmptyState v-if="certError && !cert" tone="error" title="证书状态加载失败" :hint="certError" action-text="重试" @action="loadCert" />
      <section v-else-if="cert" class="gm-settings-row">
        <div class="gm-settings-label">
          HTTPS 证书
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
    </div>
  </div>
</template>

<style scoped>
.gm-settings-loading { display: flex; justify-content: center; padding: 48px 0; }
.gm-settings-status { margin-bottom: 8px; }
.gm-tags { display: flex; flex-wrap: wrap; gap: 6px; }
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
    border-radius: var(--r-md);
    background: var(--gm-surface-2);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
}
</style>
