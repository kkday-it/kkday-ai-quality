// 設定全域狀態（Pinia）：LLM 連線層（per-provider）+ 功能區默認旋鈕層（per-area）+ QC 連線層（per-env）。
// 用 store 而非 composable：設定抽屜 unmount-on-close，composable 會隨卸載丟狀態；store 跨掛載週期持久，
// 各功能區（prejudge/prompt_debug/sandbox）與設定面板共讀同一份 llmAreaDefaults/llmConnections。
// 機密策略：llmTokens / qcPasswords 在本 store 暫存「本 session 已知明文」（loadAll 自 raw 端點 + 剛存的值），
// 供編輯回填；持久化由後端 saveSettings 整包/部分 patch 合併（空/遮罩不覆蓋）。
// 併發語義：連線層 patch 為整包替換（client 端先 spread 現有完整 state 再 overlay 變更 key），
// 多人同時編輯不同 tab 走 last-write-wins（與後端 settings.py 文件一致，可接受）。
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { getSettingsRaw, saveSettings } from '@/api';
import type {
  LlmArea,
  LlmAreaDefault,
  LlmConnection,
  QcConnection,
  SettingsBundle,
} from '@/features/settings/types';

export const useSettingsConfigsStore = defineStore('settingsConfigs', () => {
  const llmConnections = ref<Record<string, LlmConnection>>({});
  const llmAreaDefaults = ref<Partial<Record<LlmArea, LlmAreaDefault>>>({});
  /** per-provider 明文 token（本 session 已知）；key＝provider id。 */
  const llmTokens = ref<Record<string, string>>({});
  const qcConnections = ref<Record<string, QcConnection>>({});
  /** per-env 明文 password（本 session 已知）；key＝env id。 */
  const qcPasswords = ref<Record<string, string>>({});
  /** 逐供應商 / 逐環境是否已配機密（連線卡個別顯示狀態點用，不含明文）。 */
  const providerHasToken = ref<Record<string, boolean>>({});
  const qcEnvHasPassword = ref<Record<string, boolean>>({});
  const stubMode = ref(true);
  const loading = ref(false);
  const loaded = ref(false);

  /** 從 masked/raw 回應同步非機密狀態；機密維持本地明文不被遮罩覆蓋。 */
  function syncFrom(bundle: SettingsBundle): void {
    llmConnections.value = bundle.llm_connections ?? {};
    llmAreaDefaults.value = bundle.llm_area_defaults ?? {};
    qcConnections.value = bundle.qc_connections ?? {};
    providerHasToken.value = bundle.provider_has_token ?? {};
    qcEnvHasPassword.value = bundle.qc_env_has_password ?? {};
    stubMode.value = !!bundle.stub_mode;
  }

  /** 初次載入（raw 端點回明文機密，供編輯回填）。force＝true 強制重抓。 */
  async function loadAll(force = false): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    try {
      const s: SettingsBundle = await getSettingsRaw();
      syncFrom(s);
      llmTokens.value = { ...(s.llm_tokens ?? {}) };
      qcPasswords.value = { ...(s.qc_passwords ?? {}) };
      loaded.value = true;
    } finally {
      loading.value = false;
    }
  }

  /** 送 patch 給後端並以權威回應同步狀態；剛存的明文機密合併進本地 map（供再編輯）。 */
  async function persist(
    patch: Record<string, unknown>,
    localSecrets?: { llmTokens?: Record<string, string>; qcPasswords?: Record<string, string> },
  ): Promise<void> {
    const m: SettingsBundle = await saveSettings(patch);
    syncFrom(m);
    if (localSecrets?.llmTokens) Object.assign(llmTokens.value, localSecrets.llmTokens);
    if (localSecrets?.qcPasswords) Object.assign(qcPasswords.value, localSecrets.qcPasswords);
  }

  // ── LLM 連線（每供應商一條：base_url + token）──
  /** 存/更新單一供應商連線；token 空＝不變更（dirty 才帶，後端空/遮罩不覆蓋既有）。 */
  async function saveLlmConnection(
    provider: string,
    baseUrl: string,
    token?: string,
  ): Promise<void> {
    const patch: Record<string, unknown> = {
      llm_connections: { ...llmConnections.value, [provider]: { base_url: baseUrl } },
    };
    if (token) patch.llm_tokens = { [provider]: token };
    await persist(patch, token ? { llmTokens: { [provider]: token } } : undefined);
  }

  // ── LLM 旋鈕（每功能區一份默認：team 共用）──
  /** 存某功能區的默認旋鈕（「存為此區默認」動作）。 */
  async function saveLlmAreaDefault(area: LlmArea, knobs: LlmAreaDefault): Promise<void> {
    await persist({ llm_area_defaults: { [area]: knobs } });
  }

  // ── QC 連線（每環境一條：host/port/user + password）──
  /** 存/更新單一環境 QC 連線；password 空＝不變更。 */
  async function saveQcConnection(
    env: string,
    conn: QcConnection,
    password?: string,
  ): Promise<void> {
    const patch: Record<string, unknown> = {
      qc_connections: { ...qcConnections.value, [env]: conn },
    };
    if (password) patch.qc_passwords = { [env]: password };
    await persist(patch, password ? { qcPasswords: { [env]: password } } : undefined);
  }

  return {
    llmConnections,
    llmAreaDefaults,
    llmTokens,
    qcConnections,
    qcPasswords,
    providerHasToken,
    qcEnvHasPassword,
    stubMode,
    loading,
    loaded,
    loadAll,
    saveLlmConnection,
    saveLlmAreaDefault,
    saveQcConnection,
  };
});
