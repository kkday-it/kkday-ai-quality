<script setup lang="ts">
/** 面板編輯：a-tree（L1›L2›L3）左、選中任一節點 → RuleNodeForm 右。編輯後 emit 整份 content。
 *  cascade 分層界線：L1 域 / L2 面向 亦帶判準（canon/allow/forbid），故任一層皆可選編（非僅葉）。 */
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

// 切換 / 載入規則時重建 model，預設選中 L1 根節點（域層判準優先顯示；非第一個 L3 葉）。
// L1/L2/L3 皆可編（分支帶 cascade 分層界線判準），進域先看域界線最符合「層層遞進」閱讀。
watch(
  () => props.content,
  (c) => {
    model.value = deepClone(c);
    const rootCode = roots.value[0]?.code ?? null;
    selectedKeys.value = rootCode ? [rootCode] : [];
  },
  { immediate: true },
);

/** content.tree → a-tree data（code 當 key；title＝code+名稱，如「C-1 商品內容」；層級由樹縮排表達，不加 L 前綴）。 */
function toTree(nodes: Node[]): TreeNodeData[] {
  return nodes.map((n) => ({
    key: n.code,
    title: `${n.code} ${n.label}`,
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
  // 任一層皆可編（L1 域 / L2 面向 / L3 細項）：分支節點帶 cascade 分層界線判準（canon/allow/forbid），
  // 非僅葉。RuleNodeForm level-agnostic，emit 以 spread 保留 children、不清子樹。
  return code ? findByCode(code) : null;
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
      <a-empty v-else description="選一個節點（域／面向／細項）編輯判準" />
    </div>
  </div>
</template>
