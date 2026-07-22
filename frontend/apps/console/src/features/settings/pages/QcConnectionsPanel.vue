<script setup lang="ts">
import { onMounted } from 'vue';
import qcDefaults from '@config/global/qc_db.json';
import { StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';
import { QcConnectionCard } from '../components';

// 🗄️ QC DB 連線 tab：每環境（sit/stage/production）固定一條連線（host/port/user/password），
// 全項目共用（去帳戶隔離）。無新增/刪除/啟用/排序——環境本身即 key，結構上不存在跨環境殘值；
// 佐證取數（qc_evidence）固定只讀 production 這一條。
const store = useSettingsConfigsStore();
const ENVS = qcDefaults.environments;
onMounted(() => store.loadAll());

const onSave = (env: string, payload: { conn: { host: string; port: number | null; user: string }; password?: string }) =>
  store.saveQcConnection(env, payload.conn, payload.password);
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <QcConnectionCard
        v-for="e in ENVS"
        :key="e.id"
        :env="e.id"
        :connection="store.qcConnections[e.id]"
        :password-known="store.qcPasswords[e.id] ?? ''"
        :has-password="!!store.qcEnvHasPassword[e.id]"
        @save="(payload) => onSave(e.id, payload)"
      />
      <p class="mb-0 mt-1 text-[13px] leading-[1.7] text-[#4e5969]">
        各環境（sit / stage / production）連線完全隔離、互不可見，全項目共用同一份；
        佐證取數固定只讀 production 這一條。
      </p>
    </div>
  </StateGuard>
</template>
