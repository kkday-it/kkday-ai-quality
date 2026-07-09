<script setup lang="ts">
/**
 * 💾 資料導入面板（配置抽屜第 4 tab）。
 * 上傳資料包 zip（由 scripts/tools/dump_datapack.py 產生）→ 乾跑校驗預覽 → type-to-confirm →
 * 背景匯入 + SSE 逐表進度。安全：只灌白名單表、不執行 SQL（見後端 datapack）。
 * 權限：需 data.datapack.import（admin 級）——入口 tab 由 SettingsDrawer 以 can(PERM.dataDatapackImport)
 * v-if 過濾，後端端點亦掛 require_permission 兜底（見 app/core/permissions）。
 */
import { ref, computed, onUnmounted } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useExportJob } from '@/features/judge/composables';
import { ExportProgressBar } from '@/components';
import {
  startDatapackExport,
  validateDatapack,
  importDatapack,
  importStreamUrl,
  type ValidateReport,
  type ImportJobSnapshot,
} from '@/api/admin-import.api';

const file = ref<File | null>(null);
const includeSensitive = ref(false);
const validating = ref(false);
const report = ref<ValidateReport | null>(null);
const confirmInput = ref('');
const importing = ref(false);
const snapshot = ref<ImportJobSnapshot | null>(null);
let es: EventSource | null = null;

/** 覆蓋預覽表欄位（每表：資料包列數 / 現有列數 / 動作）。 */
const COLS = [
  { title: '資料表', dataIndex: 'name' },
  { title: '資料包', dataIndex: 'pack_rows', align: 'right' as const },
  { title: '現有', dataIndex: 'db_rows', align: 'right' as const },
  { title: '動作', slotName: 'action' },
];

/** 只顯示資料包內含的表（未含者對匯入無意義）。 */
const planRows = computed(() => report.value?.tables.filter((t) => t.in_pack) ?? []);

/** 可否匯入：校驗通過 + 確認短語正確 + 非匯入中。 */
const canImport = computed(
  () =>
    !!report.value?.ok &&
    confirmInput.value.trim() === report.value?.confirm_phrase &&
    !importing.value,
);

// 導出＝背景 job（逐表 SSE 進度）：復用通用 export_jobs（同 xlsx 導出）；本面板只提供 starter + 檔名。
const exportJob = useExportJob();
const { exporting, status: exportStatus, progress: exportProgress, pct: exportPct } = exportJob;

