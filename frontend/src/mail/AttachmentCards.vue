<script setup>
// 邮件中的附件卡片：图片显示缩略图，其余显示类型图标；点击下载。内嵌图片排在后面。
import { computed } from "vue";
import { NIcon } from "naive-ui";
import { DownloadOutlined } from "@vicons/material";
import FileIcon from "../ui/FileIcon.vue";
import { attachmentKind, fileSize } from "../format";

const props = defineProps({
  attachments: { type: Array, required: true },
  // 折叠的邮件不加载缩略图
  active: { type: Boolean, default: true },
});

const files = computed(() => props.attachments.filter((a) => !a.is_inline));
const inline = computed(() => props.attachments.filter((a) => a.is_inline));
</script>

<template>
  <div class="gm-ac">
    <div class="gm-ac-title">
      <template v-if="files.length">{{ files.length }} 个附件</template>
      <template v-if="files.length && inline.length"> · </template>
      <template v-if="inline.length">{{ inline.length }} 张内嵌图片</template>
    </div>
    <div class="gm-ac-cards">
      <a v-for="a in [...files, ...inline]" :key="a.id" class="gm-ac-card" :href="a.download_url" download :title="`下载 ${a.name}`">
        <span class="gm-ac-preview">
          <img v-if="attachmentKind(a.mime_type) === 'image' && active" :src="a.url" alt="" loading="lazy" />
          <FileIcon v-else :mime="a.mime_type" :size="36" />
        </span>
        <span class="gm-ac-bar">
          <span class="gm-ac-name">{{ a.name }}</span>
          <span class="gm-ac-size">{{ fileSize(a.size) }}</span>
        </span>
        <span class="gm-ac-hover"><NIcon :component="DownloadOutlined" :size="22" /></span>
      </a>
    </div>
  </div>
</template>

<style scoped>
.gm-ac { margin-top: var(--sp-4); padding-top: var(--sp-4); border-top: 1px solid var(--gm-divider); }
.gm-ac-title { margin-bottom: var(--sp-3); font-size: var(--fs-sm); font-weight: 600; color: var(--gm-text-2); }
.gm-ac-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: var(--sp-3); }
.gm-ac-card {
    position: relative;
    display: flex;
    flex-direction: column;
    height: 120px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-md);
    overflow: hidden;
    color: var(--gm-text);
    background: var(--gm-surface-2);
}
.gm-ac-preview { flex: 1; display: flex; align-items: center; justify-content: center; overflow: hidden; }
.gm-ac-preview img { width: 100%; height: 100%; object-fit: cover; }
.gm-ac-bar { display: flex; flex-direction: column; padding: 6px 10px; background: var(--gm-surface); border-top: 1px solid var(--gm-divider); }
.gm-ac-name { font-size: var(--fs-sm); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-ac-size { font-size: var(--fs-xs); color: var(--gm-text-3); }
.gm-ac-hover {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, .45);
    color: #fff;
    opacity: 0;
    transition: opacity var(--dur-fast) var(--ease);
}
.gm-ac-card:hover .gm-ac-hover, .gm-ac-card:focus-visible .gm-ac-hover { opacity: 1; }
</style>
