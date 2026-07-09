<script setup lang="ts">
/**
 * 判決規則管理：左選子規則、右編輯、工具列操作，全走 PostgreSQL 版本化（存檔 = 新版 + 熱重載）。
 * 選單置頂特殊項（純 JSON 編輯）：schema 結構規格 / global 判決總規範 / judgment 判決配置
 *   （信心閾值 · 顯示 label · prejudge 旋鈕含 G1 auto_confirm.audit_sample_rate，QC 免改碼調）
 *   / source_mapping 上傳表頭校驗（required_headers 指紋 + field_map，存檔即生效於資料上傳頁）；
 *   其後為歸因分類 C-N（面板 / JSON 雙編 + active schema 驗證）。歷史對比恢復 + 單項/整批恢復默認。
 */
import { computed, onMounted, ref, shallowRef } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { PERM } from '@/api';
import { usePermission } from '@/composables/usePermission';
import { IconDownload } from '@arco-design/web-vue/es/icon';
import JsonEditor from '@/components/JsonEditor.vue';
import StateGuard from '@/components/StateGuard.vue';
import { ExportProgressBar } from '@/components';
import { getRule, startRulesExport } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { useVerticalFilterStore } from '@/stores';
import RuleTreePanel from '../components/RuleTreePanel.vue';
import RuleHistoryPanel from '../components/RuleHistoryPanel.vue';
import { versionLabel, exportName } from '../utils';
import { useExportJob } from '../composables';

// 權限：無 judge-rule.version.manage 者唯讀（後端 403 為權威，前端 disable + 提示避免做白工）
const { can } = usePermission();
const canManage = computed(() => can(PERM.judgeRuleManage));

const store = useJudgeRulesStore();
// 全局商品垂直分類篩選（查詢用，非判準）：開關 + 選中分類，統一控制歸因列表 / 縱覽 / 未判。
const verticalFilter = useVerticalFilterStore();
// 歸因分類（C-N，schema/global/judgment 另置頂）；code 與顯示名皆來自後端 meta（label SSOT），不讀前端靜態表。
// 非 L1-L3 歸因樹者一律排除出 C-N 迴圈、改為置頂特殊項（product_vertical 已移「配置」抽屜，此處不顯示；
// schema＝結構規格、global＝判決總規範、judgment＝信心閾值/label/prejudge 旋鈕，皆純 JSON 編輯）。
const _NON_DOMAIN_CODES = new Set([
  'schema',
  'product_vertical',
  'global_rule',
  'judgment',
  'source_mapping',
]);
const domainCodes = computed(() =>
  store.metas.filter((m) => !_NON_DOMAIN_CODES.has(m.rule_code)).map((m) => m.rule_code),
);
// 純 JSON 編輯（無 L1-L3 樹）的置頂特殊項：一律 JSON 模式、不走 RuleTreePanel、不套 schema 驗證。
const _NON_TREE_CODES = new Set(['schema', 'global_rule', 'judgment', 'source_mapping']);
// 編輯/檢視模式：panel 面板編輯 / json 原始編輯 / history 歷史對比（頁內展示，取代原彈窗）。
const mode = ref<'panel' | 'json' | 'history'>('panel');
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

// schema content（給 JSON 模式即時驗證；編輯 schema 自身時不套）
const schemaContent = shallowRef<Record<string, unknown> | null>(null);

const isSchema = computed(() => store.activeCode === 'schema');
// 是否為真正的 C-N 歸因分類（有 L1-L3 樹）。面板模式 / RuleTreePanel / 歸因 schema 驗證只對這些 code 生效——
// 防禦：商品垂直分類（product_vertical）由「配置」抽屜的 ProductVerticalSettingsPanel 共用同一 store，其
// selectRule('product_vertical') 會改共用 activeCode；以 domainCodes 精準判斷（非 !_NON_TREE_CODES）即免把
// product_vertical 的 {groups}（非 L1-L3 樹）誤餵 RuleTreePanel 成空白錯亂樹。schema/global/judgment 亦非樹。
const isDomainTree = computed(() => domainCodes.value.includes(store.activeCode));
// 本頁是否管理當前 activeCode：product_vertical 由「配置」抽屜（ProductVerticalSettingsPanel）獨立管理、
// 本頁選單不含它（見 _NON_DOMAIN_CODES）。但抽屜共用同一 judgeRules store，其 selectRule('product_vertical')
// 會改共用 activeCode → 本頁背景會誤渲染 product_vertical 編輯器。守衛：非本頁管理的 code 時不渲染編輯區。
const isOwnRule = computed(() => store.activeCode !== 'product_vertical');
// schema 無 L1›L2›L3 樹，「面板」改用 JsonEditor 結構化 tree 模式；JSON＝text 模式
const jsonEditorMode = computed<'tree' | 'text'>(() =>
  isSchema.value && mode.value === 'panel' ? 'tree' : 'text',
);
// 重掛 key：rule + active 版本 + 模式 → 切換時 editor 以新內容重置
const editorKey = computed(() => `${store.activeCode}-${store.currentMeta?.version ?? 0}-${mode.value}`);

