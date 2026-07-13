<script setup lang="ts">
/**
 * 邊界測試集管理（B3：mock 邊界數據上傳 → prompt 修正閉環）：CSV 批量上傳 / 手動新增（複用
 * SaveTestcaseModal）/ 列表 CRUD（啟用開關、刪除）+「用此集測某支 prompt」（開 PromptEvalModal
 * mock 模式，樣本＝全部啟用中 case，不做 md5/篩選抽樣）。三來源同表：CSV 上傳＋手動新增＋
 * 分歧一鍵入集（PromptEvalModal / RowPromptTestModal 的「存為 case」）。
 */
import { computed, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { IconPlus, IconUpload } from '@arco-design/web-vue/es/icon';
import {
  deletePromptTestcase,
  getTaxonomyCascade,
  listPromptTestcases,
  updatePromptTestcase,
  uploadPromptTestcases,
  type CascadeNode,
  type PromptTestcase,
} from '@/api';
import { TableLayout } from '@/components';
import { DEFAULT_PAGE_SIZE } from '@/constants';
import SaveTestcaseModal from './SaveTestcaseModal.vue';
import PromptEvalModal from './PromptEvalModal.vue';

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const cascadeOpts = ref<CascadeNode[]>([]);
const domainFilterOptions = computed(() => [
  { value: '', label: '全部域' },
  ...cascadeOpts.value.map((n) => ({ value: n.value, label: n.label })),
]);
const domainLabel = (code: string): string =>
  cascadeOpts.value.find((n) => n.value === code)?.label || code || '—';
const facetLabel = (domain: string, code: string): string => {
  if (!code) return '—';
  const node = cascadeOpts.value.find((n) => n.value === domain);
  return node?.children?.find((c) => c.value === code)?.label || code;
};
const POL_LABEL: Record<string, string> = { negative: '負向', neutral: '中立', positive: '正向' };

const rows = ref<PromptTestcase[]>([]);
const total = ref(0);
const loading = ref(false);
const page = ref(1);
const pageSize = ref(DEFAULT_PAGE_SIZE);
const filterDomain = ref('');

async function load() {
  loading.value = true;
  try {
    const r = await listPromptTestcases({
      goldL1: filterDomain.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    });
    rows.value = r.items;
    total.value = r.total;
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試集失敗');
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.visible,
  async (v) => {
    if (!v) return;
    if (!cascadeOpts.value.length) cascadeOpts.value = await getTaxonomyCascade();
    page.value = 1;
    load();
  },
);
watch(filterDomain, () => {
  page.value = 1;
  load();
});

async function toggleEnabled(row: PromptTestcase) {
  const next = !row.enabled;
  try {
    await updatePromptTestcase(row.id, { enabled: next });
    row.enabled = next;
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '更新失敗');
  }
}

function confirmDelete(row: PromptTestcase) {
  Modal.confirm({
    title: '刪除測試 case',
    content: `確定刪除「${row.text.slice(0, 30)}」？`,
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
        await deletePromptTestcase(row.id);
        Message.success('已刪除');
        load();
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '刪除失敗');
      }
    },
  });
}

// 手動新增（複用 SaveTestcaseModal：同一份驗證 + 建立邏輯，免另寫一套表單）
const addOpen = ref(false);

// CSV 上傳
const uploading = ref(false);
const uploadErrors = ref<Array<{ row: number; text: string; error: string }>>([]);
const fileInput = ref<HTMLInputElement>();
async function onFileChange(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  uploading.value = true;
  uploadErrors.value = [];
  try {
    const r = await uploadPromptTestcases(file);
    uploadErrors.value = r.errors;
    const errMsg = r.errors.length ? `；${r.errors.length} 筆錯誤（見下方明細）` : '';
    Message.success(`已上傳：新增 ${r.inserted} 筆、跳過重複 ${r.skipped} 筆${errMsg}`);
    load();
  } catch (e2) {
    Message.error(e2 instanceof Error ? e2.message : '上傳失敗');
  } finally {
    uploading.value = false;
    if (fileInput.value) fileInput.value.value = '';
  }
}

// 用此集測某支 prompt（B3 mock 模式）
const evalOpen = ref(false);
const evalPromptCode = ref('prompt_C-1');
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="960"
    :footer="false"
    unmount-on-close
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <template #title>邊界測試集（mock 上傳 → prompt 修正閉環）</template>

    <TableLayout
      v-model:page="page"
      v-model:page-size="pageSize"
      full-height
      row-key="id"
      :data="rows"
      :loading="loading"
      pagination="with-all"
      server
      :total="total"
      @change="load"
    >
      <template #toolbar>
        <a-row :gutter="[12, 12]" align="center" wrap>
          <a-col :flex="'140px'">
            <a-select
              v-model="filterDomain"
              class="w-full"
              size="small"
              :options="domainFilterOptions"
            />
          </a-col>
          <a-col :flex="'none'">
            <a-button size="small" type="outline" @click="addOpen = true">
              <template #icon><icon-plus /></template>
              手動新增
            </a-button>
          </a-col>
          <a-col :flex="'none'">
            <input
              ref="fileInput"
              type="file"
              accept=".csv"
              class="hidden"
              @change="onFileChange"
            />
            <a-button size="small" type="outline" :loading="uploading" @click="fileInput?.click()">
              <template #icon><icon-upload /></template>
              上傳 CSV
            </a-button>
          </a-col>
          <a-col :flex="'auto'" />
          <a-col :flex="'none'">
            <a-button size="small" type="primary" @click="evalOpen = true"
              >用此集測試 Prompt</a-button
            >
          </a-col>
        </a-row>
        <a-alert v-if="uploadErrors.length" type="warning" class="mt-2" closable>
          <div v-for="e in uploadErrors" :key="e.row" class="text-xs">
            第 {{ e.row }} 行「{{ e.text }}」：{{ e.error }}
          </div>
        </a-alert>
      </template>

      <template #columns>
        <a-table-column title="文字" data-index="text" ellipsis tooltip />
        <a-table-column title="域" :width="90">
          <template #cell="{ record }">{{ domainLabel(record.gold_l1) }}</template>
        </a-table-column>
        <a-table-column title="面向" :width="100">
          <template #cell="{ record }">{{ facetLabel(record.gold_l1, record.gold_l2) }}</template>
        </a-table-column>
        <a-table-column title="傾向" :width="70">
          <template #cell="{ record }">{{ POL_LABEL[record.expected_polarity] || '—' }}</template>
        </a-table-column>
        <a-table-column title="備註" data-index="note" :width="160" ellipsis tooltip />
        <a-table-column title="啟用" :width="70">
          <template #cell="{ record }">
            <a-switch size="small" :model-value="record.enabled" @change="toggleEnabled(record)" />
          </template>
        </a-table-column>
        <a-table-column title="" :width="70">
          <template #cell="{ record }">
            <a-button type="text" size="mini" status="danger" @click="confirmDelete(record)"
              >刪除</a-button
            >
          </template>
        </a-table-column>
      </template>
    </TableLayout>
  </a-drawer>

  <!-- 手動新增（複用「存為測試 case」表單，prefill 為空） -->
  <SaveTestcaseModal v-model:visible="addOpen" :prefill="null" @saved="load" />

  <!-- 用此集測某支 prompt（mock 模式：樣本＝全部啟用中 case） -->
  <PromptEvalModal
    v-model:visible="evalOpen"
    :prompt-code="evalPromptCode"
    selectable
    source="mock"
  />
</template>
