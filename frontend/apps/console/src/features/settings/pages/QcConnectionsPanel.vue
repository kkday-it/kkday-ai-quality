<script setup lang="ts">
import { computed, onMounted } from 'vue';
import qcDefaults from '@config/global/qc_db.json';
import { PERM } from '@/api';
import { StateGuard } from '@/components';
import { usePermission } from '@/composables/usePermission';
import { useSettingsConfigsStore } from '@/stores';
import { QcConnectionCard } from '../components';

// 🗄️ QC DB 連線 tab：每環境（sit/stage/production）固定一條連線（host/port/user/password），
// 全項目共用（去帳戶隔離）。無新增/刪除/啟用/排序——環境本身即 key，結構上不存在跨環境殘值；
// 佐證取數（qc_evidence）固定只讀 production 這一條。
const store = useSettingsConfigsStore();
const ENVS = qcDefaults.environments;
const { can } = usePermission();
/** 是否可改連線/測試（settings.qc-config.manage，僅 grants）；無此權限僅能檢視狀態點。 */
const canManage = computed(() => can(PERM.settingsQcConfigManage));

onMounted(() => {
  store.loadAll();
  if (canManage.value) store.loadSecrets();
});

const onSave = (env: string, payload: { conn: { host: string; port: number | null; user: string }; password?: string }) =>
  store.saveQcConnection(env, payload.conn, payload.password);
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <a-alert v-if="!canManage" type="info" class="mb-3">
        僅檢視連線狀態；如需修改連線，請聯繫有 QC 連線管理權限的同事。
      </a-alert>
      <QcConnectionCard
        v-for="e in ENVS"
        :key="e.id"
        :env="e.id"
        :connection="store.qcConnections[e.id]"
        :password-known="store.qcPasswords[e.id] ?? ''"
        :has-password="!!store.qcEnvHasPassword[e.id]"
        :can-manage="canManage"
        @save="(payload) => onSave(e.id, payload)"
      />
      <p class="mb-0 mt-1 text-[13px] leading-[1.7] text-[#4e5969]">
        各環境（sit / stage / production）連線完全隔離、互不可見，全項目共用同一份；
        佐證取數固定只讀 production 這一條。
      </p>
    </div>
  </StateGuard>
</template>
