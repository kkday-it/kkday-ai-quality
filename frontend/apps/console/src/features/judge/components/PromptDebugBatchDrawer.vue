<script setup lang="ts">
/**
 * Prompt 調試台「跑批」抽屜：上傳 CSV/XLSX，以**當前編輯中的 Prompt／輸出契約／LLM 配置**
 * 整批結構化裁決（等同離線 lab 跑批的 app 原生版：app 連線 key、strict schema、斷點續跑）。
 *
 * - 每次啟動建獨立 run 目錄（`data/prompt_debug_batch/<run_id>/`，host 可直取產物）；
 *   Prompt/契約/模型以啟動當下快照鎖進 manifest，之後編輯不影響已建 run，續跑重放同一套。
 * - raw_results.jsonl 逐筆落盤＝斷點：中斷/失敗後「續跑」只補未成功筆，「重跑」忽略斷點全部重打。
 * - 進度走輪詢（in-mem 快照）；server 重啟後 run 變 interrupted，仍可從列表續跑。
 */
import { computed, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconDownload, IconPlayArrow, IconRefresh } from '@arco-design/web-vue/es/icon';
import { useIntervalFn } from '@vueuse/core';
import {
  cancelPromptDebugBatchRun,
  downloadPromptDebugBatchFile,
  getPromptDebugBatchRun,
  listPromptDebugBatchRuns,
  resumePromptDebugBatchRun,
  startPromptDebugBatch,
  type PromptDebugBatchFileKind,
  type PromptDebugBatchRunRow,
  type PromptDebugBatchSnapshot,
  type PromptDebugBatchStatus,
} from '@/api';
import type { LlmOverrides } from '@/features/settings/types';
import { fmtBeijingDt } from '../utils';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 啟動時鎖進 run 的 system prompt（頁面編輯框當前內容）。 */
  systemPrompt: string;
  /** 線上最新版 Prompt 版本名（摘要用）。 */
  promptVersion: string;
  /** 編輯框已偏離最新版＝本批跑的是臨時 Prompt（摘要要講清楚）。 */
  promptEdited: boolean;
  /** 生效 model 名（摘要用；實際以 overrides 解析為準）。 */
  model: string;
  /** 本次 LLM 旋鈕覆寫（與單次調試同一份，缺省沿用功能區默認）。 */
  overrides?: LlmOverrides;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

/** 輪詢間隔／連續失敗上限：非跨環境值，具名避免裸數字。 */
const POLL_MS = 1500;
const POLL_MAX_ERRORS = 5;

const form = reactive({
  file: null as File | null,
  sheet: '',
  idColumn: 'session_oid',
  textColumn: 'conversation_full',
  offset: 0,
  limit: 10,
  workers: 16,
});

const starting = ref(false);
const runs = ref<PromptDebugBatchRunRow[]>([]);
const loadingRuns = ref(false);
const activeRunId = ref('');
const activeSnap = ref<PromptDebugBatchSnapshot | null>(null);
const etaText = ref('');
let pollErrors = 0;

const isXlsx = computed(() => /\.(xlsx|xlsm)$/i.test(form.file?.name ?? ''));
const canStart = computed(() => !!form.file && !!props.systemPrompt.trim() && !starting.value);

/** 進度百分比（0–1，Arco a-progress 口徑）：斷點復用 + 本次完成 / 目標。 */
const pct = computed(() => {
  const s = activeSnap.value;
  if (!s || !s.total) return 0;
  return Math.min(1, (s.resumed + s.processed) / s.total);
});

const STATUS_META: Record<PromptDebugBatchStatus, { label: string; color: string }> = {
  running: { label: '執行中', color: 'arcoblue' },
  cancelling: { label: '停止中', color: 'orangered' },
  done: { label: '完成', color: 'green' },
  error: { label: '失敗', color: 'red' },
  cancelled: { label: '已停止', color: 'gray' },
  interrupted: { label: '中斷（可續跑）', color: 'orange' },
};

/** 狀態顯示 meta（未知狀態原字灰標，避免模板內型別斷言）。 */
const statusMeta = (status: string): { label: string; color: string } =>
  STATUS_META[status as PromptDebugBatchStatus] ?? { label: status, color: 'gray' };

