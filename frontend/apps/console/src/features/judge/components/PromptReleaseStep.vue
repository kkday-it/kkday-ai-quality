<script setup lang="ts">
/**
 * 流水線步驟④「定案發布」：把驗證過的候選 Prompt 存成草稿、再升為正式版。
 *
 * 為什麼「存為新草稿」在這裡而不是在「AI 改寫」步驟：它是**定案**動作，而定案的前提是③的回歸
 * 結果。放在②會強迫使用者「回歸跑完 → 倒回上一步存檔」——這正是使用者回報「流程不夠清晰」時
 * 描述的那條 Z 字形路徑。
 *
 * 兩顆按鈕的門檻刻意不同：草稿不影響線上口徑，改壞了也該讓人留存半成品；把改壞的版本推上線
 * 才是要擋的那件事。
 */
import { computed } from 'vue';
import { MdTextDiff } from '@/components';
import type { PromptReleaseMeta } from '@/api';
import { publishBlockedReason } from '../utils';
import { usePromptRelease } from '../composables';

const props = defineProps<{
  /** ②套用補丁後的候選全文（要被存檔的內容）。 */
  candidatePrompt: string;
  /** 當前正式版全文（diff 左側；沒有正式版時為空）。 */
  releasePrompt: string;
  /** 當前正式版名（顯示用）。 */
  activeRelease: string;
  /** 正式版清單——名稱建議與撞名檢查用。 */
  releases: PromptReleaseMeta[];
  /** ③回歸判定被改壞的欄數。 */
  brokenCount: number;
  /** ③回歸判定修好的欄數。 */
  fixedCount: number;
  /** 已存出的草稿名；空＝還沒存過。 */
  savedDraft: string;
  /** 存檔中。 */
  saving: boolean;
}>();

const emit = defineEmits<{
  /** 請上層執行「存為新草稿」（存檔動作屬於 `usePromptRevise`，由抽屜持有）。 */
  (e: 'saveDraft'): void;
  /** 升版或回退成功（帶新的 active 正式版名）：上層需重載 defaults。 */
  (e: 'promoted', name: string): void;
}>();

const release = usePromptRelease({
  releases: () => props.releases,
  onDone: (name) => emit('promoted', name),
});

/** 升版被擋住的原因（空＝可升）。 */
const blockedReason = computed(() =>
  publishBlockedReason(props.brokenCount, !!props.savedDraft),
);

/** 這支草稿是不是已經升版過了（重複升同一份沒有意義）。 */
const alreadyPromoted = computed(
  () => !!props.savedDraft && release.promotedDrafts.value.has(props.savedDraft),
);

const canPromote = computed(
  () => !blockedReason.value && !alreadyPromoted.value && release.canManage.value,
);

/** 升版鈕 disabled 時的 tooltip：讓「為什麼不能點」永遠可見。 */
const promoteTooltip = computed(() => {
  if (alreadyPromoted.value) return `草稿 ${props.savedDraft} 已升版過`;
  if (blockedReason.value) return blockedReason.value;
  if (!release.canManage.value) return '需要「判準版本管理」權限';
  return '';
});
</script>

