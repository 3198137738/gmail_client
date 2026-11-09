// 写邮件表单：加载预填内容（回复 / 转发 / 草稿）、发件账号、联系人；签名；发件人姓名提示。
import { computed, nextTick, reactive, ref, watch } from "vue";
import { get, post } from "../api";
import { formatAddress, parseAddressList } from "../address";
import { toast } from "../ui";
import { refreshNav } from "../composables/common";

export function useComposeForm(params, { editor, onChange }) {
  const loading = ref(true);
  const loadError = ref("");
  const accounts = ref([]);
  const contacts = ref([]);
  const prefill = ref({});
  const form = reactive({
    accountId: null,
    to: [],
    cc: [],
    bcc: [],
    subject: "",
    plain: false,
    plainText: "",
    draftId: null,
  });
  const showCc = ref(false);
  const showBcc = ref(false);

  const defaultTitle = computed(() => {
    const p = prefill.value;
    if (p.is_forward) return "转发";
    if (p.reply_all) return "回复全部";
    if (p.is_reply) return "回复";
    return "新邮件";
  });
  const title = computed(() => form.subject.trim() || defaultTitle.value);
  const accountOptions = computed(() =>
    accounts.value.map((a) => ({ value: a.id, label: a.display_name ? `${a.display_name} <${a.email}>` : a.email })),
  );
  const currentAccount = computed(() => accounts.value.find((a) => a.id === form.accountId) || null);

  async function load() {
    try {
      const query = {};
      for (const [key, value] of Object.entries(params)) if (value) query[key] = value;
      const data = await get("/api/compose", query);
      accounts.value = data.accounts;
      contacts.value = data.contacts;
      prefill.value = data.prefill || {};
      const p = prefill.value;
      const ids = data.accounts.map((a) => a.id);
      form.accountId = ids.includes(p.account_id) ? p.account_id : ids[0] ?? null;
      form.to = parseAddressList(p.to);
      form.cc = parseAddressList(p.cc);
      form.bcc = parseAddressList(p.bcc);
      form.subject = p.subject || "";
      form.draftId = p.draft_id || null;
      showCc.value = form.cc.length > 0;
      showBcc.value = form.bcc.length > 0;
    } catch (err) {
      loadError.value = err.message || "加载失败";
    } finally {
      loading.value = false;
    }
    await nextTick();
    // 草稿里已经带着签名，不再重复添加
    if (!prefill.value.draft_id) applySignature(form.accountId, { initial: true });
  }

  // ---- 签名 ----
  // 签名放在 class="gm-signature" 的块里：新邮件在正文末尾，回复 / 转发在引用原文上方（与 Gmail 一致）；
  // 切换发件账号时替换为新账号的签名，新账号没有签名就去掉。
  function applySignature(accountId, { initial = false } = {}) {
    const el = editor.value?.editorEl();
    if (!el) return;
    const html = accounts.value.find((a) => a.id === accountId)?.signature_html || "";
    let block = el.querySelector(".gm-signature");
    if (!html) {
      if (block) {
        block.remove();
        editor.value.changed();
      }
      return;
    }
    if (!block) {
      block = document.createElement("div");
      block.className = "gm-signature";
      const quote = el.querySelector(".gmail_quote");
      if (quote) {
        el.insertBefore(block, quote);
      } else {
        // 新邮件：签名上方留出空行供输入
        if (!el.textContent.trim()) el.innerHTML = "<div><br></div><div><br></div>";
        el.appendChild(block);
      }
    }
    block.innerHTML = `<div>-- </div>${html}`;
    if (!initial) editor.value.changed();
  }

  function onAccountChange(accountId) {
    if (!form.plain) applySignature(accountId);
    onChange();
  }

  // ---- 发件人姓名 ----
  // 账号没设置显示名称时，发出的邮件只带邮箱地址；从邮箱读取姓名作为建议，一键设为该账号的显示名称。
  const fetchedNames = reactive({});
  const savingName = ref(false);
  const suggestedName = computed(() => (currentAccount.value ? fetchedNames[currentAccount.value.id] : undefined));

  watch(currentAccount, async (a) => {
    if (!a || a.display_name || a.id in fetchedNames) return;
    fetchedNames[a.id] = "";
    try {
      fetchedNames[a.id] = (await get(`/api/accounts/${a.id}/gmail-name`)).name || "";
    } catch {
      // 取不到只是不给建议，不影响写邮件
    }
  });

  async function useSuggestedName() {
    const a = currentAccount.value;
    const name = suggestedName.value;
    if (!a || !name) return;
    savingName.value = true;
    try {
      await post(`/api/accounts/${a.id}/update`, { display_name: name });
      a.display_name = name;
      toast(`已将发件人姓名设为「${name}」`);
      refreshNav();
    } catch (err) {
      toast(err.message || "设置失败", { tone: "error" });
    } finally {
      savingName.value = false;
    }
  }

  function bodyFields() {
    if (form.plain) return { body_text: form.plainText, body_html: "" };
    return { body_text: editor.value?.getText() || "", body_html: editor.value?.getHtml() || "" };
  }

  function addressFields() {
    return {
      to: form.to.map(formatAddress).join(", "),
      cc: form.cc.map(formatAddress).join(", "),
      bcc: form.bcc.map(formatAddress).join(", "),
    };
  }

  return {
    loading,
    loadError,
    accounts,
    contacts,
    prefill,
    form,
    showCc,
    showBcc,
    title,
    accountOptions,
    currentAccount,
    suggestedName,
    savingName,
    useSuggestedName,
    load,
    applySignature,
    onAccountChange,
    bodyFields,
    addressFields,
  };
}
