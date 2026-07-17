<script setup lang="ts">
/**
 * 草稿採納入庫確認抽屜（沙盒閉環最後一步）：顯示「現行 active ↔ 草稿（本次測試快照）」的
 * md 行級 diff（公共元件 MdTextDiff）→ 確認後 saveRule 入庫成新 active 版（note 自動帶測試
 * run_id 溯源）→ 刪除該 rule_code 的 DB 草稿（閉環收尾）。入庫即 active：後端存檔即熱重載生效。
 */
import { ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { deleteRuleDraft, getRule, saveRule } from '@/api/judgeRules.api';
import { MdTextDiff } from '@/components';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 採納目標 rule_code（prompt_*）。 */
  code: string;
  /** 顯示名。 */
  label: string;
  /** 要入庫的草稿 md 全文（來自測試 run 的 drafts 快照——採納「驗證過的內容」而非草稿最新態）。 */
  draftText: string;
  /** 溯源 run_id（自動寫入版本 note）。 */
  runId: string;
}>();
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
  /** 入庫成功（新版本號）→ 父層刷新版本清單/草稿狀態。 */
  (e: 'adopted', payload: { code: string; version: number }): void;
}>();

const loading = ref(false);
const saving = ref(false);
const activeText = ref('');
const activeVersion = ref<number | null>(null);
/** 現行 active content（_meta 原樣沿用，只換 text 入庫）。 */
let activeContent: Record<string, unknown> = {};
const note = ref('');

watch(
  () => props.visible,
  async (v) => {
    if (!v) return;
    loading.value = true;
    try {
      const r = await getRule(props.code);
      activeContent = r.content;
      activeText.value = typeof r.content.text === 'string' ? r.content.text : '';
      activeVersion.value = r.version;
      note.value = `採納沙盒草稿（run ${props.runId}）`;
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '載入現行版本失敗');
      emit('update:visible', false);
    } finally {
      loading.value = false;
    }
  },
);

async function adopt(): Promise<void> {
  saving.value = true;
  try {
    const res = await saveRule(props.code, { ...activeContent, text: props.draftText }, note.value);
    // 入庫成功即清草稿（閉環收尾）；清失敗不影響已入庫事實，僅提示
    try {
      await deleteRuleDraft(props.code);
    } catch {
      Message.warning('已入庫，但草稿清理失敗——可至版本選擇器手動刪除');
    }
    Message.success(`已入庫為 v${res.version} 並設為 active（初判即時生效）`);
    emit('adopted', { code: props.code, version: res.version });
    emit('update:visible', false);
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '入庫失敗');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="900"
    unmount-on-close
    ok-text="確認入庫（即成 active）"
    :ok-loading="saving"
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @ok="adopt"
    @cancel="emit('update:visible', false)"
  >
    <template #title>採納草稿入庫 · {{ label }}</template>
    <a-spin :loading="loading" class="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div class="mb-2 flex-none text-xs text-[var(--color-text-3)]">
        入庫後立即成為 active 新版本（正式初判即時採用）；內容＝本次測試驗證過的草稿快照。
      </div>
      <MdTextDiff
        class="min-h-0 flex-1"
        :old-text="activeText"
        :new-text="draftText"
        :old-label="`現行 active（v${activeVersion ?? '?'}）`"
        new-label="草稿（本次測試快照）"
      />
      <div class="mt-2 flex flex-none items-center gap-2">
        <span class="shrink-0 text-xs text-[var(--color-text-3)]">版本備註</span>
        <a-input v-model="note" size="small" />
      </div>
    </a-spin>
  </a-drawer>
</template>
