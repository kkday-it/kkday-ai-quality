<script setup lang="ts">
/**
 * 「配置」抽屜 › 商品垂直分類 tab：分組（Tour / Exp / Charter / Tix …）↔ CATEGORY 代碼映射的維護入口。
 *
 * 商品垂直分類屬全域配置（seed = config/global/product_vertical.json），非歸因判準；由此抽屜**獨立維護**。
 * **不共用 judgeRules store**——那是 singleton，其 activeCode 被規則配置頁同時消費，共用會令規則頁背景
 * 誤渲染本規則。改用隔離的 useProductVerticalRule composable（自己的 local state），走同一後端版本化
 * 管線（存檔 / 歷史 / 恢復默認），與規則頁完全解耦。
 */
import { ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import StateGuard from '@/components/StateGuard.vue';
import { useVerticalFilterStore } from '@/stores/verticalFilter.store';
import { useProductVerticalRule } from '../composables';
import ProductVerticalPanel from './ProductVerticalPanel.vue';
import RuleHistoryModal from './RuleHistoryModal.vue';
import { versionLabel } from '../utils';

/** active：所屬 tab 是否為當前選中——僅在啟用時才載入（延後到真正要用）。 */
const props = defineProps<{ active?: boolean }>();

const LABEL = '商品垂直分類';
const {
  code,
  edited,
  version,
  createdAt,
  loading,
  error,
  dirty,
  load,
  setEdited,
  save,
  resetDefault,
} = useProductVerticalRule();

const loaded = ref(false);
const saving = ref(false);
const saveOpen = ref(false);
const saveNote = ref('');
const historyOpen = ref(false);

watch(
  () => props.active,
  async (on) => {
    if (on && !loaded.value) {
      await load();
      loaded.value = true;
    }
  },
  { immediate: true },
);

/** 分組表單回報變更 → 寫入隔離編輯態（合法才更新）。 */
function onChange(payload: { json: unknown; valid: boolean }) {
  setEdited(payload.json, payload.valid);
}

async function doSave() {
  saving.value = true;
  try {
    await save(saveNote.value.trim());
    // 主動刷新全局垂直分類選項（順序/分組即時反映到已掛載的歸因列表/縱覽工具列，免切頁重載）
    await useVerticalFilterStore().loadOptions();
    Message.success('已存入 PostgreSQL（新版本）');
    saveOpen.value = false;
    saveNote.value = '';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '存檔失敗');
  } finally {
    saving.value = false;
  }
}

/** 恢復為檔案默認（config/global/product_vertical.json）；二次確認，保留歷史可還原。 */
function doReset() {
  Modal.confirm({
    title: '恢復默認',
    content:
      '確定將商品垂直分類恢復為檔案默認內容？會新增一個版本覆蓋當前（保留歷史，可從「歷史」還原）。',
    okText: '恢復默認',
    cancelText: '取消',
    onOk: async () => {
      try {
        await resetDefault();
        await useVerticalFilterStore().loadOptions();
        Message.success('已恢復默認（檔案內容）');
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '恢復失敗');
      }
    },
  });
}

/** 歷史恢復後：重載當前內容 + 版本號 + 刷新全局選項。 */
async function onRestored() {
  await load();
  await useVerticalFilterStore().loadOptions();
}
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="mb-3 flex flex-none items-center gap-3">
      <span class="text-sm font-medium">{{ LABEL }}</span>
      <span v-if="version != null" class="text-xs text-[var(--color-text-3)]">
        {{ versionLabel(createdAt, version) }}
        <span v-if="dirty" class="ml-1 text-[rgb(var(--warning-6))]">● 未存</span>
      </span>
      <div class="flex-1" />
      <a-button size="small" @click="historyOpen = true">歷史</a-button>
      <a-button size="small" @click="doReset">恢復默認</a-button>
      <a-button type="primary" size="small" :disabled="!dirty" @click="saveOpen = true">
        儲存
      </a-button>
    </div>

    <div class="min-h-0 flex-1">
      <StateGuard :loading="loading" :error="error">
        <ProductVerticalPanel
          v-if="edited"
          :key="`pv-${version ?? 0}`"
          class="h-full"
          :content="edited"
          @change="onChange"
        />
      </StateGuard>
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

    <!-- 歷史對比恢復（code 驅動·恢復後 onRestored 重載）-->
    <RuleHistoryModal
      v-model:visible="historyOpen"
      :code="code"
      :label="LABEL"
      @restored="onRestored"
    />
  </div>
</template>
