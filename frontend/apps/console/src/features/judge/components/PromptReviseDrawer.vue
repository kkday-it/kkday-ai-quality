<script setup lang="ts">
/**
 * 案例庫抽屜：看／勾／刪人工評判過的案例。
 *
 * 案例來源是調試台結果卡旁的「人工評判」區塊（`PromptReviewPanel`）。勾選在此的用途是餵給
 * 後續的 AI 定點改寫與回歸重跑——所以列表刻意把「標錯幾欄」與逐欄正解攤開，讓人在勾之前
 * 就看得出這則案例到底在指控 Prompt 哪裡判壞了，而不是只看到一串時間戳。
 */
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { Modal } from '@arco-design/web-vue';
import { StickyTabs, TableLayout } from '@/components';
import { PERM, type PromptDraftMeta, type PromptReleaseMeta } from '@/api';
import { usePermission } from '@/composables/usePermission';
import { usePromptReviewCases } from '../composables';
import { fmtDt } from '../utils';

// 相對路徑 import（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞會觸發
// circular dep。改寫面板帶 LLM 旋鈕與 diff，切到該分頁才載。
const PromptRevisePanel = defineAsyncComponent(() => import('./PromptRevisePanel.vue'));
const PromptRegressionPanel = defineAsyncComponent(() => import('./PromptRegressionPanel.vue'));

const props = defineProps<{
  visible: boolean;
  /** 頁面上當前的 Prompt 全文（改寫與回歸會以它為基準）。 */
  systemPrompt: string;
  /** 線上最新版版本名。 */
  promptVersion: string;
  /** 頁面上的 Prompt 已偏離最新版。 */
  promptEdited: boolean;
  /** 草稿/正式版清單（與頁面「版本列表」同一份資料源）；回歸重跑可選任一版當基準。 */
  drafts: PromptDraftMeta[];
  releases: PromptReleaseMeta[];
}>();

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
  /** 案例總數變動（父層徽章用實數校正）。 */
  (e: 'count', n: number): void;
  /** 存出了新的 Prompt 版本，父層需重載最新版。 */
  (e: 'savedVersion'): void;
}>();

const { can } = usePermission();
const cases = usePromptReviewCases();
const tab = ref('cases');
/** 「AI 改寫」套用補丁後的候選全文；交給「回歸重跑」分頁，讓人在存版**之前**就能先驗一輪。 */
const candidatePrompt = ref('');

function onApplied(prompt: string): void {
  candidatePrompt.value = prompt;
}

const canManage = computed(() => can(PERM.prejudgeRun));

// 對話原文與模型不列欄：前者在 175px 欄寬下只看得到開頭幾個字，等於白佔版位，改放展開列；
// 列上留「標錯了哪幾欄 + 人寫的建議」——這兩項才是一眼判斷「要不要勾這則餵給 AI」的依據。
const COLUMNS = [
  { title: '評判時間', dataIndex: 'created_at', slotName: 'created', width: 150 },
  { title: '標錯', slotName: 'bad', width: 78, align: 'center' as const },
  { title: '判錯的欄位', slotName: 'badFields', width: 240, ellipsis: true, tooltip: true },
  { title: '修改建議', slotName: 'comment', minWidth: 220, ellipsis: true, tooltip: true },
  { title: 'Prompt 版本', slotName: 'version', width: 160 },
  { title: '評判人', dataIndex: 'reviewer', width: 150, ellipsis: true, tooltip: true },
  { title: '操作', slotName: 'actions', width: 76, align: 'center' as const },
];

/** 開抽屜才拉資料（避免頁面載入就打一次沒人看的請求）。 */
watch(
  () => props.visible,
  async (open) => {
    if (!open) return;
    await cases.load();
    emit('count', cases.cases.value.length);
  },
  { immediate: true },
);