const { pause: pausePoll, resume: resumePoll } = useIntervalFn(pollActive, POLL_MS, {
  immediate: false,
});

/** 追蹤某 run 的即時進度（啟動/續跑/點列表執行中列時呼叫）。 */
function track(runId: string): void {
  activeRunId.value = runId;
  pollErrors = 0;
  void pollActive();
  resumePoll();
}

async function pollActive(): Promise<void> {
  if (!activeRunId.value) {
    pausePoll();
    return;
  }
  try {
    const snap = await getPromptDebugBatchRun(activeRunId.value);
    pollErrors = 0;
    activeSnap.value = snap;
    updateEta(snap);
    if (snap.status !== 'running' && snap.status !== 'cancelling') {
      pausePoll();
      await refreshRuns();
    }
  } catch (error) {
    // 連續多次輪詢失敗（如 server 重啟）才停，避免瞬時抖動誤停
    if (++pollErrors >= POLL_MAX_ERRORS) {
      pausePoll();
      Message.warning(error instanceof Error ? error.message : '進度輪詢中斷');
    }
  }
}

/** 由快照算本次速度與 ETA（只計本次新完成，不含斷點復用）。 */
function updateEta(snap: PromptDebugBatchSnapshot): void {
  if (!snap.started_at || !snap.processed || snap.status !== 'running') {
    etaText.value = '';
    return;
  }
  const elapsed = Math.max(1, Date.now() / 1000 - snap.started_at);
  const rate = snap.processed / elapsed;
  const remaining = Math.max(0, snap.total - snap.resumed - snap.processed);
  const eta = rate > 0 ? remaining / rate : 0;
  const mm = Math.floor(eta / 60);
  const ss = Math.round(eta % 60);
  etaText.value = `${rate.toFixed(2)} 條/秒 · 預估剩 ${mm ? `${mm} 分 ` : ''}${ss} 秒`;
}

async function refreshRuns(): Promise<void> {
  loadingRuns.value = true;
  try {
    runs.value = (await listPromptDebugBatchRuns()).runs;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '載入跑批記錄失敗');
  } finally {
    loadingRuns.value = false;
  }
}

/** Arco upload 以 fileList 形態 emit；:auto-upload=false 下僅取原生 File 自行送 multipart。 */
function onFileChange(fileList: Array<{ file?: File }>): void {
  form.file = fileList.at(-1)?.file ?? null;
}

async function onStart(): Promise<void> {
  if (!canStart.value || !form.file) return;
  starting.value = true;
  try {
    const snap = await startPromptDebugBatch({
      file: form.file,
      systemPrompt: props.systemPrompt,
      sheet: isXlsx.value ? form.sheet : '',
      idColumn: form.idColumn.trim(),
      textColumn: form.textColumn.trim(),
      offset: form.offset,
      limit: form.limit,
      workers: form.workers,
      overrides: props.overrides,
    });
    Message.success(
      `跑批已啟動：${snap.run_id}（目標 ${snap.total} 條，斷點復用 ${snap.resumed}）`,
    );
    activeSnap.value = snap;
    track(snap.run_id);
    await refreshRuns();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '啟動跑批失敗');
  } finally {
    starting.value = false;
  }
}

async function onResume(run: PromptDebugBatchRunRow, rerun: boolean): Promise<void> {
  try {
    const snap = await resumePromptDebugBatchRun(run.run_id, { rerun });
    Message.success(
      rerun
        ? `重跑已啟動：目標 ${snap.total} 條全部重打`
        : `續跑已啟動：復用 ${snap.resumed} 條，待補 ${snap.pending} 條`,
    );
    activeSnap.value = snap;
    track(run.run_id);
    await refreshRuns();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '啟動失敗');
  }
}

async function onCancel(runId: string): Promise<void> {
  try {
    await cancelPromptDebugBatchRun(runId);
    Message.info('已送出停止；已完成筆保留為斷點');
    if (runId === activeRunId.value) void pollActive();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '停止失敗');
  }
}

