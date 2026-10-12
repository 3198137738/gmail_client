<script setup>
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NForm, NFormItem, NInput } from "naive-ui";
import AuthCard from "../components/AuthCard.vue";
import { post } from "../api";
import { store } from "../store";
import { safeNext } from "../router";
import { ui } from "../ui";

const route = useRoute();
const router = useRouter();

const code = ref("");
const error = ref("");
const loading = ref(false);

async function submit() {
  if (!code.value.trim() || loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const result = await post("/api/login/2fa", { code: code.value });
    store.session.csrf_token = result.csrf_token;
    store.session.two_factor_pending = false;
    store.session.authenticated = true;
    router.replace(safeNext(route.query.next));
  } catch (err) {
    if (err.data?.expired) {
      // 超时或被封禁：需要重新输入密码
      store.session.two_factor_pending = false;
      ui.message.error(err.message);
      router.replace({ name: "login", query: route.query });
      return;
    }
    error.value = err.message;
    code.value = "";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <AuthCard title="两步验证" subtitle="打开手机上的认证器 App，输入 Roundcube 的 6 位验证码">
    <NForm @submit.prevent="submit">
      <NFormItem label="验证码" :feedback="error" :validation-status="error ? 'error' : undefined">
        <NInput
          v-model:value="code"
          size="large"
          placeholder="6 位验证码"
          :maxlength="7"
          :input-props="{ inputmode: 'numeric', autocomplete: 'one-time-code', autofocus: true }"
          @keydown.enter.prevent="submit"
        />
      </NFormItem>
      <div class="gm-auth-actions">
        <NButton quaternary @click="router.replace({ name: 'login', query: route.query })">返回</NButton>
        <NButton type="primary" attr-type="submit" :loading="loading" :disabled="!code.trim()">验证</NButton>
      </div>
    </NForm>
  </AuthCard>
</template>
