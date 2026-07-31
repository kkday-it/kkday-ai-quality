// 設定全域狀態（Pinia）：LLM 連線層（per-provider）+ 模型配置庫（全域具名配置）+ QC 連線層（per-env）。
// 用 store 而非 composable：設定抽屜 unmount-on-close，composable 會隨卸載丟狀態；store 跨掛載週期持久，
// 各功能區（prejudge/prompt_debug/sandbox/prompt_revise）與設定面板共讀同一份 llmModelConfigs/llmConnections。
// ⚠️ 本 store **不持有**「哪個功能區用哪一筆配置」——那是個人選擇（一個人切配置不該讓全團隊跟著變），
// 綁定＝`llmAreaConfigs`（team 共用單一份，選了就存），見 composables/useLlmAreaConfig.ts。
// 權限分層（呼應後端 settings.llm-config.manage/qc-config.manage/secret.read 僅 grants）：
// loadAll() 走遮罩端點（/api/settings，任何登入者皆可）——功能區旋鈕/連線狀態點僅需此，不需明文；
// loadSecrets() 走明文端點（/api/settings/raw，需 secret.read）——僅連線編輯卡（LlmSettingsPanel/
// QcConnectionsPanel）需要，無權限時 403 由呼叫端吞下，不阻斷一般功能區頁面使用。
// 機密策略：llmTokens / qcPasswords 在本 store 暫存「本 session 已知明文」（loadSecrets + 剛存的值），
// 供編輯回填；持久化由後端 saveSettings 整包/部分 patch 合併（空/遮罩不覆蓋）。
// 併發語義：連線層 patch 為整包替換（client 端先 spread 現有完整 state 再 overlay 變更 key），
// 多人同時編輯不同 tab 走 last-write-wins（與後端 settings.py 文件一致，可接受）。
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { getSettings, getSettingsRaw, saveSettings } from '@/api';
import type {
  LlmModelConfig,
  LlmConnection,
  QcConnection,
  SettingsBundle,
} from '@/features/settings/types';

