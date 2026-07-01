// 5 反饋來源——SSOT 為 repo 根 config/global/sources.json（前後端同讀，後端 app/core/sources.py 對應）。
// 上傳改全自動辨識（不再手選），本常數供批次列表 source→label 顯示對照。
import sourcesConfig from '@config/global/sources.json';

/** 單一反饋來源定義（value=source code / label=顯示名 / hint=說明 / natural_key=增量 upsert 自然鍵）。 */
export interface SourceDef {
  value: string;
  label: string;
  hint: string;
  natural_key: string;
}

export const SOURCES: SourceDef[] = sourcesConfig.sources;

/** source code → 中文 label（批次列表 / 來源欄顯示）。 */
export const SOURCE_LABEL: Record<string, string> = Object.fromEntries(
  SOURCES.map((s) => [s.value, s.label]),
);
