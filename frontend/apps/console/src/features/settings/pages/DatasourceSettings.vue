<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { Message } from '@arco-design/web-vue';
import defaults from '@config/defaults.json';
import { getSettingsRaw, saveSettings, testQcDb } from '@/api';
import { StateGuard } from '@/components';

// 🗄️ QC DB（PostgreSQL）連線配置 —— ConfigPanels 折疊面板之一。
// 持久化沿用既有 settings 鏈（getSettingsRaw / saveSettings → 後端 user_settings）；
// qc_db_password 機密：比照 api_token 後端遮罩、留空不覆蓋既有。
// 連線預設（host/port/name/schema）取自 repo 根 config/defaults.json（與 Python 後端同源）。
const QC = defaults.qc_db;
const qc = ref({
  qc_db_host: QC.host,
  qc_db_port: QC.port as number, // 預設直接顯示（5432）
  qc_db_name: QC.name,
  qc_db_schema: QC.schema,
  qc_db_user: '',
  qc_db_password: '',
});
const hasQcPassword = ref(false);
const pwDirty = ref(false); // 僅當使用者輸入新密碼才回送（避免遮罩值覆蓋真值）
const loading = ref(true);
const saving = ref(false);
const testing = ref(false);

onMounted(async () => {
  try {
    const s = await getSettingsRaw(); // raw 端點回明文，password 已存則帶入供眼睛切換
    // 用 || 而非 ??：後端 _DEFAULT 回空字串時亦回退到實值（db/schema 空字串無意義，libpq 會把空 dbname 預設成 username 致誤連）
    qc.value.qc_db_host = s.qc_db_host || QC.host;
    qc.value.qc_db_port = s.qc_db_port || QC.port; // 預設直接顯示（5432）
    qc.value.qc_db_name = s.qc_db_name || QC.name;
    qc.value.qc_db_schema = s.qc_db_schema || QC.schema;
    qc.value.qc_db_user = s.qc_db_user ?? '';
    hasQcPassword.value = !!s.has_qc_db_password;
    if (hasQcPassword.value && s.qc_db_password) {
      qc.value.qc_db_password = s.qc_db_password;
      pwDirty.value = false;
    }
  } catch {
    // 後端 raw 端點未就緒（如未重啟）時靜默降級：沿用表單預設，不阻斷面板
  } finally {
    loading.value = false;
  }
});

// 組裝 patch：非機密欄位照送；password 僅 dirty 且非空才送
const buildPatch = (): Record<string, unknown> => {
  const patch: Record<string, unknown> = {
    qc_db_host: qc.value.qc_db_host,
    qc_db_port: qc.value.qc_db_port,
    qc_db_name: qc.value.qc_db_name,
    qc_db_schema: qc.value.qc_db_schema,
    qc_db_user: qc.value.qc_db_user,
  };
  if (pwDirty.value && qc.value.qc_db_password) patch.qc_db_password = qc.value.qc_db_password;
  return patch;
};

const onSave = async () => {
  saving.value = true;
  try {
    const s = await saveSettings(buildPatch());
    hasQcPassword.value = !!s.has_qc_db_password;
    pwDirty.value = false;
    Message.success('已儲存 QC DB 配置');
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  } finally {
    saving.value = false;
  }
};

// 即時測試：用「當前表單值」測連線（未改密碼則後端沿用既存明文），完整結果輸出按鈕下方 log
const testResult = ref<Record<string, unknown> | null>(null);
const onTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testQcDb(buildPatch());
    testResult.value = {
      ...r,
      target: `${qc.value.qc_db_host}:${qc.value.qc_db_port}/${qc.value.qc_db_name}`,
    };
    if (r.ok) Message.success('連線成功');
    else Message.error('連線失敗：' + (r.error || '未知錯誤'));
  } catch (e: any) {
    testResult.value = { ok: false, error: e?.message || String(e) };
    Message.error('測試失敗：' + (e?.message || e));
  } finally {
    testing.value = false;
  }
};

// 恢復預設：還原成 config/defaults.json 的 QC DB 預設（帳密留空；需按儲存才寫入後端）
const onRestoreDefaults = () => {
  qc.value.qc_db_host = QC.host;
  qc.value.qc_db_port = QC.port;
  qc.value.qc_db_name = QC.name;
  qc.value.qc_db_schema = QC.schema;
  qc.value.qc_db_user = '';
  qc.value.qc_db_password = '';
  pwDirty.value = false;
  testResult.value = null;
  Message.info('已還原為專案預設配置（需按「儲存配置」才寫入後端）');
};
</script>

<template>
  <StateGuard :loading="loading">
    <div>
      <a-space class="mb-3">
        <a-tag v-if="hasQcPassword" color="green">已設定</a-tag>
        <a-tag v-else color="gray">未設定</a-tag>
      </a-space>

      <a-form :model="qc" layout="vertical">
        <a-row :gutter="12">
          <a-col :span="16">
            <a-form-item label="Host">
              <a-input v-model="qc.qc_db_host" :placeholder="QC.host" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item label="Port">
              <a-input-number
                v-model="qc.qc_db_port"
                :min="1"
                :max="65535"
                :placeholder="String(QC.port)"
                class="w-full"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="Database">
              <a-input v-model="qc.qc_db_name" :placeholder="QC.name" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="Schema">
              <a-input v-model="qc.qc_db_schema" :placeholder="QC.schema" allow-clear />
            </a-form-item>
          </a-col>
        </a-row>

        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="User">
              <a-input v-model="qc.qc_db_user" placeholder="資料庫帳號" allow-clear />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="Password">
              <a-input-password
                v-model="qc.qc_db_password"
                :placeholder="hasQcPassword ? '已設定（留空不變更）' : '請輸入密碼'"
                allow-clear
                @input="pwDirty = true"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-space>
          <a-button type="primary" :loading="saving" @click="onSave">儲存配置</a-button>
          <a-button :loading="testing" @click="onTest">測試連線</a-button>
          <a-button @click="onRestoreDefaults">恢復預設</a-button>
        </a-space>

        <!-- 測試結果完整 log（即時測當前配置；非已儲存）-->
        <div
          v-if="testResult"
          class="mt-3 rounded-lg border p-2.5 font-mono text-[12px] leading-[1.6]"
          :class="
            testResult.ok
              ? 'border-[#a3e8dd] bg-[#e8fffb] text-[#0f9b8e]'
              : 'border-[#ffccc7] bg-[#fff1f0] text-[#cf1322]'
          "
        >
          <div class="mb-1 font-bold">{{ testResult.ok ? '✅ 連線成功' : '❌ 連線失敗' }}</div>
          <div v-if="testResult.target">目標：{{ testResult.target }}</div>
          <div v-if="testResult.error" class="whitespace-pre-wrap">
            錯誤：{{ testResult.error }}
          </div>
        </div>
      </a-form>

      <ul class="mb-0 mt-4 pl-[18px] text-[13px] leading-[1.7] text-[#4e5969]">
        <li>QC DB 為公司 PostgreSQL 來源（SIT），供 AI 法官繞過 DAP 直接抽取資料。</li>
        <li>
          連線資訊存後端 user_settings；密碼比照 API Token 僅存後端、前端遮罩、不入
          git；留空＝不變更既有密碼。
        </li>
        <li>後續其他資料來源（Mixpanel 等）將統一在此「配置」分頁逐一新增。</li>
      </ul>
    </div>
  </StateGuard>
</template>
