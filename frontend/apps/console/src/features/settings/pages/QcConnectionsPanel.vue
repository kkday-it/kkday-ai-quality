<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconPlus } from '@arco-design/web-vue/es/icon';
import qcDefaults from '@config/global/qc_db.json';
import { AccordionGroup, StateGuard, StickyTabs } from '@/components';
import { useListDragSort } from '@/composables';
import { useSettingsConfigsStore } from '@/stores';
import { QcConfigCard, QcConfigEditor } from '../components';
import { configStamp } from '../utils';
import type { QcConfig } from '../types';

// 🗄️ QC DB 接口 tab：環境（sit/stage/production）為第一層 Tabs、環境間配置完全隔離；
// 每個環境 tab 內各自維護多套帳號連線卡（對齊 LLM 模型連線的卡片清單交互：inline 新增/編輯/刪除/啟用/拖排）。
// 新增連線直接繼承當前 tab 的環境（編輯器無環境選擇），跨環境殘留從結構上不存在。
// 啟用語義維持全域唯一（抽取一次只打一套庫）；啟用中連線所在環境的 tab 標題帶綠點指示。
const store = useSettingsConfigsStore();
const QC = qcDefaults;
const ENVS = QC.environments;
const DEFAULT_ENV = QC.defaultEnv;
const envOf = (id: string) => ENVS.find((e) => e.id === id) ?? ENVS[0];

onMounted(() => store.loadAll());

/** 當前環境 tab；載入完成後一次性落點到啟用中連線所在環境（之後尊重使用者手動切換）。 */
const activeEnv = ref(DEFAULT_ENV);
/** 啟用中連線所在的環境 id（tab 綠點指示用）；無啟用連線回 ''。 */
const activeQcEnv = computed(
  () => store.qcConfigs.find((c) => c.id === store.activeQcId)?.env ?? '',
);
/** 某環境下的連線清單（環境間完全隔離的檢視邊界）。 */
const configsOf = (env: string) => store.qcConfigs.filter((c) => c.env === env);

// 新增流程：editing 僅持有「新建中的 blank config」（尚未落庫，渲染於其所屬環境 tab 的清單尾端）。
// 既有卡片已「展開即編輯」（QcConfigCard body 直接是表單），不再需要編輯態切換。
const editing = ref<QcConfig | null>(null);
const isEditingNew = computed(
  () => !!editing.value && !store.qcConfigs.some((c) => c.id === editing.value!.id),
);
const blank = (env: string): QcConfig => ({
  id: crypto.randomUUID(),
  label: `QC DB ${configStamp()}`,
  env,
  host: envOf(env).host,
  port: QC.port as number,
  user: '',
  names: [],
  schemas: [QC.schema],
});

// 手風琴受控展開：activeId＝當前展開面板；載入後展開落點環境的第一張。
const activeId = ref('');
const landed = ref(false);
watch(
  () => store.qcConfigs,
  (list) => {
    if (landed.value || !list.length) return;
    landed.value = true;
    activeEnv.value = activeQcEnv.value || DEFAULT_ENV;
    activeId.value = configsOf(activeEnv.value)[0]?.id ?? '';
  },
);
// 切換環境 tab：丟棄未存的新增草稿、展開該環境第一張卡（無卡則全收合）——環境間互不牽動。
watch(activeEnv, (env) => {
  editing.value = null;
  activeId.value = configsOf(env)[0]?.id ?? '';
});
// 單開不變量：展開任一既有面板（activeId 變真值）即丟棄尚未存的「新增」草稿，
// 確保任何交互下只有一個編輯面板展開（新增尾卡在手風琴單開控制之外，須手動互斥）。
watch(activeId, (id) => {
  if (id) editing.value = null;
});
// 新增＝先收合當前面板，於當前環境 tab 尾端展開新增編輯器（環境由 tab 決定）。
const openNew = () => {
  activeId.value = '';
  editing.value = blank(activeEnv.value);
};
const cancel = () => (editing.value = null);
// 既有卡片的編輯器按「取消」＝收合該面板（草稿不落庫）。
const collapse = () => (activeId.value = '');

