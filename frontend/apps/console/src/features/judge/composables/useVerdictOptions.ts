// verdict 下拉選項（value=機器 code / label=中文＋code），讀 config SSOT `verdicts.json`。
// 單一真相＝config/taxonomy/verdicts.json（後端 taxonomy.py 同源）；模組級快取一次，多元件共用。
import { ref } from 'vue';
import { getConfigFile } from '@/api/config.api';

/** verdict 下拉一條：value 存機器 code、label 顯示「中文（code）」。 */
export interface VerdictOption {
  value: string;
  label: string;
}

/** verdicts.json 的 item 形狀（只取下拉需要的兩欄）。 */
interface VerdictItem {
  code: string;
  label_zh: string;
}

const options = ref<VerdictOption[]>([]);
let started = false;

/**
 * 取 verdict 下拉選項（config 驅動，免硬編）。首呼觸發一次載入並快取；之後共用同一 ref。
 * @returns verdictOptions（reactive，載入完成後填入；載入失敗則保持空並可重試）
 * @example const { verdictOptions } = useVerdictOptions();
 */
export function useVerdictOptions(): { verdictOptions: typeof options } {
  if (!started) {
    started = true;
    getConfigFile('verdicts.json')
      .then((r) => {
        const items = (r.content as { items?: VerdictItem[] }).items ?? [];
        options.value = items.map((it) => ({ value: it.code, label: `${it.label_zh}（${it.code}）` }));
      })
      .catch(() => {
        started = false; // 失敗解鎖，下次再試
      });
  }
  return { verdictOptions: options };
}
