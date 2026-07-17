<script setup lang="ts">
// 帳號管理（帳號抽屜內容）：顯示當前登入者 + 個人導出偏好 + 登出 / 切換帳號。
// 首次登入仍走全屏 Login.vue（未登入關卡）；此頁為已登入後的帳號管理。
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Message } from '@arco-design/web-vue';
import exportCfg from '@config/global/export.json';
import { getSettings, saveSettings } from '@/api';
import { useAuthStore } from '@/stores';

const router = useRouter();
const auth = useAuthStore();

const onLogout = () => {
  auth.logout();
  Message.success('已登出');
  router.push('/login');
};

// 導出偏好：個人 Google Drive 上傳資料夾（per-user，存 user_settings；空＝用系統預設共用資料夾）
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
    // 傳空字串＝清除個人偏好（後端存 None → 導出通知退回系統預設）
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
      <div class="text-xs text-[#86909c]">登入帳號</div>
      <div class="text-base font-semibold text-[#1d2129]">{{ auth.user?.email || '—' }}</div>
    </div>
    <div v-if="auth.user?.created_at">
      <div class="text-xs text-[#86909c]">建立時間</div>
      <div class="text-sm text-[#4e5969]">{{ auth.user.created_at }}</div>
    </div>

    <!-- 導出偏好：導出完成通知「打開 Google Drive 上傳」的目的資料夾（個人覆寫，空＝系統預設） -->
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
        導出完成通知的「打開 Google Drive 上傳」將開啟此資料夾；留空＝使用系統預設共用資料夾。
      </div>
    </div>

    <a-button status="danger" long @click="onLogout">登出 / 切換帳號</a-button>
    <div class="text-xs text-[#86909c] leading-relaxed">
      登出後將回到登入頁，可用其他帳號登入。每個帳號的 LLM 設定（key / model /
      清單）與導出偏好互相隔離。
    </div>
  </div>
</template>
