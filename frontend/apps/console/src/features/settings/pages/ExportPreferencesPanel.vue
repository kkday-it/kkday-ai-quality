<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import exportCfg from '@config/global/export.json';
import { StateGuard } from '@/components';
import { useSettingsConfigsStore } from '@/stores';

// 📤 導出偏好：全項目共用一份，日常操作免特殊權限（原「帳號」抽屜內容，去帳戶系統後改嵌入
// DataImportPanel「導出資料包」按鈕旁——兩者都消費同一份 Google Drive 資料夾偏好，見 useExportJob；
// 它本來就是 global config，不是帳號個人設定，見 config/README.md）。
const store = useSettingsConfigsStore();

const gdriveUrl = ref('');
const saving = ref(false);

onMounted(() => store.loadAll());
watch(
  () => store.gdriveUploadFolderUrl,
  (v) => (gdriveUrl.value = v),
  { immediate: true },
);

const onSave = async () => {
  saving.value = true;
  try {
    // 傳空字串＝清除偏好（後端存 None → 導出通知退回系統預設資料夾）
    await store.saveGdriveUploadFolderUrl(gdriveUrl.value.trim());
    Message.success('已儲存導出偏好');
  } catch (e) {
    Message.error('儲存失敗：' + ((e as Error)?.message || e));
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <StateGuard :loading="store.loading">
    <div>
      <div class="mb-2 text-xs text-[#86909c]">導出偏好 — Google Drive 上傳資料夾</div>
      <div class="flex gap-2">
        <a-input
          v-model="gdriveUrl"
          class="flex-1"
          allow-clear
          :placeholder="exportCfg.gdrive_upload_folder_url || 'https://drive.google.com/drive/…'"
          @press-enter="onSave"
        />
        <a-button type="primary" :loading="saving" @click="onSave">儲存</a-button>
      </div>
      <div class="mt-1 text-xs leading-relaxed text-[#86909c]">
        導出完成通知的「打開 Google Drive 上傳」將開啟此資料夾（全項目共用一份）；留空＝使用系統預設資料夾。
      </div>
    </div>
  </StateGuard>
</template>
