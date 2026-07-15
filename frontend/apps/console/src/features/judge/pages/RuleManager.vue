<script setup lang="ts">
/**
 * 判決規則管理：左選子規則、右編輯、工具列操作，全走 PostgreSQL 版本化（存檔 = 新版 + 熱重載）。
 * 選單分兩組：整體配置（source_mapping，純 JSON 編輯）＋ 初判 Prompt（Prompt-as-Source
 * 判決 prompt 唯一真相源，md 編輯 + md 歷史 diff）。歷史對比恢復 + 單項/整批恢復默認。
 * Prompt 測試（對單列/勾選多筆跑選定 prompt 子集）於歸因列表工具列與列操作區提供，
 * 本頁不重複提供。
 * 註：歸因分類 C-N（L1/L2/L3 判準樹）+ schema 已於 2026-07-13 隨 Prompt-as-Source 全面重構退役，
 * 判準改走 prompt_C-1~6（見 RuleManager 選單「初判 Prompt」分組）。同日 global_rule（極性閘門+
 * 證據政策）併入 judgment.json（靜態設定檔，改值需重啟後端），亦移出本頁管理範圍。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { PERM } from '@/api';
import { usePermission } from '@/composables/usePermission';
import { IconDownload } from '@arco-design/web-vue/es/icon';
import JsonEditor from '@/components/JsonEditor.vue';
import StateGuard from '@/components/StateGuard.vue';
import { ExportProgressBar } from '@/components';
import { startRulesExport } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { useVerticalFilterStore } from '@/stores';
import RuleHistoryPanel from '../components/RuleHistoryPanel.vue';
import PromptHistoryPanel from '../components/PromptHistoryPanel.vue';
import { versionLabel, exportName } from '../utils';
import { useExportJob } from '../composables';

// 初判 Prompt 編輯器懶載入：md-editor-v3 較重，只在編輯 prompt_* 時才載入該 chunk，不壓首屏 bundle。
const PromptEditor = defineAsyncComponent(() => import('../components/PromptEditor.vue'));

// 權限：無 judge-rule.version.manage 者唯讀（後端 403 為權威，前端 disable + 提示避免做白工）
const { can } = usePermission();
const canManage = computed(() => can(PERM.judgeRuleManage));

const store = useJudgeRulesStore();
// 全局商品垂直分類篩選（查詢用，非判準）：開關 + 選中分類，統一控制歸因列表 / 縱覽 / 未判。
const verticalFilter = useVerticalFilterStore();
/** 初判 Prompt（Prompt-as-Source）：rule_code 前綴 prompt_（prompt_polarity + prompt_C-1~6）。
 * content 形態＝{_meta, text: md}——獨立成群、走 md 編輯器 + md 歷史 diff，不套 JSON 編輯器。 */
const isPromptCode = (code: string): boolean => code.startsWith('prompt_');
// 初判 Prompt 群（左選單第二組）：polarity 置頂、C-1~6 依序。
const promptCodes = computed(() =>
  store.metas
    .filter((m) => isPromptCode(m.rule_code))
    .map((m) => m.rule_code)
    .sort((a, b) =>
      a === 'prompt_polarity' ? -1 : b === 'prompt_polarity' ? 1 : a.localeCompare(b),
    ),
);
const isPrompt = computed(() => isPromptCode(store.activeCode));
// 編輯/檢視模式：panel（prompt_* md 編輯）/ json（整體配置原始編輯）/ history 歷史對比（頁內展示）。
const mode = ref<'panel' | 'json' | 'history'>('json');
const saveOpen = ref(false);
const saveNote = ref('');
const saving = ref(false);
// 導出背景 job（實時進度 + 停止；與問題列表導出共用 useExportJob）
const {
  exporting,
  status: exportStatus,
  progress: exportProgress,
  pct: exportPct,
  run: runExport,
  cancel: cancelExport,
} = useExportJob();

// 重掛 key：rule + active 版本 + 模式 → 切換時 editor 以新內容重置
const editorKey = computed(
  () => `${store.activeCode}-${store.currentMeta?.version ?? 0}-${mode.value}`,
);

onMounted(async () => {
  verticalFilter.loadOptions();
  await store.loadList();
  await store.selectRule('source_mapping');
});

async function pick(code: string) {
  if (store.dirty) {
    Message.warning('有未儲存變更，請先儲存或切換版本');
    return;
  }
  mode.value = isPromptCode(code) ? 'panel' : 'json'; // prompt_* → md 編輯器；其餘（整體配置）→ JSON
  await store.selectRule(code);
}

/** 編輯器 / 面板回報變更 → 寫入 store。 */
function onChange(payload: { json: unknown; valid: boolean }) {
  store.setEdited(payload.json, payload.valid);
}

async function doSave() {
  saving.value = true;
  try {
    await store.save(saveNote.value.trim());
    Message.success('已存入 PostgreSQL（新版本）');
    saveOpen.value = false;
    saveNote.value = '';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '存檔失敗');
  } finally {
    saving.value = false;
  }
}

