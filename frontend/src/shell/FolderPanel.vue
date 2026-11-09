<script setup>
// 文件夹 / 账号面板：当前账号范围（点开账号切换器）、文件夹、标签、账号（分组 / 服务商分区）、其他页面。
// 电脑端在图标栏右侧；手机端放在「邮件」的底部面板里（mobile）。
import { computed } from "vue";
import { useRoute } from "vue-router";
import { NIcon } from "naive-ui";
import {
  ChevronLeftOutlined,
  ExpandMoreOutlined,
  FolderOutlined,
  LabelFilled,
  LabelOutlined,
  LayersOutlined,
  UnfoldMoreOutlined,
} from "@vicons/material";
import GmAvatar from "../ui/GmAvatar.vue";
import GmCountBadge from "../ui/GmCountBadge.vue";
import { store } from "../store";
import {
  PAGES,
  accountSections,
  accounts,
  currentAccount,
  folders,
  isGroupCollapsed,
  labels,
  pageCount,
  switchAccount,
  toggleGroup,
} from "./navModel";

const props = defineProps({ mobile: { type: Boolean, default: false } });
const emit = defineEmits(["navigate", "collapse"]);

const route = useRoute();
const onMail = computed(() => route.name === "list" || route.name === "message");
const activeKind = computed(() => (onMail.value ? store.view.kind : null));
const multiAccount = computed(() => accounts.value.length > 1);
// 电脑端功能页在图标栏上，这里只列其余的；手机端全部列出
const extraPages = computed(() => PAGES.filter((p) => props.mobile || !p.rail));

function pick(id) {
  switchAccount(id);
  emit("navigate");
}

function openSwitcher() {
  store.accountSwitcher = true;
}
</script>

<template>
  <nav class="gm-fp" :class="{ 'is-mobile': mobile }" aria-label="文件夹与账号">
    <div class="gm-fp-head">
      <button type="button" class="gm-fp-scope" title="切换账号（'）" @click="openSwitcher">
        <GmAvatar v-if="currentAccount" :name="currentAccount.name" :color="currentAccount.color" :size="28" />
        <span v-else class="gm-fp-all"><NIcon :component="LayersOutlined" :size="18" /></span>
        <span class="gm-fp-scope-text">
          <span class="gm-fp-scope-name">{{ currentAccount ? currentAccount.name : "全部账号" }}</span>
          <span class="gm-fp-scope-sub">{{ currentAccount ? currentAccount.email : `${accounts.length} 个账号` }}</span>
        </span>
        <NIcon :component="UnfoldMoreOutlined" :size="18" class="gm-fp-scope-icon" />
      </button>
      <button v-if="!mobile" type="button" class="gm-fp-icon" title="收起面板（[）" aria-label="收起面板" @click="emit('collapse')">
        <NIcon :component="ChevronLeftOutlined" :size="20" />
      </button>
    </div>

    <div class="gm-fp-body">
      <ul class="gm-fp-list">
        <li v-for="f in folders" :key="f.kind">
          <RouterLink :to="f.to" class="gm-fp-item" :class="{ 'is-active': activeKind === f.kind }" @click="emit('navigate')">
            <NIcon :component="f.icon" :size="20" />
            <span class="gm-fp-text">{{ f.label }}</span>
            <GmCountBadge :count="f.count" />
          </RouterLink>
        </li>
      </ul>

      <template v-if="labels.length">
        <div class="gm-fp-heading">标签</div>
        <ul class="gm-fp-list">
          <li v-for="lb in labels" :key="lb.id">
            <RouterLink
              :to="lb.to"
              class="gm-fp-item"
              :class="{ 'is-active': activeKind === lb.kind }"
              :title="multiAccount && !currentAccount && lb.owner ? `${lb.name}（${lb.owner.email}）` : lb.name"
              @click="emit('navigate')"
            >
              <NIcon :component="activeKind === lb.kind ? LabelFilled : LabelOutlined" :size="20" />
              <span class="gm-fp-text">{{ lb.short }}</span>
            </RouterLink>
          </li>
        </ul>
      </template>

      <template v-if="multiAccount">
        <div class="gm-fp-heading">
          账号
          <RouterLink to="/accounts/" class="gm-fp-heading-link" title="添加或管理账号" @click="emit('navigate')">管理</RouterLink>
        </div>
        <ul class="gm-fp-list">
          <li>
            <button type="button" class="gm-fp-item" :class="{ 'is-active': onMail && !currentAccount }" @click="pick('')">
              <NIcon :component="LayersOutlined" :size="20" />
              <span class="gm-fp-text">全部账号</span>
            </button>
          </li>
        </ul>
        <section v-for="sec in accountSections" :key="sec.provider?.key || 'all'" class="gm-fp-section">
          <div v-if="sec.provider" class="gm-fp-provider" :style="{ '--provider': sec.provider.color }">
            <span class="gm-fp-provider-name">{{ sec.provider.name }}</span>
            <span class="gm-fp-provider-meta">{{ sec.accounts.length }} 个<template v-if="sec.unread"> · {{ sec.unread }} 未读</template></span>
          </div>
          <ul class="gm-fp-list">
            <li v-for="g in sec.groups" :key="g.name">
              <button
                type="button"
                class="gm-fp-item gm-fp-group"
                :aria-expanded="!isGroupCollapsed(`${sec.provider?.key || ''}:${g.name}`)"
                @click="toggleGroup(`${sec.provider?.key || ''}:${g.name}`)"
              >
                <NIcon :component="FolderOutlined" :size="20" />
                <span class="gm-fp-text">{{ g.name }}</span>
                <GmCountBadge :count="g.unread" />
                <NIcon
                  :component="ExpandMoreOutlined"
                  :size="18"
                  class="gm-fp-caret"
                  :class="{ 'is-closed': isGroupCollapsed(`${sec.provider?.key || ''}:${g.name}`) }"
                />
              </button>
              <ul v-if="!isGroupCollapsed(`${sec.provider?.key || ''}:${g.name}`)" class="gm-fp-list is-nested">
                <li v-for="a in g.accounts" :key="a.id">
                  <button
                    type="button"
                    class="gm-fp-item"
                    :class="{ 'is-active': onMail && currentAccount?.id === a.id }"
                    :title="a.email"
                    @click="pick(a.id)"
                  >
                    <span class="gm-fp-dot" :style="{ background: a.color }" />
                    <span class="gm-fp-text">{{ a.name }}</span>
                    <GmCountBadge :count="a.unread_inbox" />
                  </button>
                </li>
              </ul>
            </li>
            <li v-for="a in sec.ungrouped" :key="a.id">
              <button
                type="button"
                class="gm-fp-item"
                :class="{ 'is-active': onMail && currentAccount?.id === a.id }"
                :title="a.email"
                @click="pick(a.id)"
              >
                <span class="gm-fp-dot" :style="{ background: a.color }" />
                <span class="gm-fp-text">{{ a.name }}</span>
                <GmCountBadge :count="a.unread_inbox" />
              </button>
            </li>
          </ul>
        </section>
      </template>
      <p v-else-if="store.nav && !accounts.length" class="gm-fp-empty">
        还没有账号，<RouterLink to="/accounts/" @click="emit('navigate')">添加一个</RouterLink>
      </p>

      <div class="gm-fp-heading">{{ mobile ? "工具" : "更多" }}</div>
      <ul class="gm-fp-list">
        <li v-for="p in extraPages" :key="p.path">
          <RouterLink :to="p.path" class="gm-fp-item" :class="{ 'is-active': route.meta.section === p.section }" @click="emit('navigate')">
            <NIcon :component="p.icon" :size="20" />
            <span class="gm-fp-text">{{ p.label }}</span>
            <GmCountBadge :count="pageCount(p)" />
          </RouterLink>
        </li>
      </ul>
    </div>
  </nav>
