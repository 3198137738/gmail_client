<script setup>
// 账号标记（账号颜色的小标签）：只在有多个账号时显示
import { computed } from "vue";
import { navAccount, store } from "../store";

const props = defineProps({ accountId: { type: Number, default: null } });
const account = computed(() => ((store.nav?.accounts.length || 0) > 1 ? navAccount(props.accountId) : null));
</script>

<template>
  <span v-if="account" class="gm-acc-chip" :style="{ '--acc': account.color }" :title="account.email">{{ account.name }}</span>
</template>

<style scoped>
.gm-acc-chip {
    flex: none;
    max-width: 130px;
    padding: 0 6px;
    border-radius: var(--r-sm);
    background: color-mix(in srgb, var(--acc) 12%, transparent);
    color: var(--acc);
    font-size: 11px;
    font-weight: 500;
    line-height: 18px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
</style>
