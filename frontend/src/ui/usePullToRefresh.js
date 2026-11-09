// 手机端下拉刷新：列表滚到顶部时向下拉超过 64px 松手触发 onRefresh（返回 Promise）。
import { computed, ref } from "vue";

const THRESHOLD = 64;

export function usePullToRefresh({ el, enabled, onRefresh }) {
  const pull = ref(0);
  const refreshing = ref(false);
  let startY = 0;
  let tracking = false;

  function onTouchStart(ev) {
    if (!enabled() || refreshing.value || (el.value?.scrollTop || 0) > 0 || ev.touches.length !== 1) return;
    startY = ev.touches[0].clientY;
    tracking = true;
  }

  function onTouchMove(ev) {
    if (!tracking) return;
    const dy = ev.touches[0].clientY - startY;
    if (dy <= 0 || (el.value?.scrollTop || 0) > 0) {
      pull.value = 0;
      return;
    }
    pull.value = Math.min(120, dy * 0.5);   // 阻尼
  }

  async function onTouchEnd() {
    if (!tracking) return;
    tracking = false;
    if (pull.value < THRESHOLD) {
      pull.value = 0;
      return;
    }
    refreshing.value = true;
    pull.value = THRESHOLD * 0.75;
    try {
      await onRefresh();
    } finally {
      refreshing.value = false;
      pull.value = 0;
    }
  }

  const ready = computed(() => pull.value >= THRESHOLD);

  return {
    pull,
    ready,
    refreshing,
    handlers: { touchstart: onTouchStart, touchmove: onTouchMove, touchend: onTouchEnd, touchcancel: onTouchEnd },
  };
}
