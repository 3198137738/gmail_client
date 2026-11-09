// 发送：校验收件人、撤销发送（先隐藏窗口并提示「撤销」，几秒后才真正发送）、定时发送。
// 每个写信窗口各自独立，提示里带上主题以便区分。
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import { isValidEmail } from "../address";
import { closeCompose, store } from "../store";
import { toast } from "../ui";
import { refreshNav } from "../composables/common";
import { futureDate } from "../format";

export function useSend({ entry, form, prefill, draft, addressFields, bodyFields, inputs, error }) {
  const router = useRouter();
  const phase = ref("ready");   // ready / pending（撤销等待期）/ sending
  const undoSeconds = computed(() => store.session?.config?.undo_send_seconds || 0);
  let pendingTimer = null;
  let closeToast = null;

  const subjectLabel = () => (form.subject.trim() ? `「${form.subject.trim().slice(0, 30)}」` : "");

  function setPhase(value) {
    phase.value = value;
    entry.pending = value !== "ready";
  }

  function validate() {
    inputs.forEach((r) => r.value?.commit());
    if (!form.to.length && !form.cc.length && !form.bcc.length) {
      error.value = "没有填写收件人";
      inputs[0].value?.focus();
      return false;
    }
    if ([...form.to, ...form.cc, ...form.bcc].some((a) => !isValidEmail(a.email))) {
      error.value = "邮箱地址无效";
      return false;
    }
    if (draft.attachments.value.some((a) => a.state === "error")) {
      error.value = "有附件上传失败，请重试或移除后再发送";
      return false;
    }
    error.value = "";
    return true;
  }

  // 附件都在草稿里，表单只带草稿 id
  function buildFormData() {
    const p = prefill.value;
    const data = new FormData();
    const fields = {
      account_id: form.accountId,
      subject: form.subject,
      in_reply_to: p.in_reply_to || "",
      references: p.references || "",
      forward_message_id: p.forward_message_id || "",
      draft_id: form.draftId || "",
      ...addressFields(),
      ...bodyFields(),
    };
    for (const [key, value] of Object.entries(fields)) data.append(key, value ?? "");
    return data;
  }

  function send() {
    if (phase.value !== "ready" || !validate()) return;
    draft.stop();
    if (undoSeconds.value > 0) {
      setPhase("pending");
      pendingTimer = setTimeout(commitSend, undoSeconds.value * 1000);
      closeToast = toast(`正在发送${subjectLabel()}…（${undoSeconds.value} 秒内可撤销）`, {
        actionText: "撤销",
        onAction: undoSend,
        duration: undoSeconds.value * 1000,
      });
      return;
    }
    commitSend();
  }

  function undoSend() {
    if (phase.value !== "pending") return;
    clearTimeout(pendingTimer);
    setPhase("ready");
    entry.state = "normal";
    store.composeActive = entry.key;
    if (draft.isUnsaved()) draft.save();
  }

  async function commitSend() {
    clearTimeout(pendingTimer);
    closeToast?.();
    setPhase("sending");
    const sendingToast = toast(`正在发送${subjectLabel()}…`, { duration: 0 });
    try {
      // 等草稿保存、附件上传完成（服务端从草稿取附件，发送成功后删除草稿）
      await draft.settle();
      const result = await api("/api/compose/send", { method: "POST", form: buildFormData() });
      sendingToast();
      draft.clearUnsaved();
      closeCompose(entry.key);
      refreshNav();
      toast(result.status === "partial" ? "邮件已发送，但部分收件人被拒收" : `邮件${subjectLabel()}已发送`, {
        linkText: "查看",
        onLink: () => router.push(`/compose/sent/${result.sent_id}`),
        duration: 6000,
      });
    } catch (err) {
      sendingToast();
      setPhase("ready");
      entry.state = "normal";
      error.value = err.message || "发送失败";
    }
  }

  // ---- 定时发送 ----
  const scheduleOpen = ref(false);

  function openSchedule() {
    if (phase.value !== "ready" || !validate()) return;
    scheduleOpen.value = true;
  }

  async function scheduleAt(ms) {
    draft.stop();
    setPhase("sending");
    try {
      await draft.settle();
      const data = buildFormData();
      data.append("send_at_ms", String(ms));
      await api("/api/compose/schedule", { method: "POST", form: data });
      draft.clearUnsaved();
      closeCompose(entry.key);
      refreshNav();
      toast(`已安排在 ${futureDate(new Date(ms))} 发送`, {
        linkText: "查看",
        onLink: () => router.push("/compose/scheduled"),
        duration: 6000,
      });
    } catch (err) {
      setPhase("ready");
      error.value = err.message || "安排失败";
    }
  }

  function dispose() {
    // 等待发送期间窗口被关闭（如退出登录）：不再发送
    clearTimeout(pendingTimer);
  }

  return { phase, send, undoSend, scheduleOpen, openSchedule, scheduleAt, dispose };
}
