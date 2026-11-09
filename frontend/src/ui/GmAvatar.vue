<script setup>
// 圆形头像：账号用账号颜色，其他人按名字取颜色；显示名字首字。
import { computed } from "vue";
import { avatarColor, initial } from "../format";

const props = defineProps({
  name: { type: String, default: "" },
  color: { type: String, default: "" },
  size: { type: Number, default: 32 },
});

const style = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  fontSize: `${Math.round(props.size * 0.44)}px`,
  background: props.color || avatarColor(props.name),
}));
</script>

<template>
  <span class="gm-avatar-x" :style="style" aria-hidden="true">
    <slot>{{ initial(name) }}</slot>
  </span>
</template>

<style scoped>
.gm-avatar-x {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    border-radius: 50%;
    color: #fff;
    font-weight: 600;
    line-height: 1;
    user-select: none;
}
</style>
