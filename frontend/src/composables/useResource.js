// 页面数据加载：{ data, loading, error, reload }。加载失败时 error 为错误文字（页面显示「重试」），
// 不会一直转圈；重新加载时保留上一次的数据（silent 时不显示加载状态）。
import { ref } from "vue";

export function useResource(fetcher, { immediate = true } = {}) {
  const data = ref(null);
  const loading = ref(false);
  const error = ref("");
  let seq = 0;

  async function reload({ silent = false } = {}) {
    const mine = ++seq;
    if (!silent) loading.value = true;
    try {
      const result = await fetcher();
      if (mine !== seq) return data.value;   // 已有更新的请求
      data.value = result;
      error.value = "";
    } catch (err) {
      if (mine !== seq || err?.name === "AbortError") return data.value;
      if (err?.status !== 401) error.value = err?.message || "加载失败";
    } finally {
      if (mine === seq) loading.value = false;
    }
    return data.value;
  }

  if (immediate) reload();
  return { data, loading, error, reload };
}
