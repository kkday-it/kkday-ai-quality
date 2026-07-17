<script setup lang="ts">
/**
 * 初判 Prompt 草稿編輯抽屜（沙盒閉環第一步）：載入該 prompt 的 DB 草稿（無則以指定分叉基準版本
 * 內容為底建新草稿）→ md 編輯（複用 PromptEditor：md-editor-v3 左寫右渲染）→ 驗證（dry-run，
 * 不落庫）／儲存草稿（寬鬆，可存半成品）／刪除草稿。草稿與版本表分離：存草稿不影響判決；
 * 送測（沙盒雙跑對比）與入庫（saveRule）才強驗。
 */
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import {
  deleteRuleDraft,
  getRule,
  getRuleDraft,
  getRuleVersion,
  saveRuleDraft,
  validateRuleText,
} from '@/api/judgeRules.api';
import { fmtDt } from '../utils';

// md-editor-v3 較重，點開抽屜才載（比照 RuleManager 懶載 PromptEditor 慣例）
const PromptEditor = defineAsyncComponent(() => import('./PromptEditor.vue'));

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 編輯目標 rule_code（prompt_polarity / prompt_C-1..C-6）。 */
  code: string;
  /** 顯示名（情緒傾向／商品內容…）。 */
  label: string;
  /** 無既有草稿時，新草稿的分叉基準版本（以該版內容為底）。 */
  baseVersion: number;
  /** 現行 active 版本號（stale 提示用）。 */
  activeVersion?: number;
}>();
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
  /** 草稿存檔/刪除後通知父層刷新草稿存在狀態（picker badge/選項）。 */
  (e: 'changed'): void;
}>();

const loading = ref(false);
const saving = ref(false);
const validating = ref(false);
/** 編輯中 content（{_meta, text}；PromptEditor 只改 text，_meta 原樣帶回）。 */
const content = ref<Record<string, unknown>>({});
const editedText = ref('');
const editedValid = ref(false);
/** 既有草稿 meta（null＝本次為新草稿）。 */
const draftBase = ref<number | null>(null);
const draftUpdatedBy = ref('');
const draftUpdatedAt = ref('');
const dirty = ref(false);

/** 分叉基準已落後 active（stale）：仍可編輯送測，入庫前自行斟酌是否先對齊。 */
const isStale = computed(
  () =>
    props.activeVersion != null &&
    (draftBase.value ?? props.baseVersion) < props.activeVersion,
);

/** 開啟時載入：既有草稿優先；無草稿 → 以分叉基準版本內容為底。 */
watch(
  () => props.visible,
  async (v) => {
    if (!v) return;
    loading.value = true;
    dirty.value = false;
    try {
      const { draft } = await getRuleDraft(props.code);
      if (draft) {
        content.value = draft.content;
        draftBase.value = draft.base_version;
        draftUpdatedBy.value = draft.updated_by ?? '';
        draftUpdatedAt.value = draft.updated_at ?? '';
      } else {
        const base =
          props.baseVersion && props.baseVersion !== props.activeVersion
            ? await getRuleVersion(props.code, props.baseVersion)
            : await getRule(props.code);
        content.value = base.content;
        draftBase.value = null;
        draftUpdatedBy.value = '';
        draftUpdatedAt.value = '';
      }
      editedText.value = typeof content.value.text === 'string' ? content.value.text : '';
      editedValid.value = editedText.value.trim().length > 0;
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '載入草稿失敗');
      emit('update:visible', false);
    } finally {
      loading.value = false;
    }
  },
);

function onEditorChange(payload: { json: unknown; valid: boolean }): void {
  const next = payload.json as Record<string, unknown>;
  editedText.value = typeof next.text === 'string' ? next.text : '';
  editedValid.value = payload.valid;
  dirty.value = true;
}

/** dry-run 驗證（不落庫）：後端 prompt_source.validate 權威閘。 */
async function validate(): Promise<void> {
  validating.value = true;
  try {
    const r = await validateRuleText(props.code, editedText.value);
    if (r.valid) Message.success('驗證通過（三節/Schema/佔位符/Taxonomy 皆合法）');
    else Message.error(`驗證不過：${r.error ?? ''}`);
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '驗證失敗');
  } finally {
    validating.value = false;
  }
}

async function save(): Promise<void> {
  if (!editedValid.value) {
    Message.warning('草稿內容不可為空');
    return;
  }
  saving.value = true;
  try {
    const base = draftBase.value ?? props.baseVersion;
    await saveRuleDraft(props.code, { ...content.value, text: editedText.value }, base);
    draftBase.value = base;
    dirty.value = false;
    Message.success('草稿已儲存（未入庫，不影響正式判決）');
    emit('changed');
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '儲存草稿失敗');
  } finally {
    saving.value = false;
  }
}

function removeDraft(): void {
  Modal.confirm({
    title: '刪除草稿',
    content: `確定捨棄「${props.label}」的草稿？此操作不影響已入庫的版本。`,
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
        await deleteRuleDraft(props.code);
        Message.success('草稿已刪除');
        emit('changed');
        emit('update:visible', false);
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '刪除草稿失敗');
      }
    },
  });
}

/** 有未儲存變更時關閉前確認（避免手滑丟編輯）。 */
function onCancel(): void {
  if (!dirty.value) {
    emit('update:visible', false);
    return;
  }
  Modal.confirm({
    title: '關閉草稿編輯',
    content: '有未儲存的變更，關閉將丟失。確定關閉？',
    onOk: () => emit('update:visible', false),
  });
}
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="1040"
    :mask-closable="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="onCancel"
  >
    <template #title>
      草稿編輯 · {{ label }}
      <span class="ml-2 text-xs font-normal text-[var(--color-text-3)]">
        {{ draftBase != null ? `基於 v${draftBase}` : `新草稿（基於 v${baseVersion}）` }}
        <template v-if="draftUpdatedAt"
          >· {{ draftUpdatedBy }} 最後編輯於 {{ fmtDt(draftUpdatedAt) }}</template
        >
      </span>
    </template>

    <a-alert v-if="isStale" type="warning" class="mb-2 flex-none">
      active 版本已前進至 v{{ activeVersion }}（草稿分叉自 v{{ draftBase ?? baseVersion }}）——
      入庫前建議先對照最新版差異，避免覆蓋他人變更。
    </a-alert>

    <a-spin :loading="loading" class="flex min-h-0 flex-1 flex-col overflow-hidden">
      <PromptEditor v-if="visible && !loading" :content="content" @change="onEditorChange" />
    </a-spin>

    <template #footer>
      <div class="flex w-full items-center gap-2">
        <a-button
          v-if="draftBase != null"
          size="small"
          type="outline"
          status="danger"
          @click="removeDraft"
          >刪除草稿</a-button
        >
        <div class="flex-1" />
        <a-button size="small" :loading="validating" type="dashed" @click="validate"
          >驗證</a-button
        >
        <a-button size="small" @click="onCancel">關閉</a-button>
        <a-button type="primary" size="small" :loading="saving" :disabled="!dirty" @click="save"
          >儲存草稿</a-button
        >
      </div>
    </template>
  </a-drawer>
</template>
