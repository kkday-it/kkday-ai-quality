<script setup lang="ts">
/**
 * 資料上傳（全自動辨識 + 多工作表批量 + 必備表頭校驗 + 預覽彈窗）。
 *
 * 流程：拖檔 → /validate 乾跑（逐工作表自動辨識來源 + 校驗必備欄，不落庫）→ 彈窗列出
 * 「每表偵測到哪個來源、可否上傳、缺哪些欄」→ 用戶勾選通過項 → /upload 確認匯入。
 * 免手選 tab；一次可傳整本 xlsx（多分頁）。
 */
import { ref, onMounted, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  validateInbound,
  uploadInbound,
  getBatches,
  getBatchItems,
  exportBatchUrl,
  type SheetValidation,
} from '@/api';
import { StateGuard, CardSection } from '@/components';
import { SOURCE_LABEL } from '../constants';

const sourceLabel = (v: string) => SOURCE_LABEL[v] || v;

// ── 上傳：拖檔先校驗，再彈窗確認 ──────────────────────────────
const pendingFile = ref<File | null>(null);
const validating = ref(false);
const importing = ref(false);
const modalVisible = ref(false);
const sheets = ref<SheetValidation[]>([]);
const selectedKeys = ref<string[]>([]); // 勾選的 sheet_name（僅 ok 可選）

const okSheets = computed(() => sheets.value.filter((s) => s.status === 'ok'));

/** 勾選/取消某工作表（僅 ok 可選；維護 selectedKeys）。 */
const toggleSheet = (name: string, checked: boolean) => {
  selectedKeys.value = checked
    ? [...selectedKeys.value, name]
    : selectedKeys.value.filter((k) => k !== name);
};

/** Arco a-upload custom-request：改作「乾跑校驗 + 開彈窗」，不直接上傳。 */
const onPick = (option: any) => {
  const { fileItem, onSuccess, onError } = option;
  const file: File = fileItem.file;
  pendingFile.value = file;
  validating.value = true;
  validateInbound(file)
    .then((r) => {
      sheets.value = r.sheets;
      // 預選所有通過的工作表
      selectedKeys.value = r.sheets.filter((s) => s.status === 'ok').map((s) => s.sheet_name);
      modalVisible.value = true;
      onSuccess(r);
    })
    .catch((e) => {
      Message.error('校驗失敗：' + (e?.message || e));
      onError(e);
    })
    .finally(() => (validating.value = false));
  return { abort() {} };
};

const STATUS_META: Record<string, { color: string; text: string }> = {
  ok: { color: 'green', text: '可上傳' },
  fail: { color: 'red', text: '缺必備欄' },
  unknown: { color: 'gray', text: '無法辨識' },
};

/** 確認匯入勾選且通過的工作表。 */
const confirmImport = async () => {
  const picked = okSheets.value.filter((s) => selectedKeys.value.includes(s.sheet_name));
  if (!picked.length) {
    Message.warning('請至少勾選一個可上傳的工作表');
    return;
  }
  importing.value = true;
  try {
    const r = await uploadInbound(
      pendingFile.value!,
      picked.map((s) => ({ sheet_name: s.sheet_name, source: s.detected_source as string })),
    );
    const ok = (r.results || []).filter((x: any) => !x.error);
    const total = ok.reduce((a: number, x: any) => a + (x.inserted || 0), 0);
    Message.success(`已匯入 ${ok.length} 個工作表、共 ${total} 筆`);
    modalVisible.value = false;
    pendingFile.value = null;
    loadBatches();
  } catch (e: any) {
    Message.error('匯入失敗：' + (e?.message || e));
  } finally {
    importing.value = false;
  }
};

// ── 批次列表（三態）──────────────────────────────────
const batches = ref<any[]>([]);
const loading = ref(true);
const error = ref('');

const loadBatches = async () => {
  loading.value = true;
  error.value = '';
  try {
    batches.value = await getBatches();
  } catch (e: any) {
    error.value = '載入批次清單失敗：' + (e?.message || e);
  } finally {
    loading.value = false;
  }
};
onMounted(loadBatches);

const batchCols = [
  { title: '批次名稱', dataIndex: 'name', slotName: 'name' },
  { title: '來源', dataIndex: 'source', slotName: 'src', width: 130 },
  { title: '筆數', dataIndex: 'row_count', width: 90 },
  { title: '上傳時間', dataIndex: 'uploaded_at', width: 190 },
  { title: '原始檔名', dataIndex: 'original_name', ellipsis: true, tooltip: true },
  { title: '操作', slotName: 'op', width: 170, fixed: 'right' as const },
];

// ── 明細 drawer（點擊表格展示）─────────────────────────
const detailVisible = ref(false);
const detailBatch = ref<any>(null);
const items = ref<any[]>([]);
const itemsLoading = ref(false);
const itemsError = ref('');

const openDetail = async (batch: any) => {
  detailBatch.value = batch;
  detailVisible.value = true;
  itemsLoading.value = true;
  itemsError.value = '';
  items.value = [];
  try {
    items.value = await getBatchItems(batch.batch_id);
  } catch (e: any) {
    itemsError.value = '載入明細失敗：' + (e?.message || e);
  } finally {
    itemsLoading.value = false;
  }
};

