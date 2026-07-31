<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { PERM } from '@/api';
import { StateGuard } from '@/components';
import { usePermission } from '@/composables/usePermission';
import { useSettingsConfigsStore } from '@/stores';
import { LlmConnectionCard, LlmModelConfigList } from '../components';
import { PROVIDERS } from '../constants';

// 🤖 LLM 設定 tab（分頁名刻意不叫「LLM 連線」——它裝的不只連線，還有整個模型配置庫）。
// **每個供應商一個分頁**，分頁內兩層——
//   ① 連線（base_url + token，per-provider 各一條，全項目共用；改動需 settings.llm-config.manage）
//   ② 模型配置（該供應商旗下的具名配置庫；全域共用、日常操作免特殊權限）
// 分頁標題帶連線狀態點：三家連線卡從「同時可見」變成分頁切換後，「誰配了 token」仍要一眼可見。
const store = useSettingsConfigsStore();
const { can } = usePermission();
/** 是否可改連線/測試（settings.llm-config.manage，僅 grants）；無此權限僅能檢視狀態點，不能編輯連線。 */
const canManage = computed(() => can(PERM.settingsLlmConfigManage));
const activeProvider = ref(PROVIDERS[0]?.id ?? 'openai');

onMounted(() => {
  store.loadAll();
  if (canManage.value) store.loadSecrets(); // 明文回填僅授權者需要，無權限就不打 /raw（省一次 403）
});

const onSave = (provider: string, payload: { baseUrl: string; token?: string }) =>
  store.saveLlmConnection(provider, payload.baseUrl, payload.token);
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <a-alert v-if="!canManage" type="info" class="mb-3">
        僅檢視連線狀態；如需修改連線（base_url / token），請聯繫有 LLM 連線管理權限的同事。
        模型配置不受此限制，可自由新增與調整。
      </a-alert>

      <!-- 內層刻意用裸 a-tabs 而非 StickyTabs：外層 SettingsDrawer 的 StickyTabs 已經提供了唯一的
           捲動容器，這裡再套一層 flex-1 + overflow 會變成雙層捲軸（見 .claude/rules/frontend-vue.md
           「消費端不得再套 overflow-auto 包住整個 StickyTabs」）。分頁列只有三顆、位於面板頂端，
           隨內容捲走的成本遠低於雙捲軸。 -->
      <a-tabs v-model:active-key="activeProvider" type="card-gutter" size="small">
        <a-tab-pane v-for="p in PROVIDERS" :key="p.id">
          <template #title>
            <span class="inline-flex items-center gap-1.5">
              <span
                class="inline-block h-2 w-2 rounded-full"
                :class="
                  store.providerHasToken[p.id]
                    ? 'bg-[rgb(var(--green-6))]'
                    : 'bg-[rgb(var(--gray-4))]'
                "
              />
              {{ p.short_label }}
            </span>
          </template>

          <div class="pt-2">
            <LlmConnectionCard
              :provider="p.id"
              :connection="store.llmConnections[p.id]"
              :token-known="store.llmTokens[p.id] ?? ''"
              :has-token="!!store.providerHasToken[p.id]"
              :can-manage="canManage"
              @save="(payload) => onSave(p.id, payload)"
            />

            <a-divider orientation="left" :margin="20">
              <span class="text-sm font-medium">模型配置</span>
            </a-divider>
            <LlmModelConfigList :provider="p.id" />
          </div>
        </a-tab-pane>
      </a-tabs>

      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[var(--color-text-3)]">
        分頁標題的圓點＝該供應商是否已配 API token（綠＝已配、灰＝未配）。各功能區（初判分類、
        Prompt 調試台、Prompt 測試沙盒、AI
        定點改寫）只需在該頁面上選一個模型配置，不必再逐項調旋鈕，
        選了即時生效並自動保存，這裡不另設集中綁定入口。⚠️ 綁定是全團隊共用的一份：你換掉某一區的
        配置，同事下次進頁面也會是新的。
      </p>
    </div>
  </StateGuard>
</template>
