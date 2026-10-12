<script setup>
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NAlert, NButton, NForm, NFormItem, NInput } from "naive-ui";
import AuthCard from "../components/AuthCard.vue";
import { post } from "../api";
import { store } from "../store";
import { safeNext } from "../router";

const route = useRoute();
const router = useRouter();

const password = ref("");
const error = ref("");
const loading = ref(false);
const blocked = ref(!!store.session?.blocked);
const blockedText = computed(() =>
  store.session?.permanent_blocked ? "当前 IP 已被永久封禁" : "当前 IP 已被临时封禁",
);

async function submit() {
  if (!password.value || loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const result = await post("/api/login", { password: password.value });
    store.session.csrf_token = result.csrf_token;
    if (result.two_factor) {
      store.session.two_factor_pending = true;
      router.replace({ name: "login2fa", query: route.query });
      return;
    }
    store.session.authenticated = true;
    router.replace(safeNext(route.query.next));
  } catch (err) {
    error.value = err.message;
    if (err.data?.blocked) blocked.value = true;
    password.value = "";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <AuthCard title="访问验证" subtitle="输入访问密码以继续使用 Roundcube">
    <NForm @submit.prevent="submit">
      <NAlert v-if="blocked" type="error" :show-icon="true" class="gm-auth-alert">{{ blockedText }}</NAlert>
      <NFormItem label="访问密码" :feedback="error" :validation-status="error ? 'error' : undefined">
        <NInput
          v-model:value="password"
          type="password"
          show-password-on="click"
          size="large"
          placeholder="请输入访问密码"
          :disabled="blocked"
          :input-props="{ autocomplete: 'current-password', autofocus: true }"
          @keydown.enter.prevent="submit"
        />
      </NFormItem>
      <div class="gm-auth-meta">当前访问 IP：{{ store.session?.client_ip }}</div>
      <div class="gm-auth-actions">
        <NButton type="primary" block attr-type="submit" :loading="loading" :disabled="blocked || !password">登录</NButton>
      </div>
    </NForm>
  </AuthCard>
</template>
