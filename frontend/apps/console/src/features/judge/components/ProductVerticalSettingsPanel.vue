<script setup lang="ts">
/**
 * 「配置」抽屜 › 商品垂直分類 tab：分組（Tour / Exp / Charter / Tix …）↔ CATEGORY 代碼映射的維護入口。
 *
 * 商品垂直分類屬全域配置（seed = config/global/product_vertical.json），非歸因判準；改由此抽屜維護，
 * 不再放歸因分類規則管理頁。編輯 UI 沿用 {@link ProductVerticalPanel}（分組表單），存檔 / 歷史 / 恢復默認
 * 直接複用 judgeRules 版本化 store（與規則管理同一後端管線），避免平行造第二套版本化邏輯。
 */
import { computed, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import StateGuard from '@/components/StateGuard.vue';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { useVerticalFilterStore } from '@/stores/verticalFilter.store';
import ProductVerticalPanel from './ProductVerticalPanel.vue';
import RuleHistoryModal from './RuleHistoryModal.vue';
import { versionLabel } from '../utils';

/** active：所屬 tab 是否為當前選中——僅在啟用時才載入，避免抽屜一開就搶佔共用 store.activeCode。 */
const props = defineProps<{ active?: boolean }>();
const store = useJudgeRulesStore();

const CODE = 'product_vertical';
const loaded = ref(false);
const saving = ref(false);
const saveOpen = ref(false);
const saveNote = ref('');
const historyOpen = ref(false);

/** 首次啟用該 tab 時才 selectRule（延後到真正要用，降低與規則管理頁共用 store 的干擾）。 */
watch(
  () => props.active,
  async (on) => {
    if (on && !loaded.value) {
      await store.loadList(); // 需 meta 才有 labelFor / currentMeta 版本號
      await store.selectRule(CODE);
      loaded.value = true;
    }
  },
  { immediate: true },
);

/** 僅當 store 當前選中確為 product_vertical 時才渲染編輯器（防與規則管理頁切走後渲染到別的 rule）。 */
const isCurrent = computed(() => store.activeCode === CODE);

/** 分組表單回報變更 → 寫入 store（合法才更新編輯態）。 */
function onChange(payload: { json: unknown; valid: boolean }) {
  store.setEdited(payload.json, payload.valid);
}

async function doSave() {
  saving.value = true;
  try {
    await store.save(saveNote.value.trim());
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
    content: '確定將商品垂直分類恢復為檔案默認內容？會新增一個版本覆蓋當前（保留歷史，可從「歷史」還原）。',
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
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="mb-3 flex flex-none items-center gap-3">
      <span class="text-sm font-medium">商品垂直分類</span>
      <span v-if="isCurrent && store.currentMeta" class="text-xs text-[var(--color-text-3)]">
        {{ versionLabel(store.currentMeta.created_at, store.currentMeta.version) }}
        <span v-if="store.dirty" class="ml-1 text-[rgb(var(--warning-6))]">● 未存</span>
      </span>
      <div class="flex-1" />
      <a-button size="small" :disabled="!isCurrent" @click="(historyOpen = true)">歷史</a-button>
      <a-button size="small" :disabled="!isCurrent" @click="doReset">恢復默認</a-button>
      <a-button type="primary" size="small" :disabled="!store.dirty" @click="(saveOpen = true)">
        儲存
      </a-button>
    </div>

    <div class="min-h-0 flex-1">
      <StateGuard :loading="store.loading" :error="store.error">
        <ProductVerticalPanel
          v-if="isCurrent && store.edited"
          :key="`${store.activeCode}-${store.currentMeta?.version ?? 0}`"
          class="h-full"
          :content="store.edited"
          @change="onChange"
        />
      </StateGuard>
    </div>

    <!-- 存檔備註 -->
    <a-modal v-model:visible="saveOpen" title="存入 PostgreSQL" :confirm-loading="saving" @ok="doSave">
      <a-textarea v-model="saveNote" placeholder="本次修改備註（選填）" :auto-size="{ minRows: 2 }" />
    </a-modal>

    <!-- 歷史對比恢復（複用規則管理同款 modal，讀 store.activeCode = product_vertical 的歷史） -->
    <RuleHistoryModal v-model:visible="historyOpen" />
  </div>
</template>
