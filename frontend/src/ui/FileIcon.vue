<script>
// 文件类型图标（按 MIME 类型）：列表附件、附件卡片、附件中心共用这一份。
import {
  DescriptionOutlined,
  FolderZipOutlined,
  ImageOutlined,
  InsertDriveFileOutlined,
  PictureAsPdfOutlined,
  TableChartOutlined,
} from "@vicons/material";

export const FILE_ICONS = {
  pdf: [PictureAsPdfOutlined, "#d93025"],
  image: [ImageOutlined, "#d93025"],
  sheet: [TableChartOutlined, "#188038"],
  zip: [FolderZipOutlined, "#5f6368"],
  doc: [DescriptionOutlined, "#1a73e8"],
  other: [InsertDriveFileOutlined, "#5f6368"],
};
</script>

<script setup>
import { computed } from "vue";
import { NIcon } from "naive-ui";
import { attachmentKind } from "../format";

const props = defineProps({
  mime: { type: String, default: "" },
  size: { type: Number, default: 16 },
});
const entry = computed(() => FILE_ICONS[attachmentKind(props.mime)] || FILE_ICONS.other);
</script>

<template>
  <NIcon :component="entry[0]" :color="entry[1]" :size="size" />
</template>