<template>
  <div class="flex flex-col gap-3">
    <section class="release-card">
      <div class="mb-2 text-sm font-semibold text-[#1d2129]">驗證結果</div>
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <a-tag color="green" size="small">修好 {{ fixedCount }} 欄</a-tag>
        <a-tag :color="brokenCount ? 'red' : 'gray'" size="small">改壞 {{ brokenCount }} 欄</a-tag>
      </div>
      <a-alert v-if="brokenCount" type="error" class="mt-2">
        有 {{ brokenCount }} 個原本判對的欄被改壞——可以先存成草稿留存，但<b>不該升為正式版</b>。
        建議回②取消掉相關補丁後重新驗證。
      </a-alert>
    </section>

    <section class="release-card">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold text-[#1d2129]">存為新草稿</span>
          <a-tag v-if="savedDraft" color="arcoblue" size="small">{{ savedDraft }}</a-tag>
        </div>
        <a-button type="primary" size="small" :loading="saving" @click="emit('saveDraft')">
          {{ savedDraft ? '再存一次' : '存為新草稿' }}
        </a-button>
      </div>
      <p class="text-[11px] leading-relaxed text-[#86909c]">
        草稿只是存檔，<b>不改變線上口徑</b>——改壞了也可以先存起來留存。要上線得再按下方「升為正式版」。
      </p>
    </section>

    <section class="release-card">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold text-[#1d2129]">升為正式版</span>
          <span class="text-[11px] text-[#86909c]">
            當前線上口徑：{{ activeRelease || '尚無正式版' }}
          </span>
        </div>
        <a-tooltip :content="promoteTooltip" :disabled="!promoteTooltip">
          <!-- disabled 的按鈕不觸發 tooltip，故包一層 span 承接 hover -->
          <span>
            <a-button
              type="primary"
              status="success"
              size="small"
              :disabled="!canPromote"
              @click="release.openPromote(savedDraft)"
            >
              升為正式版
            </a-button>
          </span>
        </a-tooltip>
      </div>
      <p class="text-[11px] leading-relaxed text-[#86909c]">
        升版後跑批與調試台的「正式」側立即改用這一版。建議先在下方比對確認差異。
      </p>
    </section>

    <section class="release-card">
      <div class="mb-2 text-sm font-semibold text-[#1d2129]">
        與當前正式版比對
        <span class="ml-1 text-[11px] font-normal text-[#86909c]">
          左：{{ activeRelease || '（尚無正式版）' }} · 右：本次候選
        </span>
      </div>
      <div class="diff-box">
        <MdTextDiff
          :old-text="releasePrompt"
          :new-text="candidatePrompt"
          :old-label="activeRelease || '尚無正式版'"
          new-label="本次候選"
        />
      </div>
    </section>

    <!-- 升版確認：影響線上口徑，故用表單式 modal（與版本列表抽屜同一套判準，邏輯共用 composable） -->
    <a-modal
      v-model:visible="release.promoteVisible.value"
      title="升為正式版"
      :ok-loading="release.promoting.value"
      :ok-button-props="{ disabled: !release.canConfirmPromote.value }"
      ok-text="設為正式版"
      cancel-text="取消"
      @ok="release.confirmPromote"
    >
      <a-form
        :model="{
          releaseName: release.releaseName.value,
          releaseNote: release.releaseNote.value,
        }"
        layout="vertical"
      >
        <a-form-item label="來源草稿">
          <span class="font-medium">{{ release.sourceDraft.value }}</span>
        </a-form-item>
        <a-form-item
          label="正式版名稱"
          :validate-status="
            !release.nameValid.value || release.nameTaken.value ? 'error' : undefined
          "
          :help="
            release.nameTaken.value
              ? '此名稱已存在（正式版不覆寫，請換名）'
              : !release.nameValid.value
                ? '僅允許英數與 . _ -，首字元須為英數'
                : ''
          "
        >
          <a-input v-model="release.releaseName.value" allow-clear />
        </a-form-item>
        <a-form-item
          label="上線理由"
          :validate-status="release.noteValid.value ? undefined : 'error'"
          :help="release.noteValid.value ? '' : '請寫明這版為何上線，供日後回查'"
        >
          <a-textarea v-model="release.releaseNote.value" :auto-size="{ minRows: 2, maxRows: 4 }" />
        </a-form-item>
      </a-form>
      <div class="text-xs text-[#86909c]">
        確認後 <b>{{ release.releaseName.value || '—' }}</b> 立即成為線上唯一口徑（前一版
        {{ activeRelease || '—' }}）。
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.release-card {
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 12px;
}
/* 固定高度讓 diff 自帶捲軸：全文動輒數萬字，讓它撐開會把上方三張卡片推到捲軸深處 */
.diff-box {
  height: 380px;
  overflow: hidden;
}
</style>