/** 本地時間戳檔名（kkday-ai-quality-datapack-YYYYMMDDHHMM.zip）。 */
const _tsName = () => {
  const p = (n: number) => String(n).padStart(2, '0');
  const d = new Date();
  const ts = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}${p(d.getMinutes())}`;
  return `kkday-ai-quality-datapack-${ts}.zip`;
};

/** 啟動導出 job：SSE 追逐表進度 → done 下載 zip。 */
const runExport = () =>
  exportJob.run(() => startDatapackExport(includeSensitive.value), _tsName(), '已導出資料包');

/** 選檔（Arco auto-upload=false）→ 取原始 File → 立即乾跑校驗。 */
const onFileChange = async (fileList: { file?: File }[]) => {
  const f = fileList.at(-1)?.file ?? null;
  file.value = f;
  resetImport();
  if (f) await runValidate();
};

/** 乾跑校驗（含三態：validating / error / 空）。 */
const runValidate = async () => {
  if (!file.value) return;
  validating.value = true;
  report.value = null;
  try {
    report.value = await validateDatapack(file.value, includeSensitive.value);
  } catch (e) {
    Message.error(`校驗失敗：${(e as Error).message}`);
  } finally {
    validating.value = false;
  }
};

/** 切換敏感表納入 → 重新校驗（計畫會變）。 */
const onToggleSensitive = () => {
  if (file.value) runValidate();
};

/** 確認匯入：啟動背景 job → SSE 消費逐表進度。 */
const runImport = async () => {
  if (!file.value || !canImport.value) return;
  importing.value = true;
  snapshot.value = null;
  try {
    const { job_id } = await importDatapack(file.value, confirmInput.value.trim(), includeSensitive.value);
    es = new EventSource(importStreamUrl(job_id));
    es.onmessage = (ev) => {
      snapshot.value = JSON.parse(ev.data) as ImportJobSnapshot;
      if (snapshot.value.status === 'done') {
        Message.success('匯入完成，資料已還原');
        closeStream();
      } else if (snapshot.value.status === 'error') {
        Message.error(`匯入失敗：${snapshot.value.error}`);
        closeStream();
      }
    };
    es.onerror = () => {
      Message.error('進度連線中斷');
      closeStream();
    };
  } catch (e) {
    Message.error(`匯入啟動失敗：${(e as Error).message}`);
    closeStream();
  }
};

/** 關閉 SSE + 解除匯入中狀態。 */
const closeStream = () => {
  es?.close();
  es = null;
  importing.value = false;
};

/** 清匯入態（換檔 / 重選時）。 */
const resetImport = () => {
  report.value = null;
  confirmInput.value = '';
  snapshot.value = null;
  closeStream();
};

onUnmounted(closeStream);
</script>

<template>
  <div class="flex flex-col gap-4">
    <a-alert type="normal">
      資料包 zip 承載全部數據（純資料，非 SQL）。可在此<strong>導出</strong>下載、或<strong>導入</strong>載入他人資料包。
      導入為<strong>覆蓋式</strong>：清空並以資料包重建各表。
    </a-alert>

    <!-- 共用：是否納入敏感表（導出/導入皆適用）-->
    <a-checkbox v-model="includeSensitive" @change="onToggleSensitive">
      納入帳號 / 機密表（users、user_settings）—— 導出/導入皆適用；跨環境金鑰須一致，否則機密匯入後失效
    </a-checkbox>

    <!-- 導出區（背景 job + 逐表 SSE 進度）-->
    <div class="flex flex-col gap-2">
      <div class="flex items-center gap-2">
        <a-button type="outline" :loading="exporting" :disabled="exporting" @click="runExport">
          <template #icon><icon-download /></template>
          導出資料包
        </a-button>
        <span class="text-sm text-[var(--kk-color-text-3)]">下載當前全庫快照 zip，供分發 / 備份</span>
      </div>
      <ExportProgressBar
        v-if="exporting"
        :status="exportStatus"
        :processed="exportProgress.processed"
        :total="exportProgress.total"
        :pct="exportPct"
        label="導出中"
        @cancel="exportJob.cancel()"
      />
    </div>

    <a-divider class="!my-1" />

    <!-- 導入區：上傳 -->
    <a-upload
      :auto-upload="false"
      :limit="1"
      accept=".zip"
      :show-file-list="true"
      @change="onFileChange"
    >
      <template #upload-button>
        <a-button type="outline">選擇資料包 zip…</a-button>
      </template>
    </a-upload>

    <!-- 三態：validating -->
    <a-spin v-if="validating" tip="校驗中…" class="self-center" />

    <!-- 校驗結果 -->
    <template v-else-if="report">
      <!-- schema banner -->
      <a-alert :type="report.schema_ok ? 'success' : 'error'">
        schema 版本：資料包 {{ report.manifest_head ?? '—' }} / 當前 DB {{ report.current_head ?? '—' }}
        {{ report.schema_ok ? '（相符）' : '（不符，無法匯入，請先 alembic upgrade head 或重新匯出）' }}
      </a-alert>
      <a-alert v-for="(w, i) in report.warnings" :key="i" type="warning">{{ w }}</a-alert>
      <a-alert v-for="(er, i) in report.errors" :key="`e${i}`" type="error">{{ er }}</a-alert>

      <!-- 覆蓋預覽表 -->
      <a-table :data="planRows" :columns="COLS" :pagination="false" row-key="name" size="small">
        <template #action="{ record }">
          <span v-if="record.will_truncate" class="text-[var(--kk-color-text-danger)]">
            覆蓋（清 {{ record.db_rows }} → 灌 {{ record.pack_rows }}）
          </span>
          <span v-else class="text-[var(--kk-color-text-3)]">不碰</span>
        </template>
      </a-table>

      <!-- type-to-confirm + 匯入進度 -->
      <template v-if="report.ok">
        <div class="flex items-center gap-2">
          <span class="whitespace-nowrap">輸入 <code>{{ report.confirm_phrase }}</code> 以確認：</span>
          <a-input v-model="confirmInput" :placeholder="report.confirm_phrase" class="flex-1" allow-clear />
          <a-button type="primary" status="danger" :disabled="!canImport" :loading="importing" @click="runImport">
            確認匯入
          </a-button>
        </div>

        <!-- 匯入進度（SSE） -->
        <div v-if="snapshot" class="flex flex-col gap-1">
          <a-progress
            :percent="snapshot.total_tables ? snapshot.done_tables / snapshot.total_tables : 0"
            :status="snapshot.status === 'error' ? 'danger' : snapshot.status === 'done' ? 'success' : 'normal'"
          >
            <template #text="{ percent }">{{ (percent * 100).toFixed(2) }}%</template>
          </a-progress>
          <span class="text-sm text-[var(--kk-color-text-3)]">
            {{ snapshot.done_tables }}/{{ snapshot.total_tables }} 表
            <template v-if="snapshot.current_table">· 當前 {{ snapshot.current_table }}</template>
          </span>
        </div>
      </template>
    </template>
  </div>
</template>
