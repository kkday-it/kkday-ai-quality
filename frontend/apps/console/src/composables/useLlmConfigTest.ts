// 「本次 LLM 配置」測試連線邏輯：canonical composable，供 Prompt 調試台 / 初判設定 /
// Prompt Sandbox 三處「本次 LLM 配置」面板（LlmConfigPicker + LlmKnobs 組合）共用，避免各自
// 重做 testLlm 呼叫 + 訊息提示（對齊同語義控件跨頁一致慣例）。
import { ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { testLlm, type LlmPingResult } from '@/api';
import type { LlmAreaDefault } from '@/features/settings/types';

type Knobs = Pick<LlmAreaDefault, 'model' | 'thinking' | 'reasoning_effort' | 'temperature'>;

/**
 * 用「當前表單值」（provider + 旋鈕）即時測試 LLM 連線，不落庫；對齊設定面板
 * `/settings/test-llm` 唯一測試端點，讓使用者能驗證 reasoning_effort / temperature 等旋鈕調整
 * 是否真的對該 model 生效，而不只是連線通不通。
 * @param getProvider 呼叫當下取得目前 provider（避免閉包吃到舊值）。
 * @param getKnobs 呼叫當下取得目前旋鈕值。
 * @returns `testing`（測試中）、`testResult`（最近一次結果，供 LlmConfigTestResult 顯示）、`onTest`（觸發測試）。
 */
export function useLlmConfigTest(getProvider: () => string, getKnobs: () => Knobs) {
  const testing = ref(false);
  const testResult = ref<LlmPingResult | null>(null);

  const onTest = async (): Promise<void> => {
    testing.value = true;
    testResult.value = null;
    try {
      const knobs = getKnobs();
      const r = await testLlm({
        provider: getProvider(),
        model: knobs.model,
        temperature: knobs.temperature,
        thinking: knobs.thinking,
        reasoning_effort: knobs.reasoning_effort,
      });
      testResult.value = r;
      if (r.ok) Message.success('連線成功');
      else Message.error('連線失敗：' + (r.error || '未知錯誤'));
    } catch (e: any) {
      testResult.value = { ok: false, error: e?.message || String(e) };
      Message.error('測試失敗：' + (e?.message || e));
    } finally {
      testing.value = false;
    }
  };

  return { testing, testResult, onTest };
}