</template>

<style scoped>
.gm-fp { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.gm-fp-head {
    display: flex;
    align-items: center;
    gap: var(--sp-1);
    padding: var(--sp-3) var(--sp-2) var(--sp-2) var(--sp-3);
    flex: none;
}
.gm-fp-scope {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
    padding: 6px 8px;
    border: 0;
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
}
.gm-fp-scope:hover { background: var(--gm-hover); }
.gm-fp-all {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--gm-primary-soft);
    color: var(--gm-primary);
    flex: none;
}
.gm-fp-scope-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.gm-fp-scope-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-fp-scope-sub { font-size: var(--fs-xs); color: var(--gm-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-fp-scope-icon { color: var(--gm-text-3); flex: none; }
.gm-fp-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-3);
    cursor: pointer;
    flex: none;
}
.gm-fp-icon:hover { background: var(--gm-hover); color: var(--gm-text); }

.gm-fp-body { flex: 1; min-height: 0; overflow-y: auto; padding: 0 var(--sp-2) var(--sp-4); }
.gm-fp-list { list-style: none; margin: 0; padding: 0; }
.gm-fp-list.is-nested { padding-left: 14px; }
.gm-fp-item {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    height: 36px;
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text-2);
    font: inherit;
    text-align: left;
    cursor: pointer;
    transition: background var(--dur-fast) var(--ease);
}
.gm-fp-item:hover { background: var(--gm-hover); color: var(--gm-text); }
.gm-fp-item.is-active { background: var(--gm-selected); color: var(--gm-primary); font-weight: 600; }
.gm-fp-item:focus-visible { outline: 2px solid var(--gm-primary); outline-offset: -2px; }
.gm-fp-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-fp-dot { width: 10px; height: 10px; margin: 0 5px; border-radius: 3px; flex: none; }
.gm-fp-caret { color: var(--gm-text-3); transition: transform var(--dur-fast) var(--ease); }
.gm-fp-caret.is-closed { transform: rotate(-90deg); }
.gm-fp-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--sp-4) 10px var(--sp-1);
    font-size: var(--fs-xs);
    font-weight: 600;
    letter-spacing: .02em;
    color: var(--gm-text-3);
}
.gm-fp-heading-link { font-weight: 500; }
.gm-fp-section { margin-top: var(--sp-1); }
.gm-fp-provider {
    display: flex;
    align-items: baseline;
    gap: var(--sp-2);
    margin: var(--sp-2) 0 2px;
    padding: 4px 10px;
    border-left: 3px solid var(--provider);
    border-radius: 2px var(--r-sm) var(--r-sm) 2px;
    background: color-mix(in srgb, var(--provider) 8%, transparent);
}
.gm-fp-provider-name { font-weight: 600; font-size: var(--fs-sm); color: var(--provider); }
.gm-fp-provider-meta { margin-left: auto; font-size: var(--fs-xs); color: var(--gm-text-3); }
.gm-fp-empty { padding: var(--sp-2) 10px; color: var(--gm-text-3); }

.gm-fp.is-mobile .gm-fp-item { height: 44px; }
</style>
