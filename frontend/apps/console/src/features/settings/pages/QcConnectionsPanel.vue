<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import qcDefaults from '@config/global/default_qc.json';
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

// inline 編輯：editing 持有「編輯中的 config」（新建＝blank，id 不在清單→渲染於尾端；編輯＝既有副本，就地取代卡片）。
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
// 手風琴預設展開第一張卡片（StateGuard 待 loadAll 完成才渲染 AccordionGroup，故此時 configs 已就緒）
const firstConfigId = computed(() => store.qcConfigs[0]?.id ?? '');
const openNew = () => (editing.value = blank());
const openEdit = (cfg: QcConfig) =>
  (editing.value = { ...cfg, names: [...cfg.names], schemas: [...cfg.schemas] });
const cancel = () => (editing.value = null);

const onSave = async (payload: { config: QcConfig; password?: string }) => {
  try {
    await store.saveQcConfig(payload.config, payload.password);
    editing.value = null;
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
const onRename = async (cfg: QcConfig, label: string) => {
  try {
    await store.saveQcConfig({ ...cfg, label }); // 不帶 password → 僅改名，機密不動
    Message.success('已更新名稱');
  } catch (e: any) {
    Message.error('改名失敗：' + (e?.message || e));
  }
};
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <div class="mb-2 flex items-center justify-between">
        <span class="font-medium">🗄️ QC DB（PostgreSQL）連線</span>
        <a-button type="primary" size="small" @click="openNew">新增連線</a-button>
      </div>
      <a-empty
        v-if="!store.qcConfigs.length && !editing"
        description="尚無 QC 連線，點「新增連線」建立第一套（SIT / Stage 各建一套）"
      />

      <!-- 手風琴卡片清單（單開 + 預設展開第一張）；編輯中者就地展開為 inline 編輯器 -->
      <AccordionGroup v-if="store.qcConfigs.length || isEditingNew" :default-active="firstConfigId">
        <template v-for="c in store.qcConfigs" :key="c.id">
          <a-card v-if="editing && editing.id === c.id" :bordered="true" size="small" class="mb-2">
            <QcConfigEditor
              :model-value="editing"
              :password="store.qcPasswords[editing.id] ?? ''"
              @save="onSave"
              @cancel="cancel"
            />
          </a-card>
          <QcConfigCard
            v-else
            :config="c"
            :item-key="c.id"
            :active="c.id === store.activeQcId"
            @edit="openEdit(c)"
            @delete="onDelete(c.id)"
            @activate="onActivate(c.id)"
            @rename="(label) => onRename(c, label)"
          />
        </template>

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
