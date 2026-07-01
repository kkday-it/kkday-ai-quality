<script setup lang="ts">
/**
 * 判決規則管理（config/ai_judge 7 域 + schema）：面板 / JSON 雙編 + schema 查改 +
 * 歷史對比恢復 + 恢復默認 + PostgreSQL 版本化。左選子規則、右編輯、工具列操作。
 */
import { computed, onMounted, ref, shallowRef } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import JsonEditor from '@/components/JsonEditor.vue';
import StateGuard from '@/components/StateGuard.vue';
import { getRule } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import RuleTreePanel from '../components/RuleTreePanel.vue';
import RuleHistoryModal from '../components/RuleHistoryModal.vue';

const store = useJudgeRulesStore();
// 歸因分類（C-N，schema 另置頂）；code 與顯示名皆來自後端 meta（label SSOT），不再讀前端靜態表。
const domainCodes = computed(() =>
  store.metas.filter((m) => m.rule_code !== 'schema').map((m) => m.rule_code),
);
const mode = ref<'panel' | 'json'>('panel');
const historyOpen = ref(false);
const saveOpen = ref(false);
const saveNote = ref('');
const saving = ref(false);

// schema content（給 JSON 模式即時驗證；編輯 schema 自身時不套）
const schemaContent = shallowRef<Record<string, unknown> | null>(null);

const isSchema = computed(() => store.activeCode === 'schema');
// schema 無 L1›L2›L3 樹，「面板」改用 JsonEditor 結構化 tree 模式；JSON＝text 模式
const jsonEditorMode = computed<'tree' | 'text'>(() =>
  isSchema.value && mode.value === 'panel' ? 'tree' : 'text',
);
// 重掛 key：rule + active 版本 + 模式 → 切換時 editor 以新內容重置
const editorKey = computed(() => `${store.activeCode}-${store.currentMeta?.version ?? 0}-${mode.value}`);

onMounted(async () => {
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
  // schema 無樹狀結構 → 只用 JSON；C-N 預設面板
  mode.value = code === 'schema' ? 'json' : 'panel';
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

/** 恢復所有歸因分類（C-N）為檔案默認，各新增版本覆蓋當前（彈窗二次確認，保留歷史）。 */
function doResetAll() {
  Modal.confirm({
    title: '恢復所有分類為默認',
    content: '確定將所有歸因分類恢復為檔案默認？各分類將新增一個版本覆蓋當前（保留歷史）。',
    okText: '全部恢復',
    cancelText: '取消',
    onOk: async () => {
      try {
        const res = await store.resetAllDefault();
        const skip = res.skipped?.length ? `，略過 ${res.skipped.join('、')}（無默認檔）` : '';
        Message.success(`已恢復 ${res.reset.length} 個歸因分類為默認（各新增版本）${skip}`);
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '恢復失敗');
      }
    },
  });
}
</script>

<template>
  <div class="flex h-full gap-4">
    <!-- 左：子規則選單（schema 結構規格 · 規則分類 C-N〔可批次恢復默認〕） -->
    <a-menu
      :selected-keys="[store.activeCode]"
      class="h-full w-44 shrink-0 overflow-auto rounded-lg border"
      @menu-item-click="pick"
    >
      <a-menu-item key="schema">
        <span class="font-mono text-xs text-[var(--color-text-3)]">schema</span>
        <span class="ml-2">{{ store.labelFor('schema') }}</span>
      </a-menu-item>
      <a-menu-item-group>
        <template #title>
          <div class="flex items-center justify-between pr-1">
            <span>歸因分類</span>
            <a-button size="mini" type="text" @click.stop="doResetAll">恢復默認</a-button>
          </div>
        </template>
        <a-menu-item v-for="c in domainCodes" :key="c">
          <span class="font-mono text-xs text-[var(--color-text-3)]">{{ c }}</span>
          <span class="ml-2">{{ store.labelFor(c) }}</span>
        </a-menu-item>
      </a-menu-item-group>
    </a-menu>

    <!-- 右：工具列 + 編輯區（直欄撐滿，編輯區 flex-1 內捲） -->
    <div class="flex min-w-0 flex-1 flex-col">
      <div class="mb-3 flex flex-none items-center gap-3">
        <!-- schema 僅 JSON，不顯示面板/JSON 切換 -->
        <a-radio-group v-if="!isSchema" v-model="mode" type="button" size="small">
          <a-radio value="panel">面板</a-radio>
          <a-radio value="json">JSON</a-radio>
        </a-radio-group>
        <span v-if="store.currentMeta" class="text-xs text-[var(--color-text-3)]">
          v{{ store.currentMeta.version }}
          <span v-if="store.dirty" class="ml-1 text-[rgb(var(--warning-6))]">● 未存</span>
        </span>
        <div class="flex-1" />
        <a-button size="small" @click="(historyOpen = true)">歷史</a-button>
        <a-button size="small" @click="doReset">恢復默認</a-button>
        <a-button
          type="primary"
          size="small"
          :disabled="!store.dirty"
          @click="(saveOpen = true)"
        >
          儲存
        </a-button>
      </div>

      <!-- 編輯區：撐滿剩餘高度，內部各自捲動 -->
      <div class="min-h-0 flex-1">
        <StateGuard :loading="store.loading" :error="store.error">
          <!-- schema 一律 JSON 模式；C-N 依 mode -->
          <RuleTreePanel
            v-if="mode === 'panel' && !isSchema && store.edited"
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
            :schema="isSchema ? undefined : (schemaContent ?? undefined)"
            :mode="jsonEditorMode"
            @change="onChange"
          />
        </StateGuard>
      </div>
    </div>

    <!-- 存檔備註 -->
    <a-modal v-model:visible="saveOpen" title="存入 PostgreSQL" :confirm-loading="saving" @ok="doSave">
      <a-textarea v-model="saveNote" placeholder="本次修改備註（選填）" :auto-size="{ minRows: 2 }" />
    </a-modal>

    <!-- 歷史對比恢復 -->
    <RuleHistoryModal v-model:visible="historyOpen" />
  </div>
</template>
