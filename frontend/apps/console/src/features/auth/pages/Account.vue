<script setup lang="ts">
// 帳號資訊（帳號抽屜內容）：本地模式無登入系統，顯示固定身分 + 團隊共用導出偏好。
import { onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import exportCfg from '@config/global/export.json';
import { getSettings, saveSettings } from '@/api';
import { useAuthStore } from '@/stores';

const auth = useAuthStore();

// 導出偏好：Google Drive 上傳資料夾（全項目共享，存 user_settings 固定 key；空＝用系統預設資料夾）
const gdriveUrl = ref('');
const gdriveSaving = ref(false);
onMounted(async () => {
  try {
    gdriveUrl.value = (await getSettings()).gdrive_upload_folder_url || '';
  } catch {
    // 載入失敗不阻斷抽屜（欄位留空可重存）
  }
});
const saveGdriveUrl = async () => {
  gdriveSaving.value = true;
  try {
    // 傳空字串＝清除偏好（後端存 None → 導出通知退回系統預設）
    await saveSettings({ gdrive_upload_folder_url: gdriveUrl.value.trim() });
    Message.success('已儲存導出偏好');
  } catch (e) {
    Message.error('儲存失敗：' + ((e as Error)?.message || e));
  } finally {
    gdriveSaving.value = false;
  }
};
</script>

<template>
  <div class="flex flex-col gap-5">
    <div>
      <div class="text-xs text-[#86909c]">當前身分</div>
      <div class="text-base font-semibold text-[#1d2129]">{{ auth.user?.email || '—' }}</div>
    </div>

    <!-- 導出偏好：導出完成通知「打開 Google Drive 上傳」的目的資料夾（全項目共享，空＝系統預設） -->
    <div>
      <div class="mb-2 text-xs text-[#86909c]">導出偏好 — Google Drive 上傳資料夾</div>
      <div class="flex gap-2">
        <a-input
          v-model="gdriveUrl"
          class="flex-1"
          allow-clear
          :placeholder="exportCfg.gdrive_upload_folder_url || 'https://drive.google.com/drive/…'"
          @press-enter="saveGdriveUrl"
        />
        <a-button type="primary" :loading="gdriveSaving" @click="saveGdriveUrl">儲存</a-button>
      </div>
      <div class="mt-1 text-xs text-[#86909c] leading-relaxed">
        導出完成通知的「打開 Google Drive 上傳」將開啟此資料夾（全項目共用一份）；留空＝使用系統預設資料夾。
      </div>
    </div>
  </div>
</template>
