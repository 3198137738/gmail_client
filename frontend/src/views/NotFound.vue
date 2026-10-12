<script setup>
import { NButton, NResult } from "naive-ui";
</script>

<template>
  <div class="gm-card gm-notfound">
    <NResult status="404" title="找不到您要访问的页面" description="链接可能有误，或者页面已被移除。">
      <template #footer>
        <RouterLink to="/messages/" custom v-slot="{ navigate }">
          <NButton type="primary" @click="navigate">返回收件箱</NButton>
        </RouterLink>
      </template>
    </NResult>
  </div>
</template>

<style scoped>
.gm-notfound { display: flex; align-items: center; justify-content: center; }
</style>
