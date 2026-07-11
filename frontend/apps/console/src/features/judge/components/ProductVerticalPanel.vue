<script setup lang="ts">
/**
 * 商品垂直分類編輯面板：分組新增/刪除/拖動排序 + 組內 CATEGORY 代碼（tag 清單，可拖排/刪除/Enter 新增）。
 *
 * 內容結構為 `{groups:{分組名:[CATEGORY代碼,...]}, group_order:[分組名,...]}`（見 config/global/product_vertical.json）。
 * ⚠️ groups 是 JSONB object map，PostgreSQL jsonb **不保留 key 順序**（按 key 長度重排）——分組顯示順序
 * 以顯式 `group_order` 陣列為準（缺欄回退 Object.keys，即 jsonb 序，向後相容舊版本內容）。
 * 非 L1/L2/L3 樹狀結構故不用 `RuleTreePanel`；emit 介面對齊（`{json, valid}`），由
 * `ProductVerticalSettingsPanel`（「配置」抽屜）包一層 save / 歷史 / 恢復默認版本化管線。
 * `_meta`（label 等）原樣保留，不因編輯 groups 遺失。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { IconDragDotVertical } from '@arco-design/web-vue/es/icon';
import Sortable, { type SortableEvent } from 'sortablejs';
import { reorderByDragEvent, revertSortableDom, useListDragSort } from '@/composables';

interface ProductVerticalContent {
  groups: Record<string, string[]>;
  group_order?: string[];
  [k: string]: unknown;
}

const props = defineProps<{ content: Record<string, unknown> }>();
const emit = defineEmits<{ (e: 'change', payload: { json: unknown; valid: boolean }): void }>();

/** 深拷貝（JSON 法）。不可用 structuredClone：Vue reactive proxy 會拋 DataCloneError。 */
function deepClone<T>(o: T): T {
  return JSON.parse(JSON.stringify(o));
}

// 本地深拷貝為編輯 model（不直接改 prop）
const model = ref<ProductVerticalContent>(deepClone(props.content as ProductVerticalContent));
const newGroupName = ref('');
/** 各分組「新增代碼」輸入框草稿（keyed by 分組名）。 */
const newCode = ref<Record<string, string>>({});

watch(
  () => props.content,
  (c) => {
    model.value = deepClone(c as ProductVerticalContent);
  },
  { immediate: true },
);

/** 分組顯示順序：group_order 為準（過濾已刪分組）+ 補掛不在 order 內的新分組（向後相容舊內容）。 */
const groupNames = computed(() => {
  const keys = Object.keys(model.value.groups ?? {});
  const order = (model.value.group_order ?? []).filter((n) => keys.includes(n));
  return [...order, ...keys.filter((n) => !order.includes(n))];
});

/** 結構驗證：分組名非空、代碼皆非空字串。 */
const valid = computed(() => {
  const groups = model.value.groups ?? {};
  return Object.entries(groups).every(
    ([name, codes]) =>
      name.trim().length > 0 && Array.isArray(codes) && codes.every((c) => c.trim().length > 0),
  );
});

/** 任一變更 → 正規化 group_order（顯示順序快照）後 emit 整份 content（保留 _meta 等非 groups 欄）。 */
function commit() {
  model.value.group_order = [...groupNames.value];
  emit('change', { json: deepClone(model.value), valid: valid.value });
}

/** 新增分組（名稱去重；已存在則忽略）。 */
function addGroup() {
  const name = newGroupName.value.trim();
  if (!name) return;
  if (!model.value.groups) model.value.groups = {};
  if (name in model.value.groups) return;
  model.value.groups[name] = [];
  newGroupName.value = '';
  commit();
}

/** 刪除分組。 */
function removeGroup(name: string) {
  delete model.value.groups[name];
  model.value.group_order = (model.value.group_order ?? []).filter((n) => n !== name);
  commit();
}

/** 刪除某分組的一個代碼。 */
function removeCode(name: string, code: string) {
  model.value.groups[name] = (model.value.groups[name] ?? []).filter((c) => c !== code);
  commit();
}

