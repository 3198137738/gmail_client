// 触屏手势：左右滑动触发动作（超过宽度 35% 或快速甩动）、长按（500ms 不移动）。
// 竖向移动为主时交给页面滚动。willLeave(direction) 为真时这一行先滑出再执行 onCommit(direction)。
import { computed, ref } from "vue";

const LONG_PRESS_MS = 500;

export function useSwipe({ el, enabled, canSwipe, willLeave, onCommit, onLongPress }) {
  const dx = ref(0);
  const swiping = ref(false);
  const leaving = ref(false);
  let startX = 0;
  let startY = 0;
  let startT = 0;
  let tracking = false;
  let pressTimer = null;
  let lastGestureAt = 0;

  const width = () => el.value?.offsetWidth || 360;
  const direction = computed(() => (dx.value > 0 ? "right" : dx.value < 0 ? "left" : null));
  const armed = computed(() => Math.abs(dx.value) > width() * 0.35);

  function cancelPress() {
    clearTimeout(pressTimer);
    pressTimer = null;
  }

  function onTouchStart(ev) {
    if (!enabled() || ev.touches.length !== 1 || ev.target.closest("button, input, label, a")) {
      tracking = false;
      return;
    }
    const t = ev.touches[0];
    startX = t.clientX;
    startY = t.clientY;
    startT = Date.now();
    tracking = true;
    swiping.value = false;
    dx.value = 0;
    if (onLongPress) {
      pressTimer = setTimeout(() => {
        pressTimer = null;
        tracking = false;
        lastGestureAt = Date.now();
        navigator.vibrate?.(10);
        onLongPress();
      }, LONG_PRESS_MS);
    }
  }

  function onTouchMove(ev) {
    if (!tracking) return;
    const t = ev.touches[0];
    const mx = t.clientX - startX;
    const my = t.clientY - startY;
    if (Math.abs(mx) > 8 || Math.abs(my) > 8) cancelPress();
    if (!swiping.value) {
      if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
      if (Math.abs(my) >= Math.abs(mx) || !canSwipe(mx > 0 ? "right" : "left")) {
        tracking = false;
        return;
      }
      swiping.value = true;
    }
    if (ev.cancelable) ev.preventDefault();
    dx.value = mx;
  }

  function onTouchEnd() {
    cancelPress();
    if (!tracking) return;
    tracking = false;
    if (!swiping.value) return;
    lastGestureAt = Date.now();
    const velocity = Math.abs(dx.value) / Math.max(1, Date.now() - startT);   // px/ms
    const commit = armed.value || (Math.abs(dx.value) > 48 && velocity > 0.6);
    const dir = direction.value;
    swiping.value = false;
    if (!commit || !canSwipe(dir)) {
      dx.value = 0;
      return;
    }
    if (willLeave(dir)) {
      leaving.value = true;
      dx.value = dir === "right" ? width() : -width();
      setTimeout(() => onCommit(dir), 180);
    } else {
      dx.value = 0;
      onCommit(dir);
    }
  }

  function onTouchCancel() {
    cancelPress();
    tracking = false;
    swiping.value = false;
    dx.value = 0;
  }

  // 手势结束后浏览器可能补发一次 click，忽略它
  const justGestured = () => Date.now() - lastGestureAt < 400;

  const style = computed(() =>
    dx.value || leaving.value
      ? {
          transform: `translateX(${dx.value}px)`,
          transition: swiping.value ? "none" : "transform var(--dur) var(--ease), opacity var(--dur) var(--ease)",
          opacity: leaving.value ? 0 : 1,
        }
      : undefined,
  );

  function reset() {
    leaving.value = false;
    dx.value = 0;
  }

  return {
    dx,
    direction,
    armed,
    style,
    justGestured,
    reset,
    handlers: { touchstart: onTouchStart, touchmove: onTouchMove, touchend: onTouchEnd, touchcancel: onTouchCancel },
  };
}