const onSave = async (payload: { config: QcConfig; password?: string }) => {
  try {
    await store.saveQcConfig(payload.config, payload.password);
    editing.value = null;
    activeId.value = payload.config.id; // 存後展開該套（新增者以正式卡片呈現並保持開啟）
    Message.success('已儲存 QC DB 連線');
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  }
};
const onDelete = async (id: string) => {
  try {
    await store.deleteQcConfig(id);
    if (activeId.value === id) activeId.value = '';
    Message.success('已刪除');
  } catch (e: any) {
    Message.error('刪除失敗：' + (e?.message || e));
  }
};
const onActivate = async (id: string) => {
  try {
    await store.setActiveQc(id);
    Message.success('已設為啟用');
  } catch (e: any) {
    Message.error('切換失敗：' + (e?.message || e));
  }
};

// 卡片拖動排序：各環境 tab 內獨立拖排；subset 新順序寫回全量列表時保留其他環境項的原位，環境間互不影響。
const accordionRefs = reactive<Record<string, InstanceType<typeof AccordionGroup> | null>>({});
const applySubsetOrder = (env: string, subset: QcConfig[]): QcConfig[] => {
  const queue = [...subset];
  return store.qcConfigs.map((c) => (c.env === env ? (queue.shift() ?? c) : c));
};
ENVS.forEach((e) => {
  useListDragSort(
    () => (accordionRefs[e.id]?.$el ?? null) as HTMLElement | null,
    () => configsOf(e.id),
    async (next) => {
      try {
        await store.reorderQcConfigs(applySubsetOrder(e.id, next));
      } catch (err: any) {
        Message.error('排序儲存失敗：' + (err?.message || err));
      }
    },
    { handle: '.drag-handle', draggable: '.arco-collapse-item' },
  );
});
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <!-- 環境第一層 Tabs：每個環境各自一份卡片清單，配置完全隔離；新增按鈕掛 tab 列右側（extra），
           「新增到哪個環境」由當前 active tab 語境決定，不再於按鈕文案重複標註環境 -->
      <StickyTabs v-model:active-key="activeEnv" type="card-gutter" size="small">
        <template #extra>
          <a-button type="primary" size="small" @click="openNew">
            <template #icon><icon-plus /></template>新增連線
          </a-button>
        </template>
        <a-tab-pane v-for="e in ENVS" :key="e.id">
          <template #title>
            <span class="inline-flex items-center gap-1.5">
              <!-- 綠點＝啟用中連線所在環境 -->
              <span
                v-if="activeQcEnv === e.id"
                class="inline-block h-2 w-2 rounded-full bg-[rgb(var(--green-6))]"
              />
              {{ e.label }}
              <span class="text-xs text-[var(--color-text-3)]">{{ configsOf(e.id).length }}</span>
            </span>
          </template>

          <a-empty
            v-if="!configsOf(e.id).length && !(isEditingNew && editing?.env === e.id)"
            :description="`尚無 ${e.label} 連線，點「新增連線」建立（各環境配置完全隔離）`"
          />

          <!-- 手風琴卡片清單（單開 + 預設展開第一張）；新增草稿渲染於其所屬環境的清單尾端 -->
          <AccordionGroup
            v-if="configsOf(e.id).length || (isEditingNew && editing?.env === e.id)"
            :ref="(el: any) => (accordionRefs[e.id] = el)"
            v-model:active="activeId"
          >
            <QcConfigCard
              v-for="c in configsOf(e.id)"
              :key="c.id"
              :config="c"
              :item-key="c.id"
              :active="c.id === store.activeQcId"
              :expanded="activeId === c.id"
              :password="store.qcPasswords[c.id] ?? ''"
              @delete="onDelete(c.id)"
              @activate="onActivate(c.id)"
              @save="onSave"
              @cancel="collapse"
            />

            <!-- 新增：於當前環境清單尾端 inline 展開一條（環境繼承自 tab，不可選） -->
            <a-card
              v-if="isEditingNew && editing && editing.env === e.id"
              :bordered="true"
              size="small"
              class="mb-2"
            >
              <div class="mb-2 text-[13px] font-medium text-[var(--color-text-2)]">
                新增連線 · {{ e.label }} 環境
              </div>
              <QcConfigEditor
                :model-value="editing"
                :password="store.qcPasswords[editing.id] ?? ''"
                @save="onSave"
                @cancel="cancel"
              />
            </a-card>
          </AccordionGroup>
        </a-tab-pane>
      </StickyTabs>

      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        各環境（sit / stage / production）連線完全隔離、互不可見；同環境可建多套帳號連線，
        開啟卡片右側開關即切換當前抽取使用的連線（全域同時僅一套啟用，tab 綠點＝啟用中所在環境）。
      </p>
    </div>
  </StateGuard>
</template>