/** 新增代碼到某分組（Enter 送出；去空白 + 去重）。 */
function addCode(name: string) {
  const code = (newCode.value[name] ?? '').trim();
  if (!code) return;
  const list = model.value.groups[name] ?? [];
  if (!list.includes(code)) {
    model.value.groups[name] = [...list, code];
    commit();
  }
  newCode.value[name] = '';
}

// ── 分組拖動排序（卡片 title 把手）──
const groupListRef = ref<HTMLElement | null>(null);
useListDragSort(
  groupListRef,
  () => groupNames.value,
  (next) => {
    model.value.group_order = next;
    commit();
  },
  { handle: '.group-drag-handle', draggable: '.arco-card' },
);

// ── 組內代碼 tag 拖動排序：每分組一個 Sortable 實例（function ref 動態掛/卸）──
const tagSortables = new Map<string, Sortable>();
function onTagDragEnd(name: string, evt: SortableEvent) {
  if (evt.oldIndex == null || evt.newIndex == null || evt.oldIndex === evt.newIndex) return;
  revertSortableDom(evt, '.arco-tag');
  model.value.groups[name] = reorderByDragEvent(model.value.groups[name] ?? [], evt);
  commit();
}
/** tag 容器 function ref：元素掛載建 Sortable、卸載銷毀（v-for 動態分組安全）。 */
function setTagContainer(name: string, el: Element | null) {
  tagSortables.get(name)?.destroy();
  tagSortables.delete(name);
  if (el) {
    tagSortables.set(
      name,
      new Sortable(el as HTMLElement, {
        animation: 150,
        draggable: '.arco-tag', // 尾端新增輸入框非 .arco-tag，不參與拖排
        onEnd: (evt) => onTagDragEnd(name, evt),
      }),
    );
  }
}
onBeforeUnmount(() => {
  tagSortables.forEach((s) => s.destroy());
  tagSortables.clear();
});
</script>

<template>
  <a-form
    :model="model"
    layout="vertical"
    size="small"
    class="flex h-full flex-col gap-4 overflow-auto p-1"
  >
    <a-form-item field="newGroupName" label="新增分組" class="mb-0">
      <a-space>
        <a-input
          v-model="newGroupName"
          style="width: 200px"
          placeholder="新分組名（如 Tour）"
          @press-enter="addGroup"
        />
        <a-button type="primary" @click="addGroup">＋ 新增分組</a-button>
      </a-space>
    </a-form-item>

    <a-empty v-if="!groupNames.length" description="尚無分組，於上方新增第一個分組" />
    <div v-else ref="groupListRef" class="flex flex-col gap-3">
      <a-card v-for="name in groupNames" :key="name" size="small">
        <template #title>
          <div class="flex items-center justify-between">
            <span class="inline-flex items-center gap-1">
              <!-- 分組拖曳把手（SortableJS handle） -->
              <IconDragDotVertical
                class="group-drag-handle cursor-move text-[var(--color-text-3)]"
              />
              <span class="font-mono text-sm">{{ name }}</span>
            </span>
            <a-popconfirm :content="`刪除分組「${name}」？`" @ok="removeGroup(name)">
              <a-button size="mini" status="danger">刪除</a-button>
            </a-popconfirm>
          </div>
        </template>
        <!-- 受控 tag 清單（取代 a-input-tag：Arco 內部 DOM 無法掛 Sortable）：tag 可拖排/刪除，尾端輸入框 Enter 新增 -->
        <div
          :ref="(el) => setTagContainer(name, el as Element | null)"
          class="flex flex-wrap items-center gap-1.5"
        >
          <a-tag
            v-for="code in model.groups[name]"
            :key="code"
            closable
            class="cursor-move font-mono"
            @close="removeCode(name, code)"
          >
            {{ code }}
          </a-tag>
          <a-input
            v-model="newCode[name]"
            size="mini"
            style="width: 200px"
            placeholder="輸入代碼後 Enter（如 CATEGORY_019）"
            @press-enter="addCode(name)"
          />
        </div>
      </a-card>
    </div>
  </a-form>
</template>