export const useSettingsConfigsStore = defineStore('settingsConfigs', () => {
  const llmConnections = ref<Record<string, LlmConnection>>({});
  /** 生效的模型配置清單（單層＝DB `llm_model_configs`；全新環境的初始內容由後端種入，之後無出廠層）。 */
  const llmModelConfigs = ref<LlmModelConfig[]>([]);
  /**
   * 功能區 → 用哪一筆配置（area → config id）。
   *
   * ⚠️ **團隊共用單一份，不是個人設定**：一個人在功能區換配置，同事下次進頁面就會看到新的。
   * 這是使用者拍板的取捨（2026-07-31）——換來的是「你調好的安排，同事與新裝置能直接用到」，
   * 詳見後端 `settings.py` 模組 docstring。缺項＝該區還沒綁過，回落出廠 `areaDefaults`。
   */
  const llmAreaConfigs = ref<Record<string, string>>({});
  /** per-provider 明文 token（本 session 已知，僅 secret.read 授權者透過 loadSecrets 取得）；key＝provider id。 */
  const llmTokens = ref<Record<string, string>>({});
  const qcConnections = ref<Record<string, QcConnection>>({});
  /** per-env 明文 password（本 session 已知，僅 secret.read 授權者透過 loadSecrets 取得）；key＝env id。 */
  const qcPasswords = ref<Record<string, string>>({});
  /** 逐供應商 / 逐環境是否已配機密（連線卡個別顯示狀態點用，不含明文；遮罩端點即含此欄位）。 */
  const providerHasToken = ref<Record<string, boolean>>({});
  const qcEnvHasPassword = ref<Record<string, boolean>>({});
  /** 導出完成通知「打開 Google Drive 上傳」的全項目共用目的資料夾（空＝系統預設）。 */
  const gdriveUploadFolderUrl = ref('');
  const stubMode = ref(true);
  const loading = ref(false);
  const loaded = ref(false);

  /** 從 masked/raw 回應同步非機密狀態；機密維持本地明文不被遮罩覆蓋。 */
  function syncFrom(bundle: SettingsBundle): void {
    llmConnections.value = bundle.llm_connections ?? {};
    llmModelConfigs.value = bundle.llm_model_configs ?? [];
    llmAreaConfigs.value = bundle.llm_area_configs ?? {};
    qcConnections.value = bundle.qc_connections ?? {};
    providerHasToken.value = bundle.provider_has_token ?? {};
    qcEnvHasPassword.value = bundle.qc_env_has_password ?? {};
    gdriveUploadFolderUrl.value = bundle.gdrive_upload_folder_url ?? '';
    stubMode.value = !!bundle.stub_mode;
  }

  /** 初次載入（遮罩端點，任何登入者皆可）：連線狀態/功能區旋鈕，供各功能區日常使用。force＝true 強制重抓。 */
  async function loadAll(force = false): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    try {
      const s: SettingsBundle = await getSettings();
      syncFrom(s);
      loaded.value = true;
    } finally {
      loading.value = false;
    }
  }

  /** 載入明文機密（llm_tokens/qc_passwords）供連線編輯卡回填；需 settings.secret.read 權限，
   * 無權限時 403，靜默吞下（連線卡維持遮罩態，不阻斷頁面——僅設定面板的連線分頁需呼叫此函式）。 */
  async function loadSecrets(): Promise<void> {
    try {
      const s: SettingsBundle = await getSettingsRaw();
      llmTokens.value = { ...(s.llm_tokens ?? {}) };
      qcPasswords.value = { ...(s.qc_passwords ?? {}) };
    } catch {
      /* 無 secret.read 權限或請求失敗：連線卡維持遮罩態 */
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

  // ── LLM 模型配置庫（全域具名配置，team 共用）──
  /**
   * 整包替換使用者自訂的模型配置清單（新增／編輯／刪除都走這支）。
   *
   * 刻意是整包而非逐筆 patch：前端本來就持有完整清單，增刪改都是對整份清單操作（同 overview_boards）。
   *
   * @param configs 完整的配置清單（少送一筆＝刪除該筆；後端會同步剪除指向被刪配置的功能區綁定）。
   * @throws 後端校驗未過時 400（規格重複、供應商未登記、旋鈕值域外、清單為空…），訊息可直接顯示給使用者。
   */
  async function saveLlmModelConfigs(configs: LlmModelConfig[]): Promise<void> {
    await persist({ llm_model_configs: configs });
  }

  /**
   * 設定某功能區用哪一筆配置——**選了就存，沒有獨立的儲存按鈕**。
   *
   * 樂觀更新：先改本地（下拉即時反映、不等 round-trip），失敗再回滾並拋出，讓呼叫端提示。
   * 不回滾的話畫面會停在一個「看起來已生效、實際沒落庫」的狀態，重整就變回去——最難查的那種。
   *
   * @param area 功能區 key。
   * @param configId 配置 id；空字串＝清除綁定，回落出廠預設。
   * @throws 後端校驗未過時 400（未知功能區／配置不存在），訊息可直接顯示給使用者。
   */
  async function saveLlmAreaConfig(area: string, configId: string): Promise<void> {
    const before = { ...llmAreaConfigs.value };
    llmAreaConfigs.value = { ...before, [area]: configId };
    try {
      await persist({ llm_area_configs: llmAreaConfigs.value });
    } catch (e) {
      llmAreaConfigs.value = before;
      throw e;
    }
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

  // ── 導出偏好（全項目共用一份，日常操作免特殊權限）──
  /** 存 Google Drive 上傳資料夾偏好；空字串＝清除（後端存 None → 導出通知退回系統預設資料夾）。 */
  async function saveGdriveUploadFolderUrl(url: string): Promise<void> {
    await persist({ gdrive_upload_folder_url: url });
  }

  return {
    llmConnections,
    llmModelConfigs,
    llmAreaConfigs,
    llmTokens,
    qcConnections,
    qcPasswords,
    providerHasToken,
    qcEnvHasPassword,
    gdriveUploadFolderUrl,
    stubMode,
    loading,
    loaded,
    loadAll,
    loadSecrets,
    saveLlmConnection,
    saveLlmModelConfigs,
    saveLlmAreaConfig,
    saveQcConnection,
    saveGdriveUploadFolderUrl,
  };
});
