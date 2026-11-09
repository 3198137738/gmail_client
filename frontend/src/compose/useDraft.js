// 草稿：停止输入 2 秒后自动保存；关闭窗口、离开页面时立即保存。
// 附件随草稿保存：添加时立即上传到草稿（显示进度），关闭窗口后再打开草稿仍在；发送时服务端从草稿取附件。
import { ref } from "vue";
import { post, upload } from "../api";
import { refreshNav } from "../composables/common";

let seq = 0;

export function useDraft({ entry, form, prefill, bodyFields, addressFields, canSave }) {
  const status = ref("");
  // [{ key, id, name, size, progress (0..1), state: uploading / done / error, error, abort }]
  const attachments = ref([]);
  let dirty = false;      // 填写过内容（舍弃时需要确认）
  let unsaved = false;    // 有尚未保存到草稿的修改
  let timer = null;
  let saving = null;

  function markDirty() {
    dirty = true;
    unsaved = true;
    entry.busy = true;
    clearTimeout(timer);
    timer = setTimeout(() => save(), 2000);
  }

  function save({ keepalive = false, keepEmpty = false } = {}) {
    clearTimeout(timer);
    if ((!unsaved && !keepEmpty) || !canSave() || !form.accountId) return saving || Promise.resolve(true);
    // 上一次保存还没回来时排在它后面，保证用的是最新的草稿 id
    const previous = saving || Promise.resolve(true);
    const mine = previous.then(async () => {
      unsaved = false;
      status.value = "正在保存…";
      const p = prefill.value;
      try {
        const result = await post(
          "/api/drafts",
          {
            draft_id: form.draftId,
            account_id: form.accountId,
            subject: form.subject,
            in_reply_to: p.in_reply_to || null,
            references: p.references || null,
            forward_message_id: p.forward_message_id || null,
            keep_empty: keepEmpty || attachments.value.length > 0,
            ...addressFields(),
            ...bodyFields(),
          },
          { keepalive },
        );
        form.draftId = result.id || null;
        status.value = result.id ? `草稿已保存 ${result.saved_at}` : "";
        if (!unsaved) entry.busy = false;
        refreshNav();
        return true;
      } catch {
        unsaved = true;
        status.value = "草稿保存失败";
        return false;
      }
    });
    saving = mine;
    mine.then(() => {
      if (saving === mine) saving = null;
    });
    return mine;
  }

  // ---- 附件 ----
  function restoreAttachments(list) {
    attachments.value = (list || []).map((a) => ({ key: ++seq, id: a.id, name: a.name, size: a.size, progress: 1, state: "done" }));
  }

  async function ensureDraft() {
    if (form.draftId) return form.draftId;
    unsaved = true;
    await save({ keepEmpty: true });
    return form.draftId;
  }

  async function uploadOne(item, file) {
    try {
      const draftId = await ensureDraft();
      if (!draftId) throw new Error("草稿保存失败，无法添加附件");
      const data = new FormData();
      data.append("file", file, file.name);
      const job = upload(`/api/drafts/${draftId}/attachments`, data, (p) => (item.progress = p));
      item.abort = job.abort;
      const result = await job.promise;
      Object.assign(item, { id: result.id, size: result.size, progress: 1, state: "done", abort: null });
      refreshNav();
    } catch (err) {
      if (err.name === "AbortError") return;
      Object.assign(item, { state: "error", error: err.message || "上传失败", abort: null });
    }
  }

  function addFiles(list) {
    const files = Array.from(list || []);
    if (!files.length) return;
    dirty = true;
    for (const file of files) {
      const item = { key: ++seq, id: null, name: file.name, size: file.size, progress: 0, state: "uploading", error: "", abort: null, file };
      attachments.value.push(item);
      // 先取到 reactive 代理再上传，进度才会更新到界面
      uploadOne(attachments.value[attachments.value.length - 1], file);
    }
  }

  async function removeAttachment(item) {
    attachments.value = attachments.value.filter((a) => a !== item);
    if (item.state === "uploading") item.abort?.();
    if (item.id && form.draftId) {
      try {
        await post(`/api/drafts/${form.draftId}/attachments/${item.id}/delete`);
      } catch {
        /* 草稿删除时会一起清理 */
      }
    }
  }

  function retryAttachment(item) {
    if (!item.file) return;
    Object.assign(item, { state: "uploading", progress: 0, error: "" });
    uploadOne(item, item.file);
  }

  // 发送前：等草稿保存和附件上传都完成
  async function settle() {
    clearTimeout(timer);
    while (attachments.value.some((a) => a.state === "uploading")) {
      await new Promise((r) => setTimeout(r, 200));
    }
    if (unsaved || (attachments.value.length && !form.draftId)) await save({ keepEmpty: attachments.value.length > 0 });
    await (saving || Promise.resolve());
  }

  function stop() {
    clearTimeout(timer);
  }

  return {
    status,
    attachments,
    markDirty,
    save,
    settle,
    stop,
    addFiles,
    removeAttachment,
    retryAttachment,
    restoreAttachments,
    isDirty: () => dirty,
    isUnsaved: () => unsaved,
    clearUnsaved: () => {
      unsaved = false;
    },
  };
}