onMounted(async () => {
  verticalFilter.loadOptions();
  await store.loadList();
  await store.selectRule('C-1');
  try {
    schemaContent.value = (await getRule('schema')).content;
  } catch {
    schemaContent.value = null; // 無 schema 仍可編輯（後端為真閘）
  }
});

async function pick(code: string) {
  if (store.dirty) {
    Message.warning('有未儲存變更，請先儲存或切換版本');
    return;
  }
  // schema / global_rule / judgment 無 L1-L3 樹 → 只用 JSON；C-N 預設面板
  mode.value = _NON_TREE_CODES.has(code) ? 'json' : 'panel';
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
    content: '確定將此規則恢復為檔案默認內容？會新增一個版本覆蓋當前（保留歷史，可從「歷史」還原）。',
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

/** 導出全部判決規則為 Excel（C-N 各一分頁 + global 判決總規範；DB active 版本）→ 背景 job + 實時進度下載。 */
function doExport() {
  return runExport(startRulesExport, exportName('判決規則', 'xlsx'));
}

/** 恢復規則配置頁所有規則（schema + 整體規則 + C-N）為檔案默認，各新增版本覆蓋當前（彈窗二次確認，保留歷史）。 */
function doResetAll() {
  Modal.confirm({
    title: '恢復所有規則為默認',
    content: '確定將規則配置頁所有規則（schema / 整體規則 / 歸因分類 C-N）恢復為檔案默認？各新增一個版本覆蓋當前（保留歷史）。',
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
      唯讀模式：規則發布／恢復默認需 judge-rule.version.manage 權限；儲存 / 恢復按鈕已停用（後端亦以 403 兜底）。
    </a-alert>
  <div class="flex min-h-0 flex-1 gap-4">
    <!-- 左：子規則選單 + 全局商品垂直分類篩選（w-52：容 group indent 後仍完整顯示 judgment 判決配置，不截字）-->
    <!-- 面板標題移除：頂部 tab 已是「規則配置」，此處不再重複；全部恢復默認移至右側工具列 -->
    <div class="flex h-full w-52 shrink-0 flex-col gap-3">
      <!-- 兩組：整體配置（schema/global/judgment 純 JSON）+ 歸因分類（C-N 判準樹）-->
      <a-menu
        :selected-keys="[store.activeCode]"
        class="min-h-0 flex-1 overflow-auto rounded-lg border"
        @menu-item-click="pick"
      >
        <a-menu-item-group title="整體配置">
          <a-menu-item key="schema">
            <span class="font-mono text-xs text-[var(--color-text-3)]">schema</span>
            <span class="ml-2">{{ store.labelFor('schema') }}</span>
          </a-menu-item>
          <a-menu-item key="global_rule">
            <span class="font-mono text-xs text-[var(--color-text-3)]">global</span>
            <span class="ml-2">{{ store.labelFor('global_rule') }}</span>
          </a-menu-item>
          <a-menu-item key="judgment">
            <span class="font-mono text-xs text-[var(--color-text-3)]">judgment</span>
            <span class="ml-2">{{ store.labelFor('judgment') }}</span>
          </a-menu-item>
          <a-menu-item key="source_mapping">
            <span class="font-mono text-xs text-[var(--color-text-3)]">upload</span>
            <span class="ml-2">{{ store.labelFor('source_mapping') }}</span>
          </a-menu-item>
        </a-menu-item-group>
        <a-menu-item-group title="歸因分類">
          <a-menu-item v-for="c in domainCodes" :key="c">
            <span class="font-mono text-xs text-[var(--color-text-3)]">{{ c }}</span>
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
          配置歸因列表工具列可選的分類（選項池／總 list）；實際篩選於工具列進行，此處不直接篩資料（複選；至少 1 個）。
        </div>
      </div>
    </div>

    <!-- 右：工具列 + 編輯區（直欄撐滿，編輯區 flex-1 內捲） -->
    <div class="flex min-w-0 flex-1 flex-col">
      <div class="mb-3 flex flex-none items-center gap-3">
        <!-- 當前規則的檢視/編輯模式（面板編輯 / JSON 原始 / 歷史對比——歷史改頁內展示，不再彈窗）；
             面板僅 C-N 歸因樹有（schema/global/judgment 純 JSON），歷史所有規則皆可看 -->
        <a-radio-group v-model="mode" type="button" size="small">
          <a-radio v-if="isDomainTree" value="panel">面板</a-radio>
          <a-radio value="json">JSON</a-radio>
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
          導出規則
        </a-button>
      </div>

      <!-- 導出實時進度：導出進行中才顯示（背景 job + SSE，可停止）-->
      <ExportProgressBar
        v-if="exporting"
        class="mb-3 flex-none"
        label="導出規則"
        :status="exportStatus"
        :processed="exportProgress.processed"
        :total="exportProgress.total"
        :pct="exportPct"
        @cancel="cancelExport"
      />

      <!-- 當前規則標頭：碼 + 名稱（左）＋ 針對「當前規則」的恢復默認 / 儲存（右），明示只作用於選中的這條 -->
      <div v-if="isOwnRule" class="mb-2 flex flex-none items-center gap-2">
        <span class="rounded bg-[rgb(var(--primary-1))] px-2 py-0.5 font-mono text-xs font-semibold text-[rgb(var(--primary-6))]">
          {{ store.activeCode }}
        </span>
        <span class="text-sm font-medium text-[var(--color-text-1)]">{{ store.labelFor(store.activeCode) }}</span>
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
          @click="(saveOpen = true)"
          >儲存</a-button
        >
      </div>

      <!-- 編輯區：撐滿剩餘高度，內部各自捲動 -->
      <div v-if="isOwnRule" class="min-h-0 flex-1">
        <StateGuard :loading="store.loading" :error="store.error">
          <!-- 歷史模式：頁內對比恢復面板（依 activeCode 重掛，切規則即重載該規則歷史）-->
          <RuleHistoryPanel v-if="mode === 'history'" :key="`hist-${store.activeCode}`" class="h-full" />
          <!-- 只有真正的 C-N 歸因分類（isDomainTree）＋面板模式才走 RuleTreePanel；其餘（schema/global/
               judgment，及被抽屜共用 store 帶入的 product_vertical）一律 JsonEditor，歸因 schema 也僅對 C-N 套用 -->
          <RuleTreePanel
            v-else-if="mode === 'panel' && isDomainTree && store.edited"
            :key="editorKey"
            class="h-full"
            :content="store.edited"
            @change="onChange"
          />
          <JsonEditor
            v-else-if="store.edited"
            :key="editorKey"
            class="h-full"
            fill
            :json="store.edited"
            :schema="isDomainTree ? (schemaContent ?? undefined) : undefined"
            :mode="jsonEditorMode"
            @change="onChange"
          />
        </StateGuard>
      </div>
      <!-- 抽屜帶入 product_vertical（本頁不管理）→ 顯示提示，不重複渲染編輯器 -->
      <div
        v-else
        class="flex min-h-0 flex-1 items-center justify-center text-sm text-[var(--color-text-3)]"
      >
        商品垂直分類請於「配置 › 商品垂直分類」抽屜編輯，此頁不重複管理。
      </div>
    </div>

    <!-- 存檔備註 -->
    <a-modal v-model:visible="saveOpen" title="存入 PostgreSQL" :confirm-loading="saving" @ok="doSave">
      <a-textarea v-model="saveNote" placeholder="本次修改備註（選填）" :auto-size="{ minRows: 2 }" />
    </a-modal>
  </div>
  </div>
</template>
