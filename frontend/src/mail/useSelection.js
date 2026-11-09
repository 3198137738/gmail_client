// 列表勾选：单选、Shift 区间选择、按已读 / 未读选择；列表刷新后只保留仍在列表中的邮件。
import { computed, reactive, ref, watch } from "vue";

export function useSelection(items) {
  const selected = reactive(new Set());
  const anchor = ref(null);   // 上一次点选的邮件，Shift 点选时从这里选到当前

  const count = computed(() => selected.size);
  const active = computed(() => selected.size > 0);
  const allChecked = computed(() => items.value.length > 0 && items.value.every((m) => selected.has(m.id)));
  const someChecked = computed(() => selected.size > 0 && !allChecked.value);
  const selectedItems = computed(() => items.value.filter((m) => selected.has(m.id)));

  watch(items, (list) => {
    const ids = new Set(list.map((m) => m.id));
    for (const id of [...selected]) if (!ids.has(id)) selected.delete(id);
  });

  function toggle(id, { range = false } = {}) {
    if (range && anchor.value !== null) {
      const ids = items.value.map((m) => m.id);
      const a = ids.indexOf(anchor.value);
      const b = ids.indexOf(id);
      if (a !== -1 && b !== -1) {
        const turnOn = !selected.has(id);
        for (const x of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) {
          if (turnOn) selected.add(x);
          else selected.delete(x);
        }
        anchor.value = id;
        return;
      }
    }
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    anchor.value = id;
  }

  function selectBy(mode) {
    selected.clear();
    for (const m of items.value) {
      const unread = !m.is_read || m.thread_unread > 0;
      if (mode === "all" || (mode === "unread" && unread) || (mode === "read" && !unread) || (mode === "starred" && m.is_starred)) {
        selected.add(m.id);
      }
    }
  }

  function clear() {
    selected.clear();
    anchor.value = null;
  }

  return { selected, count, active, allChecked, someChecked, selectedItems, toggle, selectBy, clear };
}
