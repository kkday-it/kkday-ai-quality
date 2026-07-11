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
// label 同 canon 走 computed v-model（統一綁定手法，取代手動 :model-value+@update）
const label = computed({
  get: () => props.node.label ?? '',
  set: (v) => patch('label', v),
});
</script>

<template>
  <a-form :model="node" layout="vertical" size="small" class="rule-node-form">
    <div
      class="mb-3 inline-block rounded-md bg-[rgb(var(--primary-1))] px-2.5 py-1 font-mono text-sm font-semibold tracking-wide text-[rgb(var(--primary-6))]"
    >
      {{ node.code }}
    </div>
    <a-form-item field="label" label="名稱 label">
      <a-input v-model="label" />
    </a-form-item>
    <a-form-item field="canon" label="法典條文 canon">
      <a-textarea v-model="canon" :auto-size="{ minRows: 2 }" />
    </a-form-item>

    <!-- 判準清單左右兩欄：第一列 允許｜禁止、第二列 好範例｜壞範例（LIST_FIELDS 順序對應）-->
    <div class="grid grid-cols-2 gap-x-4">
      <a-form-item
        v-for="f in LIST_FIELDS"
        :key="String(f.key)"
        :field="String(f.key)"
        :label="f.label"
      >
        <div class="w-full space-y-1">
          <div
            v-for="(item, i) in (node[f.key] as string[]) ?? []"
            :key="i"
            class="flex items-start gap-1"
          >
            <!-- textarea 自動撐高：長條目換行完整顯示、不截斷 -->
            <a-textarea
              :model-value="item"
              size="small"
              :auto-size="{ minRows: 1 }"
              @update:model-value="setListItem(f.key, i, $event)"
            />
            <a-button size="mini" status="danger" @click="removeListItem(f.key, i)">−</a-button>
          </div>
          <a-button size="mini" long @click="addListItem(f.key)">＋ 新增一條</a-button>
        </div>
      </a-form-item>
    </div>
  </a-form>
</template>
