<script setup lang="ts">
/** 面板編輯：a-tree（L1›L2›L3）左、選中 L3 → RuleNodeForm 右。編輯後 emit 整份 content。 */
import { computed, ref, watch } from 'vue';
import type { TreeNodeData } from '@arco-design/web-vue';
import RuleNodeForm from './RuleNodeForm.vue';

interface Node {
  code: string;
  level: number;
  label: string;
  children?: Node[];
  [k: string]: unknown;
}

const props = defineProps<{ content: Record<string, unknown> }>();
const emit = defineEmits<{ (e: 'change', payload: { json: unknown; valid: boolean }): void }>();

/** 深拷貝（JSON 法）。不可用 structuredClone：Vue reactive proxy 會拋 DataCloneError。 */
function deepClone<T>(o: T): T {
  return JSON.parse(JSON.stringify(o));
}

// 本地深拷貝為編輯 model（不直接改 prop）
const model = ref<Record<string, unknown>>(deepClone(props.content));
const selectedKeys = ref<string[]>([]); // 須在 immediate watch 前宣告（避免 TDZ）

/** model.tree（單域 L1）。 */
const roots = computed(() => (model.value.tree as Node[] | undefined) ?? []);

/** 深度優先找第一個葉節點 code（無 children 即葉；變深度：葉可在 L1/L2/L3）。 */
function firstLeafCode(nodes: Node[]): string | null {
  for (const n of nodes) {
    if (!n.children?.length) return n.code; // 葉
    const c = firstLeafCode(n.children);
    if (c) return c;
  }
  return null;
}

// 切換 / 載入規則時重建 model，並預設選中第一個 L3 葉節點（免手動點才顯表單）
watch(
  () => props.content,
  (c) => {
    model.value = deepClone(c);
    const first = firstLeafCode(roots.value);
    selectedKeys.value = first ? [first] : [];
  },
  { immediate: true },
);

/** content.tree → a-tree data（code 當 key、label 當 title）。 */
function toTree(nodes: Node[]): TreeNodeData[] {
  return nodes.map((n) => ({
    key: n.code,
    title: n.label,
    children: n.children ? toTree(n.children) : undefined,
  }));
}
const treeData = computed(() => toTree(roots.value));

/** 依 code 在 model 找節點（回參照，供就地改）。 */
function findByCode(code: string): Node | null {
  const walk = (nodes: Node[]): Node | null => {
    for (const n of nodes) {
      if (n.code === code) return n;
      if (n.children) {
        const f = walk(n.children);
        if (f) return f;
      }
    }
    return null;
  };
  return walk(roots.value);
}

const selectedNode = computed<Node | null>(() => {
  const code = selectedKeys.value[0];
  const n = code ? findByCode(code) : null;
  return n && !n.children?.length ? n : null; // 只編葉節點（無 children，變深度可為 L2/L3）
});

/** RuleNodeForm 回傳更新後節點 → 就地替換並 emit 整份 content。 */
function onNodeUpdate(updated: { code: string; [k: string]: unknown }) {
  const target = findByCode(updated.code);
  if (!target) return;
  Object.assign(target, updated);
  emit('change', { json: deepClone(model.value), valid: true });
}
</script>

<template>
  <div class="flex h-full gap-4">
    <a-tree
      :data="treeData"
      :selected-keys="selectedKeys"
      size="small"
      block-node
      default-expand-all
      class="h-full w-72 shrink-0 overflow-auto rounded-lg border p-2"
      @update:selected-keys="(k) => (selectedKeys = k as string[])"
    />
    <div class="h-full min-w-0 flex-1 overflow-auto rounded-lg border p-3">
      <RuleNodeForm v-if="selectedNode" :node="selectedNode" @update="onNodeUpdate" />
      <a-empty v-else description="選一個葉節點（面向或細項）編輯判準" />
    </div>
  </div>
</template>
