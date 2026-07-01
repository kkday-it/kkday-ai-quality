<script setup lang="ts">
/**
 * 商品分類分組編輯面板：分組新增/刪除表單（分組名 + CATEGORY 代碼清單）。
 *
 * 內容結構為 `{groups:{分組名:[CATEGORY代碼,...]}}`（見 config/ai_judge/rule_category_groups.json），
 * 非 L1/L2/L3 樹狀結構故不能直接用 `RuleTreePanel`；但 emit 介面對齊（`{json, valid}`），
 * 復用 `RuleManager` 既有 save / 歷史 / 恢復默認整套版本化管線。
 */
import { computed, ref, watch } from 'vue';

interface CategoryGroupsContent {
  groups: Record<string, string[]>;
  [k: string]: unknown;
}

const props = defineProps<{ content: Record<string, unknown> }>();
const emit = defineEmits<{ (e: 'change', payload: { json: unknown; valid: boolean }): void }>();

/** 深拷貝（JSON 法）。不可用 structuredClone：Vue reactive proxy 會拋 DataCloneError。 */
function deepClone<T>(o: T): T {
  return JSON.parse(JSON.stringify(o));
}

// 本地深拷貝為編輯 model（不直接改 prop）
const model = ref<CategoryGroupsContent>(deepClone(props.content as CategoryGroupsContent));
const newGroupName = ref('');

watch(
  () => props.content,
  (c) => {
    model.value = deepClone(c as CategoryGroupsContent);
  },
  { immediate: true },
);

const groupNames = computed(() => Object.keys(model.value.groups ?? {}));

/** 結構驗證：分組名非空、代碼皆非空字串。 */
const valid = computed(() => {
  const groups = model.value.groups ?? {};
  return Object.entries(groups).every(
    ([name, codes]) => name.trim().length > 0 && Array.isArray(codes) && codes.every((c) => c.trim().length > 0),
  );
});

/** 任一變更 → emit 整份 content。 */
function commit() {
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
  commit();
}

/** 更新某分組的 CATEGORY 代碼清單（a-input-tag 回傳字串陣列）。去空白 + 去重，避免污染版本化資料與下游展開多算一份。 */
function setCodes(name: string, codes: string[]) {
  model.value.groups[name] = Array.from(new Set(codes.map((c) => c.trim()).filter(Boolean)));
  commit();
}
</script>

<template>
  <div class="flex h-full flex-col gap-4 overflow-auto p-1">
    <div class="flex items-center gap-2">
      <a-input
        v-model="newGroupName"
        size="small"
        style="width: 200px"
        placeholder="新分組名（如 Tour）"
        @press-enter="addGroup"
      />
      <a-button size="small" type="primary" @click="addGroup">＋ 新增分組</a-button>
    </div>

    <a-empty v-if="!groupNames.length" description="尚無分組，於上方新增第一個分組" />
    <div v-else class="flex flex-col gap-3">
      <a-card v-for="name in groupNames" :key="name" size="small">
        <template #title>
          <div class="flex items-center justify-between">
            <span class="font-mono text-sm">{{ name }}</span>
            <a-popconfirm :content="`刪除分組「${name}」？`" @ok="removeGroup(name)">
              <a-button size="mini" status="danger">刪除</a-button>
            </a-popconfirm>
          </div>
        </template>
        <a-input-tag
          :model-value="model.groups[name]"
          size="small"
          placeholder="輸入 CATEGORY 代碼後 Enter（如 CATEGORY_019）"
          @update:model-value="(v) => setCodes(name, v as string[])"
        />
      </a-card>
    </div>
  </div>
</template>