const itemCols = [
  { title: '商品', dataIndex: 'prod_oid', width: 110 },
  { title: 'rating', dataIndex: 'rating', width: 80 },
  { title: 'comment / 對話', dataIndex: 'comment', ellipsis: true, tooltip: true },
  { title: '狀態', dataIndex: 'status', width: 90 },
  { title: '錄入時間', dataIndex: 'created_at', width: 180 },
];

/** 導出批次 CSV（瀏覽器直接下載，dev 經 vite proxy /api）。 */
const exportBatch = (batch: any) => {
  window.open(exportBatchUrl(batch.batch_id), '_blank');
};
</script>

<template>
  <div>
    <CardSection
      title="資料上傳"
      hint="拖檔（CSV / 多分頁 xlsx）→ 自動辨識來源 + 表頭校驗 → 勾選確認 → 錄入 PostgreSQL（冪等去重）"
      class="mb-4"
    >
      <a-upload
        :custom-request="onPick"
        :show-file-list="false"
        accept=".csv,.xlsx,.xls"
        draggable
        :disabled="validating"
      >
        <template #upload-button>
          <div
            class="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-[#c9cdd4] bg-[#fafafa] p-8 transition-colors duration-200 hover:border-[#165dff]"
            :class="validating ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'"
          >
            <div class="text-2xl leading-none text-[#165dff]">⬆</div>
            <div class="text-sm font-medium text-[#1d2129]">
              {{ validating ? '校驗中…' : '拖拉或點擊上傳檔案（自動辨識來源）' }}
            </div>
            <div class="text-xs text-[#86909c]">
              支援 .csv / .xlsx / .xls；可一次上傳多分頁 xlsx，系統自動辨識每張工作表屬哪個來源
            </div>
          </div>
        </template>
      </a-upload>
    </CardSection>

    <!-- 校驗預覽彈窗：每工作表偵測來源 + 可否上傳 + 勾選確認 -->
    <a-modal
      v-model:visible="modalVisible"
      title="上傳預覽 · 表頭校驗結果"
      :width="760"
      :ok-text="`匯入勾選的 ${selectedKeys.length} 張工作表`"
      :ok-button-props="{ disabled: !selectedKeys.length }"
      :confirm-loading="importing"
      @ok="confirmImport"
    >
      <p class="mb-3 text-xs text-gray-500">
        共偵測 {{ sheets.length }} 張工作表，其中 {{ okSheets.length }} 張可上傳。勾選要匯入的工作表後按確認；不可上傳者已停用勾選。
      </p>
      <a-table :data="sheets" :pagination="false" size="small" row-key="sheet_name">
        <template #columns>
          <a-table-column title="" :width="50">
            <template #cell="{ record }">
              <a-checkbox
                :model-value="selectedKeys.includes(record.sheet_name)"
                :disabled="record.status !== 'ok'"
                @change="(v) => toggleSheet(record.sheet_name, !!v)"
              />
            </template>
          </a-table-column>
          <a-table-column title="工作表" data-index="sheet_name" :width="160" />
          <a-table-column title="偵測來源" :width="120">
            <template #cell="{ record }">{{ record.label || '—' }}</template>
          </a-table-column>
          <a-table-column title="狀態" :width="100">
            <template #cell="{ record }">
              <a-tag :color="STATUS_META[record.status]?.color">{{
                STATUS_META[record.status]?.text || record.status
              }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="筆數" data-index="row_count" :width="80" />
          <a-table-column title="說明" data-index="reason" ellipsis tooltip />
        </template>
      </a-table>
    </a-modal>

    <CardSection title="上傳批次" :hint="`共 ${batches.length} 批 · 新到舊 · 點「查看」展開明細`">
      <StateGuard
        :loading="loading"
        :error="error"
        :empty="!batches.length"
        empty-text="尚無上傳批次，請於上方上傳檔案"
      >
        <a-table
          :columns="batchCols"
          :data="batches"
          :pagination="{ pageSize: 15, showTotal: true }"
          size="small"
          row-key="batch_id"
          :scroll="{ x: 900 }"
        >
          <template #name="{ record }"
            ><b>{{ record.name }}</b></template
          >
          <template #src="{ record }"
            ><a-tag>{{ sourceLabel(record.source) }}</a-tag></template
          >
          <template #op="{ record }">
            <a-space>
              <a-link @click="openDetail(record)">查看</a-link>
              <a-link @click="exportBatch(record)">導出 CSV</a-link>
            </a-space>
          </template>
        </a-table>
      </StateGuard>
    </CardSection>

    <a-drawer
      v-model:visible="detailVisible"
      :width="860"
      :title="detailBatch ? `批次明細 · ${detailBatch.name}` : '批次明細'"
      :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
      unmount-on-close
    >
      <template #extra>
        <a-button v-if="detailBatch" size="small" @click="exportBatch(detailBatch)"
          >導出 CSV</a-button
        >
      </template>
      <StateGuard
        :loading="itemsLoading"
        :error="itemsError"
        :empty="!items.length"
        empty-text="此批次無明細資料"
      >
        <a-table
          class="min-h-0 flex-1"
          :columns="itemCols"
          :data="items"
          :pagination="{ pageSize: 20, showTotal: true }"
          :scroll="{ y: '100%' }"
          size="small"
          row-key="item_id"
        />
      </StateGuard>
    </a-drawer>
  </div>
</template>