/** 恢復當前規則為檔案默認（彈窗二次確認）。 */
function doReset() {
  Modal.confirm({
    title: '恢復默認',
    content:
      '確定將此規則恢復為檔案默認內容？會新增一個版本覆蓋當前（保留歷史，可從「歷史」還原）。',
    okText: '恢復默認',
    cancelText: '取消',
    onOk: async () => {
      try {
        await store.resetDefault();
        Message.success('已恢復默認（檔案內容）');
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '恢復失敗');
      }
    },
  });
}

/** 打包 prompts 判決 prompt 目錄（7 支 md ＋ README ＋ BASELINE）為 zip → 背景 job + 實時進度下載。 */
function doExport() {
  return runExport(startRulesExport, exportName('判決 Prompt 包', 'zip'), '已導出 Prompt 包');
}

/** 恢復整體配置（source_mapping）為檔案默認，各新增版本覆蓋當前（彈窗二次確認，保留歷史）。 */
function doResetAll() {
  Modal.confirm({
    title: '恢復所有規則為默認',
    content:
      '確定將整體配置（source_mapping 上傳表頭校驗）恢復為檔案默認？各新增一個版本覆蓋當前（保留歷史）。',
    okText: '全部恢復',
    cancelText: '取消',
    onOk: async () => {
      try {
        const res = await store.resetAllDefault();
        const skip = res.skipped?.length ? `，略過 ${res.skipped.join('、')}（無默認檔）` : '';
        Message.success(`已恢復 ${res.reset.length} 條規則為默認（各新增版本）${skip}`);
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '恢復失敗');
      }
    },
  });
}
</script>