/** 逐欄攤開「AI 判的 → 正解」，供展開列顯示。 */
function correctionRows(row: {
  ai_output: Record<string, unknown>;
  corrections: Record<string, unknown>;
}): Array<{ key: string; before: string; after: string }> {
  return Object.entries(row.corrections ?? {}).map(([key, after]) => ({
    key,
    before: display(row.ai_output?.[key]),
    after: display(after),
  }));
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (Array.isArray(value)) return value.length ? value.join('、') : '[]（空）';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function confirmRemove(id: number): void {
  Modal.confirm({
    title: '刪除案例',
    content: '刪掉後這則案例不再參與 AI 改寫與回歸重跑，且無法復原。',
    okText: '刪除',
    cancelText: '取消',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      await cases.remove(id);
      emit('count', cases.cases.value.length);
    },
  });
}
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="1240"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="emit('update:visible', false)"
  >
    <template #title>
      <div class="flex items-center gap-2">
        <span>案例庫 × AI 改寫</span>
        <a-tag size="small">{{ cases.cases.value.length }} 則</a-tag>
        <a-tag v-if="cases.selectedIds.value.length" color="arcoblue" size="small">
          已勾 {{ cases.selectedIds.value.length }}
        </a-tag>
      </div>
    </template>

    <a-alert v-if="promptEdited" type="warning" class="mb-3">
      頁面上的 Prompt 已編輯未存檔（線上最新版為
      {{
        promptVersion || '—'
      }}）。案例記的是評判當下的版本，改寫與回歸會以你現在編輯中的內容為基準。
    </a-alert>

    <StickyTabs v-model:active-key="tab" type="card-gutter" size="small" :lazy-load="true">
      <a-tab-pane key="cases" title="案例庫">
        <div class="flex h-full flex-col">
          <TableLayout
            full-height
            :data="cases.cases.value"
            :columns="COLUMNS"
            :loading="cases.loading.value"
            :error="cases.error.value"
            row-key="id"
            empty-text="還沒有案例。在調試台跑一條對話，於結果卡旁逐欄標對錯後按「存為案例」。"
            :pagination="false"
            :row-selection="{
              type: 'checkbox',
              showCheckedAll: true,
              selectedRowKeys: cases.selectedIds.value,
            }"
            :expandable="{ width: 40 }"
            @selection-change="(keys: number[]) => (cases.selectedIds.value = keys)"
          >
            <template #created="{ record }">
              <span class="text-xs">{{ fmtDt(record.created_at) }}</span>
            </template>

            <template #bad="{ record }">
              <a-tag v-if="cases.badCount(record)" color="red" size="small">
                {{ cases.badCount(record) }} 欄
              </a-tag>
              <a-tag v-else color="green" size="small">全對</a-tag>
            </template>

            <template #badFields="{ record }">
              <span v-if="cases.badCount(record)" class="text-xs text-[#4e5969]">
                {{ Object.keys(record.corrections).join('、') }}
              </span>
              <span v-else class="text-xs text-[#c9cdd4]">—</span>
            </template>

            <template #comment="{ record }">
              <span v-if="record.comment" class="text-xs">{{ record.comment }}</span>
              <span v-else class="text-xs text-[#c9cdd4]">—</span>
            </template>

            <template #version="{ record }">
              <a-tag v-if="record.prompt_version" size="small">{{ record.prompt_version }}</a-tag>
              <a-tooltip
                v-else
                content="評判當下頁面上的 Prompt 是臨時編輯過的，不對應任何存檔版本"
              >
                <a-tag color="orange" size="small">臨時編輯</a-tag>
              </a-tooltip>
            </template>

            <template #actions="{ record }">
              <a-button
                type="text"
                size="mini"
                status="danger"
                :disabled="!canManage"
                @click="confirmRemove(record.id)"
                >刪除</a-button
              >
            </template>

            <template #expand-row="{ record }">
              <div class="px-4 py-3">
                <div v-if="cases.badCount(record)" class="mb-3">
                  <div class="mb-1 text-xs font-semibold text-[#1d2129]">逐欄正解</div>
                  <div class="flex flex-col gap-1">
                    <div
                      v-for="item in correctionRows(record)"
                      :key="item.key"
                      class="flex flex-wrap items-baseline gap-2 text-xs"
                    >
                      <span class="min-w-[168px] font-medium text-[#4e5969]">{{ item.key }}</span>
                      <span class="text-[#f53f3f] line-through">{{ item.before }}</span>
                      <span class="text-[#86909c]">→</span>
                      <span class="font-medium text-[#00b42a]">{{ item.after }}</span>
                    </div>
                  </div>
                </div>
                <div v-else class="mb-3 text-xs text-[#86909c]">
                  全欄皆判對——這則是回歸正例，用來確認之後改 Prompt 沒把對的改壞。
                </div>

                <div class="mb-1 flex flex-wrap items-baseline gap-2">
                  <span class="text-xs font-semibold text-[#1d2129]">對話原文（前 200 字）</span>
                  <span class="text-[11px] text-[#86909c]">
                    共 {{ record.conversation_chars.toLocaleString() }} 字 · 判定模型
                    {{ record.model || '—' }}
                  </span>
                </div>
                <pre class="conversation-preview">{{ record.conversation_preview }}</pre>
              </div>
            </template>
          </TableLayout>
        </div>
      </a-tab-pane>

      <a-tab-pane key="revise" title="AI 改寫">
        <div class="p-1">
          <a-alert v-if="!cases.selectedIds.value.length" type="info" class="mb-3">
            先回「案例庫」分頁勾選要餵給模型的案例。勾誤判案（標錯 N
            欄）當證據，順手勾幾則全對的當正例—— 模型會知道那些別改壞。
          </a-alert>
          <PromptRevisePanel
            :system-prompt="systemPrompt"
            :review-ids="cases.selectedIds.value"
            @applied="onApplied"
            @saved-version="emit('savedVersion')"
          />
        </div>
      </a-tab-pane>

      <a-tab-pane key="regression" title="回歸重跑">
        <div class="p-1">
          <a-alert v-if="!cases.selectedIds.value.length" type="info" class="mb-3">
            先回「案例庫」分頁勾選要重跑的案例。建議把過去累積的案例全勾——回歸的價值就在於發現
            「這次改的那條修好了，但另外那條被弄壞了」。
          </a-alert>
          <PromptRegressionPanel
            :baseline-prompt="systemPrompt"
            :candidate-prompt="candidatePrompt"
            :review-ids="cases.selectedIds.value"
            :drafts="drafts"
            :releases="releases"
          />
        </div>
      </a-tab-pane>
    </StickyTabs>
  </a-drawer>
</template>

<style scoped>
.conversation-preview {
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid #e5e6eb;
  border-radius: 6px;
  background: #f7f8fa;
  padding: 8px 10px;
  color: #4e5969;
  font-size: 11px;
  line-height: 1.6;
}
</style>
