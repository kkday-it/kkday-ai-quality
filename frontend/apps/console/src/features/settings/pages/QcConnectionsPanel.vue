<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import defaults from '@config/defaults.json';
import { StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';
import { QcConfigCard, QcConfigEditor } from '../components';
import { configStamp } from '../utils';
import type { QcConfig } from '../types';

// 🗄️ QC DB 接口 tab：管理多套 QC 連線（卡片清單 + 新增/編輯 modal + 刪除 + 卡片內「設為啟用」）。
// SIT / Stage 各建一套獨立 config，各自記住 host/帳密/db/schema（切換不再丟綁定）。
const store = useSettingsConfigsStore();
const QC = defaults.qc_db;
const DEFAULT_ENV = QC.defaultEnv;
const envOf = (id: string) => QC.environments.find((e) => e.id === id) ?? QC.environments[0];

onMounted(() => store.loadAll());

const modal = ref(false);
const editing = ref<QcConfig | null>(null);
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
const openNew = () => {
  editing.value = blank();
  modal.value = true;
};
const openEdit = (cfg: QcConfig) => {
  editing.value = { ...cfg, names: [...cfg.names], schemas: [...cfg.schemas] };
  modal.value = true;
};
const onSave = async (payload: { config: QcConfig; password?: string }) => {
  try {
    await store.saveQcConfig(payload.config, payload.password);
    modal.value = false;
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
        v-if="!store.qcConfigs.length"
        description="尚無 QC 連線，點「新增連線」建立第一套（SIT / Stage 各建一套）"
      />
      <QcConfigCard
        v-for="c in store.qcConfigs"
        :key="c.id"
        :config="c"
        :active="c.id === store.activeQcId"
        @edit="openEdit(c)"
        @delete="onDelete(c.id)"
        @activate="onActivate(c.id)"
        @rename="(label) => onRename(c, label)"
      />
      <p class="mb-0 mt-3 text-[13px] leading-[1.7] text-[#4e5969]">
        管理多套 QC 連線；卡片「設為啟用」即切換當前抽取使用的連線。SIT / Stage 各自獨立、切換不丟綁定。
      </p>

      <a-modal v-model:visible="modal" :width="700" :footer="false" title="QC DB 連線配置" unmount-on-close>
        <QcConfigEditor
          v-if="editing"
          :model-value="editing"
          :password="store.qcPasswords[editing.id] ?? ''"
          @save="onSave"
        />
      </a-modal>
    </div>
  </StateGuard>
</template>
