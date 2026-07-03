<script setup lang="ts">
/** 判準節點表單：編 label + 判準五欄（canon + allow/forbid + 好範例/壞範例 few-shot）。 */
import { computed } from 'vue';

interface L3Node {
  code: string;
  label: string;
  canon?: string;
  allow?: string[];
  forbid?: string[];
  positive_cases?: string[];
  negative_cases?: string[];
  [k: string]: unknown;
}

const props = defineProps<{ node: L3Node }>();
const emit = defineEmits<{ (e: 'update', node: L3Node): void }>();

const LIST_FIELDS: { key: keyof L3Node; label: string }[] = [
  { key: 'allow', label: '允許 allow ✅' },
  { key: 'forbid', label: '禁止 forbid ❌' },
  { key: 'positive_cases', label: '好範例 positive_cases' },
  { key: 'negative_cases', label: '壞範例 negative_cases' },
];

/** 改某欄位 → emit 整個節點（淺拷貝，父層持有深層 model）。 */
function patch(key: keyof L3Node, value: unknown) {
  emit('update', { ...props.node, [key]: value });
}

function setListItem(key: keyof L3Node, idx: number, val: string) {
  const arr = [...((props.node[key] as string[]) ?? [])];
  arr[idx] = val;
  patch(key, arr);
}
function addListItem(key: keyof L3Node) {
  patch(key, [...((props.node[key] as string[]) ?? []), '']);
}
function removeListItem(key: keyof L3Node, idx: number) {
  const arr = [...((props.node[key] as string[]) ?? [])];
  arr.splice(idx, 1);
  patch(key, arr);
}

const canon = computed({
  get: () => props.node.canon ?? '',
  set: (v) => patch('canon', v),
});
</script>

<template>
  <a-form :model="node" layout="vertical" size="small" class="rule-node-form">
    <div
      class="mb-3 inline-block rounded-md bg-[rgb(var(--primary-1))] px-2.5 py-1 font-mono text-sm font-semibold tracking-wide text-[rgb(var(--primary-6))]"
    >
      {{ node.code }}
    </div>
    <a-form-item label="名稱 label">
      <a-input :model-value="node.label" @update:model-value="patch('label', $event)" />
    </a-form-item>
    <a-form-item label="法典條文 canon">
      <a-textarea v-model="canon" :auto-size="{ minRows: 2 }" />
    </a-form-item>

    <a-form-item v-for="f in LIST_FIELDS" :key="String(f.key)" :label="f.label">
      <div class="w-full space-y-1">
        <div
          v-for="(item, i) in (node[f.key] as string[]) ?? []"
          :key="i"
          class="flex items-center gap-1"
        >
          <a-input
            :model-value="item"
            size="small"
            @update:model-value="setListItem(f.key, i, $event)"
          />
          <a-button size="mini" status="danger" @click="removeListItem(f.key, i)">−</a-button>
        </div>
        <a-button size="mini" long @click="addListItem(f.key)">＋ 新增一條</a-button>
      </div>
    </a-form-item>
  </a-form>
</template>
