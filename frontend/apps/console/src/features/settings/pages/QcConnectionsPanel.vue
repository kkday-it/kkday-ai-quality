<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import qcDefaults from '@config/global/qc_db.json';
import { AccordionGroup, StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';
import { QcConfigCard, QcConfigEditor } from '../components';
import { configStamp } from '../utils';
import type { QcConfig } from '../types';

// 🗄️ QC DB 接口 tab：管理多套 QC 連線（卡片清單 + inline 新增/編輯 + 刪除 + 卡片內「設為啟用」）。
// SIT / Stage 各建一套獨立 config，各自記住 host/帳密/db/schema（切換不再丟綁定）。
const store = useSettingsConfigsStore();
const QC = qcDefaults;
const DEFAULT_ENV = QC.defaultEnv;
const envOf = (id: string) => QC.environments.find((e) => e.id === id) ?? QC.environments[0];

onMounted(() => store.loadAll());

// 新增流程：editing 僅持有「新建中的 blank config」（尚未落庫，渲染於清單尾端）。
// 既有卡片已「展開即編輯」（QcConfigCard body 直接是表單），不再需要編輯態切換。
const editing = ref<QcConfig | null>(null);
const isEditingNew = computed(
  () => !!editing.value && !store.qcConfigs.some((c) => c.id === editing.value!.id)
);
const blank = (): QcConfig => ({
  id: crypto.randomUUID(),
  label: `QC DB ${configStamp()}`,
  env: DEFAULT_ENV,
  host: envOf(DEFAULT_ENV).host,
  port: QC.port as number,
  user: '',
  names: [],
  schemas: [QC.schema],
});
// 手風琴受控展開：activeId＝當前展開面板；預設展開第一張（loadAll 完成後第一筆就緒時補上）。
const activeId = ref('');
watch(
  () => store.qcConfigs,
  (list) => {
    if (!activeId.value && list[0]) activeId.value = list[0].id;
  },
  { immediate: true, deep: false }
);
// 單開不變量：展開任一既有面板（activeId 變真值）即丟棄尚未存的「新增」草稿，
// 確保任何交互下只有一個編輯面板展開（新增尾卡在手風琴單開控制之外，須手動互斥）。
watch(activeId, (id) => {
  if (id) editing.value = null;
});
// 新增＝先收合所有既有面板（activeId=''），只展開尾端新增編輯器。
const openNew = () => {
  activeId.value = '';
  editing.value = blank();
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
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <div class="mb-2 flex items-center justify-between">
        <span class="font-medium">🗄️ QC DB 連線</span>
        <a-button type="primary" size="small" @click="openNew">新增連線</a-button>
      </div>
      <a-empty
        v-if="!store.qcConfigs.length && !editing"
        description="尚無 QC 連線，點「新增連線」建立第一套（SIT / Stage 各建一套）"
      />

      <!-- 手風琴卡片清單（單開 + 預設展開第一張）；編輯中者就地展開為 inline 編輯器 -->
      <AccordionGroup v-if="store.qcConfigs.length || isEditingNew" v-model:active="activeId">
        <QcConfigCard
          v-for="c in store.qcConfigs"
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

        <!-- 新增：於清單尾端 inline 展開一條 -->
        <a-card v-if="isEditingNew && editing" :bordered="true" size="small" class="mb-2">
          <QcConfigEditor
            :model-value="editing"
            :password="store.qcPasswords[editing.id] ?? ''"
            @save="onSave"
            @cancel="cancel"
          />
        </a-card>
      </AccordionGroup>

      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        管理多套 QC 連線；開啟卡片右側開關即切換當前抽取使用的連線。SIT / Stage 各自獨立、切換不丟綁定。
      </p>
    </div>
  </StateGuard>
</template>
