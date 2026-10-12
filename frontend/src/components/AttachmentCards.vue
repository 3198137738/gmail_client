<script setup>
// 邮件详情中的附件卡片：图片显示缩略图，其余显示类型图标；点击下载。
import { computed } from "vue";
import { NIcon } from "naive-ui";
import {
  DescriptionOutlined,
  DownloadOutlined,
  FolderZipOutlined,
  PictureAsPdfOutlined,
  TableChartOutlined,
} from "@vicons/material";
import { attachmentKind, fileSize } from "../format";

const props = defineProps({
  attachments: { type: Array, required: true },
  // 折叠的邮件不加载缩略图
  active: { type: Boolean, default: true },
});

const files = computed(() => props.attachments.filter((a) => !a.is_inline));
const inline = computed(() => props.attachments.filter((a) => a.is_inline));
const ICONS = {
  pdf: [PictureAsPdfOutlined, "#d93025"],
  sheet: [TableChartOutlined, "#188038"],
  zip: [FolderZipOutlined, "#5f6368"],
  doc: [DescriptionOutlined, "#1a73e8"],
};
</script>

<template>
  <div class="gm-msg-atts">
    <div class="gm-msg-atts-title">
      <template v-if="files.length">{{ files.length }} 个附件</template>
      <template v-if="files.length && inline.length"> · </template>
      <template v-if="inline.length">{{ inline.length }} 张内嵌图片</template>
    </div>
    <div class="gm-att-cards">
      <a
        v-for="a in [...files, ...inline]"
        :key="a.id"
        class="gm-att-card"
        :href="a.download_url"
        download
        :title="`下载 ${a.name}`"
      >
        <span class="gm-att-card-preview">
          <img v-if="attachmentKind(a.mime_type) === 'image' && active" :src="a.url" alt="" loading="lazy" />
          <NIcon
            v-else-if="attachmentKind(a.mime_type) !== 'image'"
            :component="ICONS[attachmentKind(a.mime_type)][0]"
            :color="ICONS[attachmentKind(a.mime_type)][1]"
            :size="40"
          />
        </span>
        <span class="gm-att-card-bar">
          <span class="gm-att-card-name">{{ a.name }}</span>
          <span class="gm-att-card-size">{{ fileSize(a.size) }}</span>
        </span>
        <span class="gm-att-card-hover"><NIcon :component="DownloadOutlined" :size="24" /></span>
      </a>
    </div>
  </div>
</template>

<style scoped>
.gm-msg-atts { margin: 16px 0 8px; padding-top: 16px; border-top: 1px solid var(--gm-divider); }
.gm-msg-atts-title { font: 500 14px var(--gm-font); color: var(--gm-text-2); margin-bottom: 12px; }
.gm-att-cards { display: flex; flex-wrap: wrap; gap: 12px; }
.gm-att-card {
    position: relative;
    display: flex;
    flex-direction: column;
    width: 180px;
    height: 120px;
    border: 1px solid var(--gm-outline-variant);
    border-radius: 8px;
    overflow: hidden;
    color: var(--gm-text);
    text-decoration: none !important;
    background: #f8fafd;
}
.gm-att-card-preview { flex: 1; display: flex; align-items: center; justify-content: center; overflow: hidden; }
.gm-att-card-preview img { width: 100%; height: 100%; object-fit: cover; }
.gm-att-card-bar { display: flex; flex-direction: column; padding: 6px 10px; background: #fff; border-top: 1px solid var(--gm-divider); }
.gm-att-card-name { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-att-card-size { font-size: 12px; color: var(--gm-text-3); }
.gm-att-card-hover {
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, .45);
    color: #fff;
}
.gm-att-card:hover .gm-att-card-hover { display: flex; }

@media (max-width: 768px) {
    .gm-att-card { width: calc(50% - 6px); }
}
</style>
