// 多套設定 config 全域狀態（Pinia）：連線(管理) 與 啟用(切換) 兩個 tab 共享同一份資料。
// 用 store 而非 composable：設定抽屜 unmount-on-close，composable 會隨卸載丟狀態；store 跨掛載週期持久。
// 機密策略：providerTokens / qcPasswords 在本 store 暫存「本 session 已知明文」（loadAll 自 raw 端點 + 剛存的值），
// 供編輯回填；持久化由後端 saveSettings 整包/部分 patch 合併（空/遮罩不覆蓋）。
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { getSettingsRaw, saveSettings } from '@/api';
import type { LlmConfig, QcConfig, SettingsBundle } from '@/features/settings/types';

export const useSettingsConfigsStore = defineStore('settingsConfigs', () => {
  const llmConfigs = ref<LlmConfig[]>([]);
  const qcConfigs = ref<QcConfig[]>([]);
  const activeLlmId = ref<string | null>(null);
  const activeQcId = ref<string | null>(null);
  /** per-provider 明文 token（本 session 已知）；key＝provider id。 */
  const providerTokens = ref<Record<string, string>>({});
  /** per-config 明文 password（本 session 已知）；key＝config id。 */
  const qcPasswords = ref<Record<string, string>>({});
  const stubMode = ref(true);
  const loading = ref(false);
  const loaded = ref(false);

  /** 從 masked/raw 回應同步非機密狀態（configs / active / stub）；機密維持本地明文不被遮罩覆蓋。 */
  function syncFrom(bundle: SettingsBundle): void {
    llmConfigs.value = bundle.llm_configs ?? [];
    qcConfigs.value = bundle.qc_configs ?? [];
    activeLlmId.value = bundle.active_llm_config_id ?? null;
    activeQcId.value = bundle.active_qc_config_id ?? null;
    stubMode.value = !!bundle.stub_mode;
  }

  /** 初次載入（raw 端點回明文機密，供編輯回填）。force＝true 強制重抓。 */
  async function loadAll(force = false): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    try {
      const s: SettingsBundle = await getSettingsRaw();
      syncFrom(s);
      providerTokens.value = { ...(s.provider_tokens ?? {}) };
      qcPasswords.value = { ...(s.qc_passwords ?? {}) };
      loaded.value = true;
    } finally {
      loading.value = false;
    }
  }

  /** 送 patch 給後端並以權威回應同步狀態；剛存的明文機密合併進本地 map（供再編輯）。 */
  async function persist(
    patch: Record<string, unknown>,
    localSecrets?: { providerTokens?: Record<string, string>; qcPasswords?: Record<string, string> }
  ): Promise<void> {
    const m: SettingsBundle = await saveSettings(patch);
    syncFrom(m);
    if (localSecrets?.providerTokens) Object.assign(providerTokens.value, localSecrets.providerTokens);
    if (localSecrets?.qcPasswords) Object.assign(qcPasswords.value, localSecrets.qcPasswords);
  }

  // ── LLM ──
  /** 新增/更新一套 LLM config（依 id upsert）；tokenPatch＝{providerId: 明文token}（dirty 才帶）。 */
  async function saveLlmConfig(
    cfg: LlmConfig,
    tokenPatch?: Record<string, string>
  ): Promise<void> {
    const list = [...llmConfigs.value];
    const idx = list.findIndex((c) => c.id === cfg.id);
    if (idx >= 0) list[idx] = cfg;
    else list.push(cfg);
    const patch: Record<string, unknown> = { llm_configs: list };
    if (!activeLlmId.value) patch.active_llm_config_id = cfg.id; // 首套自動設為啟用
    if (tokenPatch) patch.provider_tokens = tokenPatch;
    await persist(patch, tokenPatch ? { providerTokens: tokenPatch } : undefined);
  }

  async function deleteLlmConfig(id: string): Promise<void> {
    await persist({ llm_configs: llmConfigs.value.filter((c) => c.id !== id) });
  }

  async function setActiveLlm(id: string): Promise<void> {
    await persist({ active_llm_config_id: id });
  }

  // ── QC DB ──
  /** 新增/更新一套 QC config（依 id upsert）；password 明文（dirty 才帶）以 transient 欄位送，後端抽存 qc_passwords。 */
  async function saveQcConfig(cfg: QcConfig, password?: string): Promise<void> {
    const payload = password ? { ...cfg, password } : { ...cfg };
    const list = [...qcConfigs.value];
    const idx = list.findIndex((c) => c.id === cfg.id);
    if (idx >= 0) list[idx] = payload as QcConfig;
    else list.push(payload as QcConfig);
    const patch: Record<string, unknown> = { qc_configs: list };
    if (!activeQcId.value) patch.active_qc_config_id = cfg.id; // 首套自動設為啟用
    await persist(patch, password ? { qcPasswords: { [cfg.id]: password } } : undefined);
  }

  async function deleteQcConfig(id: string): Promise<void> {
    await persist({ qc_configs: qcConfigs.value.filter((c) => c.id !== id) });
  }

  async function setActiveQc(id: string): Promise<void> {
    await persist({ active_qc_config_id: id });
  }

  return {
    llmConfigs,
    qcConfigs,
    activeLlmId,
    activeQcId,
    providerTokens,
    qcPasswords,
    stubMode,
    loading,
    loaded,
    loadAll,
    saveLlmConfig,
    deleteLlmConfig,
    setActiveLlm,
    saveQcConfig,
    deleteQcConfig,
    setActiveQc,
  };
});