async function onDownload(
  run: PromptDebugBatchRunRow,
  kind: PromptDebugBatchFileKind,
): Promise<void> {
  try {
    const blob = await downloadPromptDebugBatchFile(run.run_id, kind);
    const names: Record<PromptDebugBatchFileKind, string> = {
      csv: `${run.run_id}_results.csv`,
      jsonl: `${run.run_id}_raw_results.jsonl`,
      preds: `${run.run_id}_preds.json`,
      input: run.input_name,
    };
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = names[kind];
    a.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '下載失敗');
  }
}

const runningExists = computed(() =>
  runs.value.some((r) => r.status === 'running' || r.status === 'cancelling'),
);

watch(
  () => props.visible,
  async (visible) => {
    if (!visible) {
      pausePoll();
      return;
    }
    await refreshRuns();
    const running = runs.value.find((r) => r.status === 'running' || r.status === 'cancelling');
    if (running) track(running.run_id);
  },
);
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="920"
    :footer="false"
    unmount-on-close
    @update:visible="emit('update:visible', $event)"
  >
    <template #title>跑批 · 以當前 Prompt 整批裁決</template>

    <!-- 本批固定配置摘要：啟動當下快照鎖進 run，之後編輯頁面不影響已建 run -->
    <a-alert type="info" class="mb-4">
      本批將鎖定啟動當下的配置：Prompt
      <b>{{ promptEdited ? '頁面臨時編輯版（未存檔）' : `最新版 ${promptVersion || '—'}` }}</b>
      （<b>{{ systemPrompt.length.toLocaleString() }}</b> 字元）· 模型
      <b>{{ model || '（功能區默認）' }}</b
      >；產物落在 <code>data/prompt_debug_batch/&lt;run_id&gt;/</code>（jsonl
      逐筆斷點，中斷可續跑）。
    </a-alert>

    <!-- 新跑批表單 -->
    <section class="mb-4 rounded-lg border border-[#e5e6eb] p-4">
      <div class="mb-3 text-sm font-semibold text-[#1d2129]">新跑批</div>
      <a-upload
        :auto-upload="false"
        :limit="1"
        accept=".csv,.xlsx,.xlsm"
        draggable
        @change="onFileChange"
      >
        <template #upload-button>
          <div
            class="flex h-20 w-full cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[#c9cdd4] bg-[#f7f8fa] text-sm text-[#4e5969]"
          >
            <div>點擊或拖入輸入檔（.csv / .xlsx）</div>
            <div class="mt-1 text-xs text-[#86909c]">
              需含唯一 ID 欄與完整對話欄；表頭行自動識別（前 20 行內）
            </div>
          </div>
        </template>
      </a-upload>

      <a-row :gutter="[12, 8]" align="center" wrap class="mt-3">
        <a-col v-if="isXlsx" :flex="'180px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">工作表（空＝第一個）</span>
            <a-input v-model="form.sheet" class="w-full" placeholder="Sheet 名" allow-clear />
          </div>
        </a-col>
        <a-col :flex="'170px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">ID 欄名</span>
            <a-input v-model="form.idColumn" class="w-full" />
          </div>
        </a-col>
        <a-col :flex="'190px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">對話欄名</span>
            <a-input v-model="form.textColumn" class="w-full" />
          </div>
        </a-col>
        <a-col :flex="'120px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">offset</span>
            <a-input-number v-model="form.offset" class="w-full" :min="0" :step="10" />
          </div>
        </a-col>
        <a-col :flex="'130px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">limit（0＝全部）</span>
            <a-input-number v-model="form.limit" class="w-full" :min="0" :step="10" />
          </div>
        </a-col>
        <a-col :flex="'120px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">併發 workers</span>
            <a-input-number v-model="form.workers" class="w-full" :min="1" :max="32" />
          </div>
        </a-col>
        <a-col :flex="'auto'" class="self-end text-right">
          <a-button type="primary" :loading="starting" :disabled="!canStart" @click="onStart">
            <template #icon><icon-play-arrow /></template>
            開始跑批
          </a-button>
        </a-col>
      </a-row>
      <div class="mt-2 text-xs text-[#86909c]">
        每次啟動建立新 run；跑到一半可停止，之後在下方記錄「續跑」只補未成功筆。全量大批請先用小
        limit 試跑確認欄位與 Prompt 再放量。
      </div>
    </section>

    <!-- 進行中／最近追蹤 run 的即時進度 -->
    <section v-if="activeSnap" class="mb-4 rounded-lg border border-[#e5e6eb] p-4">
      <div class="mb-2 flex items-center justify-between gap-3">
        <div class="flex items-center gap-2 text-sm font-semibold text-[#1d2129]">
          進度 · {{ activeSnap.run_id }}
          <a-tag :color="statusMeta(activeSnap.status).color" size="small">
            {{ statusMeta(activeSnap.status).label }}
          </a-tag>
        </div>
        <a-popconfirm
          v-if="activeSnap.status === 'running'"
          content="確定停止？已完成筆保留為斷點，之後可續跑。"
          @ok="onCancel(activeSnap.run_id)"
        >
          <a-button size="small" status="danger">停止</a-button>
        </a-popconfirm>
      </div>

      <a-progress
        :percent="pct"
        :status="activeSnap.status === 'error' ? 'danger' : pct >= 1 ? 'success' : 'normal'"
      />
      <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#4e5969]">
        <span>目標 {{ activeSnap.total }}</span>
        <span>斷點復用 {{ activeSnap.resumed }}</span>
        <span class="text-[#00b42a]">成功 {{ activeSnap.ok }}</span>
        <span :class="activeSnap.failed ? 'text-[#f53f3f]' : ''">失敗 {{ activeSnap.failed }}</span>
        <span :class="activeSnap.invalid ? 'text-[#ff7d00]' : ''">
          校驗未過 {{ activeSnap.invalid }}
        </span>
        <span>{{ activeSnap.total_tokens.toLocaleString() }} tokens</span>
        <span>US$ {{ activeSnap.cost_usd.toFixed(4) }}</span>
        <span v-if="etaText">{{ etaText }}</span>
      </div>

      <a-alert v-if="activeSnap.error" type="error" class="mt-3">{{ activeSnap.error }}</a-alert>
      <a-alert v-for="w in activeSnap.warnings" :key="w" type="warning" class="mt-2">{{
        w
      }}</a-alert>

      <div v-if="activeSnap.recent.length" class="recent-list mt-3">
        <div
          v-for="item in activeSnap.recent"
          :key="item.item_id + String(item.latency_ms)"
          class="recent-row"
        >
          <span :class="item.ok ? 'text-[#00b42a]' : 'text-[#f53f3f]'">
            {{ item.ok ? '✓' : '✗' }}
          </span>
          <span class="font-medium">{{ item.item_id }}</span>
          <span v-if="item.ok" class="truncate text-[#4e5969]">
            {{ item.L1 }} › {{ item.L2 }}
            <template v-if="item.issues">（校驗未過 {{ item.issues }} 項）</template>
          </span>
          <span v-else class="truncate text-[#f53f3f]">{{ item.error }}</span>
          <span class="ml-auto shrink-0 text-[#86909c]">
            {{ item.latency_ms != null ? `${(item.latency_ms / 1000).toFixed(1)}s` : '—' }}
          </span>
        </div>
      </div>

      <a-collapse v-if="activeSnap.failed_items.length" class="mt-3" :bordered="false">
        <a-collapse-item
          key="failed"
          :header="`失敗明細 ${activeSnap.failed_items.length}${activeSnap.failed_items_truncated ? '+' : ''} 筆`"
        >
          <div v-for="f in activeSnap.failed_items" :key="f.item_id" class="text-xs text-[#4e5969]">
            <span class="font-medium">{{ f.item_id }}</span
            >：{{ f.error }}
          </div>
        </a-collapse-item>
      </a-collapse>
    </section>

    <!-- 跑批記錄（磁碟 run 目錄為準；server 重啟後仍在，可續跑） -->
    <section class="rounded-lg border border-[#e5e6eb] p-4">
      <div class="mb-2 flex items-center justify-between gap-3">
        <div class="text-sm font-semibold text-[#1d2129]">跑批記錄</div>
        <a-button size="small" type="text" :loading="loadingRuns" @click="refreshRuns">
          <template #icon><icon-refresh /></template>
          刷新
        </a-button>
      </div>
      <a-table
        :data="runs"
        :loading="loadingRuns"
        :pagination="false"
        size="small"
        row-key="run_id"
        :scroll="{ x: 860 }"
      >
        <template #columns>
          <a-table-column title="時間（北京）" :width="150">
            <template #cell="{ record }">
              <div class="text-xs">{{ fmtBeijingDt(record.created_at) }}</div>
              <div class="text-[11px] text-[#86909c]">{{ record.run_id }}</div>
            </template>
          </a-table-column>
          <a-table-column title="輸入 / 範圍" :width="180">
            <template #cell="{ record }">
              <div class="truncate text-xs" :title="record.input_name">{{ record.input_name }}</div>
              <div class="text-[11px] text-[#86909c]">
                offset {{ record.offset }} · limit {{ record.limit || '全部' }} ·
                {{ record.workers }} 併發
              </div>
            </template>
          </a-table-column>
          <a-table-column title="Prompt 版本 / 模型" :width="180">
            <template #cell="{ record }">
              <a-tag size="small" :color="record.prompt_version ? 'arcoblue' : 'orange'">
                {{ record.prompt_version || '臨時編輯版' }}
              </a-tag>
              <div class="mt-0.5 text-[11px] text-[#86909c]">{{ record.model }}</div>
            </template>
          </a-table-column>
          <a-table-column title="結果" :width="130">
            <template #cell="{ record }">
              <div class="text-xs">
                <span class="text-[#00b42a]">{{ record.ok }}</span>
                <span v-if="record.failed" class="text-[#f53f3f]"> / 敗 {{ record.failed }}</span>
                <span class="text-[#86909c]"> / {{ record.total || '—' }}</span>
              </div>
              <div class="text-[11px] text-[#86909c]">US$ {{ record.cost_usd.toFixed(4) }}</div>
            </template>
          </a-table-column>
          <a-table-column title="狀態" :width="110">
            <template #cell="{ record }">
              <a-tag size="small" :color="statusMeta(record.status).color">
                {{ statusMeta(record.status).label }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="操作">
            <template #cell="{ record }">
              <div class="flex flex-wrap items-center gap-x-1">
                <a-button
                  v-if="record.status === 'running' || record.status === 'cancelling'"
                  size="mini"
                  type="text"
                  @click="track(record.run_id)"
                  >看進度</a-button
                >
                <a-popconfirm
                  v-if="record.status === 'running'"
                  content="確定停止？已完成筆保留為斷點。"
                  @ok="onCancel(record.run_id)"
                >
                  <a-button size="mini" type="text" status="danger">停止</a-button>
                </a-popconfirm>
                <template v-if="record.status !== 'running' && record.status !== 'cancelling'">
                  <a-button
                    size="mini"
                    type="text"
                    :disabled="runningExists"
                    @click="onResume(record, false)"
                    >續跑</a-button
                  >
                  <a-popconfirm
                    content="忽略斷點、全部重打（重新計費），確定？"
                    @ok="onResume(record, true)"
                  >
                    <a-button size="mini" type="text" status="warning" :disabled="runningExists"
                      >重跑</a-button
                    >
                  </a-popconfirm>
                </template>
                <a-button
                  v-if="record.has_csv"
                  size="mini"
                  type="text"
                  @click="onDownload(record, 'csv')"
                >
                  <template #icon><icon-download /></template>
                  CSV
                </a-button>
                <a-button
                  v-if="record.processed || record.ok"
                  size="mini"
                  type="text"
                  @click="onDownload(record, 'jsonl')"
                >
                  <template #icon><icon-download /></template>
                  JSONL
                </a-button>
              </div>
            </template>
          </a-table-column>
        </template>
        <template #empty>
          <a-empty description="尚無跑批記錄；上方上傳輸入檔開始第一批" />
        </template>
      </a-table>
    </section>
  </a-drawer>
</template>

<style scoped>
.recent-list {
  max-height: 180px;
  overflow: auto;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 6px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}
.recent-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}
</style>
