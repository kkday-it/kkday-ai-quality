<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { uploadInbound, getBatches, getBatchItems, exportBatchUrl } from '@/api';
import { StateGuard, CardSection } from '@/components';
import { SOURCES } from '../constants';

const source = ref(SOURCES[0].value);
const sourceLabel = (v: string) => SOURCES.find((s) => s.value === v)?.label || v;
const currentHint = computed(() => SOURCES.find((s) => s.value === source.value)?.hint || '');

// ── 上傳 ──────────────────────────────────────────────
const uploading = ref(false);

/** Arco a-upload custom-request：經 service 層上傳，成功後刷新批次列表。 */
const onUpload = (option: any) => {
  const { fileItem, onSuccess, onError } = option;
  uploading.value = true;
  uploadInbound(fileItem.file, source.value)
    .then((r) => {
      Message.success(`批次「${r.batch?.name}」已錄入 ${r.inserted} 筆`);
      onSuccess(r);
      loadBatches();
    })
    .catch((e) => {
      Message.error('上傳失敗：' + (e?.message || e));
      onError(e);
    })
    .finally(() => (uploading.value = false));
  return { abort() {} };
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
    <CardSection title="資料上傳" hint="選擇來源 → 上傳 CSV/Excel → 自動建批次並錄入 SQLite（冪等去重）" class="mb-4">
      <div class="mb-4">
        <div class="mb-2 text-[13px] font-semibold">資料來源</div>
        <a-radio-group v-model="source" type="button">
          <a-radio v-for="s in SOURCES" :key="s.value" :value="s.value">{{ s.label }}</a-radio>
        </a-radio-group>
        <div class="mt-1.5 text-xs text-[#86909c]">{{ currentHint }}</div>
      </div>

      <a-upload
        :custom-request="onUpload"
        :show-file-list="false"
        accept=".csv,.xlsx,.xls"
        draggable
        :disabled="uploading"
      >
        <template #upload-button>
          <div
            class="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-[#c9cdd4] bg-[#fafafa] p-8 transition-colors duration-200 hover:border-[#165dff]"
            :class="uploading ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'"
          >
            <div class="text-2xl leading-none text-[#165dff]">⬆</div>
            <div class="text-sm font-medium text-[#1d2129]">{{ uploading ? '上傳中…' : `拖拉或點擊上傳「${sourceLabel(source)}」檔案` }}</div>
            <div class="text-xs text-[#86909c]">支援 .csv / .xlsx / .xls；表頭需含商品 ID 與對話/評論欄（中英別名容錯）</div>
          </div>
        </template>
      </a-upload>
    </CardSection>

    <CardSection title="上傳批次" :hint="`共 ${batches.length} 批 · 新到舊 · 點「查看」展開明細`">
      <StateGuard :loading="loading" :error="error" :empty="!batches.length" empty-text="尚無上傳批次，請於上方上傳檔案">
        <a-table
          :columns="batchCols"
          :data="batches"
          :pagination="{ pageSize: 15, showTotal: true }"
          size="small"
          row-key="batch_id"
          :scroll="{ x: 900 }"
        >
          <template #name="{ record }"><b>{{ record.name }}</b></template>
          <template #src="{ record }"><a-tag>{{ sourceLabel(record.source) }}</a-tag></template>
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
        <a-button v-if="detailBatch" size="small" @click="exportBatch(detailBatch)">導出 CSV</a-button>
      </template>
      <StateGuard :loading="itemsLoading" :error="itemsError" :empty="!items.length" empty-text="此批次無明細資料">
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