<template>
  <div class="flex h-full flex-col gap-2">
    <a-alert v-if="!canManage" type="warning" banner>
      唯讀模式：規則發布／恢復默認需 judge-rule.version.manage 權限；儲存 / 恢復按鈕已停用（後端亦以
      403 兜底）。
    </a-alert>
    <div class="flex min-h-0 flex-1 gap-4">
      <!-- 左：子規則選單 + 全局商品垂直分類篩選（w-52：容 group indent 後仍完整顯示，不截字）
           高度不用 h-full（父列由 flex-1 撐出、對百分比屬不確定高度，height:100% 會退回 auto 而收邊）；
           改交給父列 align-items:stretch 自動拉滿（同右欄），min-h-0 讓內部選單可正確壓縮/滾動 -->
      <div class="flex min-h-0 w-52 shrink-0 flex-col gap-3">
        <!-- 兩組：整體配置（source_mapping 純 JSON）+ 初判 Prompt（polarity + C-1~6 md） -->
        <a-menu
          :selected-keys="[store.activeCode]"
          class="min-h-0 flex-1 overflow-auto rounded-lg border"
          @menu-item-click="pick"
        >
          <a-menu-item-group title="整體配置">
            <a-menu-item key="source_mapping">
              <span class="font-mono text-xs text-[var(--color-text-3)]">upload</span>
              <span class="ml-2">{{ store.labelFor('source_mapping') }}</span>
            </a-menu-item>
          </a-menu-item-group>
          <!-- 初判 Prompt（判決 prompt 唯一真相源）：md 編輯 + md 歷史 diff，無 JSON -->
          <a-menu-item-group title="初判 Prompt">
            <a-menu-item v-for="c in promptCodes" :key="c">
              <span class="font-mono text-xs text-[var(--color-text-3)]">{{
                c.replace('prompt_', '')
              }}</span>
              <span class="ml-2">{{ store.labelFor(c) }}</span>
            </a-menu-item>
          </a-menu-item-group>
        </a-menu>

        <!-- 商品垂直分類「選項池」配置（查詢用，非判準）：決定歸因列表工具列篩選器可選哪些分類 -->
        <div class="flex-none rounded-lg border p-3">
          <div class="mb-2 flex items-center justify-between">
            <span class="text-xs font-medium">商品垂直分類選項池</span>
          </div>
          <a-select
            :model-value="verticalFilter.pool"
            multiple
            size="small"
            placeholder="選分類分組"
            :max-tag-count="1"
            :options="verticalFilter.allOptions.map((g) => ({ value: g, label: g }))"
            @change="(v) => verticalFilter.setPool(v as string[])"
          />
          <div class="mt-1.5 text-[11px] leading-snug text-[var(--color-text-3)]">
            配置歸因列表工具列可選的分類（選項池／總
            list）；實際篩選於工具列進行，此處不直接篩資料（複選；至少 1 個）。
          </div>
        </div>
      </div>

      <!-- 右：工具列 + 編輯區（直欄撐滿，編輯區 flex-1 內捲） -->
      <div class="flex min-w-0 flex-1 flex-col">
        <div class="mb-3 flex flex-none items-center gap-3">
          <!-- 當前規則的檢視/編輯模式：prompt_*＝編輯（md）+ 歷史（md diff），無 JSON；
             其餘（整體配置）＝JSON + 歷史 -->
          <a-radio-group v-model="mode" type="button" size="small">
            <a-radio v-if="isPrompt" value="panel">編輯</a-radio>
            <a-radio v-else value="json">JSON</a-radio>
            <a-radio value="history">歷史</a-radio>
          </a-radio-group>
          <span v-if="store.currentMeta" class="text-xs text-[var(--color-text-3)]">
            {{ versionLabel(store.currentMeta.created_at, store.currentMeta.version) }}
            <span v-if="store.dirty" class="ml-1 text-[rgb(var(--warning-6))]">● 未存</span>
          </span>
          <div class="flex-1" />
          <!-- 全部規則層級操作（作用於所有規則，非當前選中）：置右 -->
          <a-button
            size="small"
            type="text"
            status="warning"
            :disabled="!canManage"
            @click="doResetAll"
            >全部恢復默認</a-button
          >
          <a-button size="small" type="outline" :loading="exporting" @click="doExport">
            <template #icon><icon-download /></template>
            導出 Prompt 包
          </a-button>
        </div>

        <!-- 導出實時進度：導出進行中才顯示（背景 job + SSE，可停止）-->
        <ExportProgressBar
          v-if="exporting"
          class="mb-3 flex-none"
          label="導出 Prompt 包"
          :status="exportStatus"
          :processed="exportProgress.processed"
          :total="exportProgress.total"
          :pct="exportPct"
          @cancel="cancelExport"
        />

        <!-- 當前規則標頭：碼 + 名稱（左）＋ 針對「當前規則」的恢復默認 / 儲存（右），明示只作用於選中的這條 -->
        <div class="mb-2 flex flex-none items-center gap-2">
          <span
            class="rounded bg-[rgb(var(--primary-1))] px-2 py-0.5 font-mono text-xs font-semibold text-[rgb(var(--primary-6))]"
          >
            {{ store.activeCode }}
          </span>
          <span class="text-sm font-medium text-[var(--color-text-1)]">{{
            store.labelFor(store.activeCode)
          }}</span>
          <div class="flex-1" />
          <a-button
            size="small"
            type="outline"
            status="warning"
            :disabled="!canManage"
            @click="doReset"
            >恢復默認</a-button
          >
          <a-button
            type="primary"
            size="small"
            :disabled="!store.dirty || !canManage"
            @click="saveOpen = true"
            >儲存</a-button
          >
        </div>

        <!-- 編輯區：撐滿剩餘高度，內部各自捲動 -->
        <div class="min-h-0 flex-1">
          <StateGuard :loading="store.loading" :error="store.error">
            <!-- 初判 Prompt（prompt_*）：md 歷史 diff / md 編輯器（優先於下方 JSON 分支）-->
            <PromptHistoryPanel
              v-if="isPrompt && mode === 'history'"
              :key="`phist-${store.activeCode}`"
              class="h-full"
            />
            <PromptEditor
              v-else-if="isPrompt && store.edited"
              :key="editorKey"
              class="h-full"
              :content="store.edited"
              @change="onChange"
            />
            <!-- 歷史模式：頁內對比恢復面板（依 activeCode 重掛，切規則即重載該規則歷史）-->
            <RuleHistoryPanel
              v-else-if="mode === 'history'"
              :key="`hist-${store.activeCode}`"
              class="h-full"
            />
            <!-- 整體配置（source_mapping）：純 JSON 編輯，無面板/schema 驗證 -->
            <JsonEditor
              v-else-if="store.edited"
              :key="editorKey"
              class="h-full"
              fill
              :json="store.edited"
              mode="text"
              @change="onChange"
            />
          </StateGuard>
        </div>
      </div>

      <!-- 存檔備註 -->
      <a-modal
        v-model:visible="saveOpen"
        title="存入 PostgreSQL"
        :confirm-loading="saving"
        @ok="doSave"
      >
        <a-textarea
          v-model="saveNote"
          placeholder="本次修改備註（選填）"
          :auto-size="{ minRows: 2 }"
        />
      </a-modal>
    </div>
  </div>
</template>

<style scoped>
/**
 * 左選單長 label 換行不截字（如「polarity 情緒傾向（Step 1）」在 w-52 內超寬）。
 * Arco 在「item 本身（.arco-menu-item:not(.has-icon)）」與「內層 .arco-menu-item-inner」
 * 兩處都設 nowrap + text-overflow:ellipsis（皆 specificity 0,3,0），故須：
 *   1. 同時覆寫兩層（只改 item 外層無效，文字在 inner 內仍被 nowrap 截）
 *   2. 疊 .arco-menu.arco-menu-vertical 拉高 specificity 蓋過 Arco，免用 !important
 * 無對應 prop / utility 可觸及此第三方內部 DOM，故 :deep（frontend-vue 樣式鐵律 #3）。
 * 改為自動換行 + 行高 1.5 + 高度自適應；短項（C-1~C-6）單行不受影響。
 */
:deep(.arco-menu.arco-menu-vertical .arco-menu-item),
:deep(.arco-menu.arco-menu-vertical .arco-menu-item .arco-menu-item-inner) {
  height: auto;
  line-height: 1.5;
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
}
:deep(.arco-menu.arco-menu-vertical .arco-menu-item) {
  padding-top: 8px;
  padding-bottom: 8px;
}
</style>
