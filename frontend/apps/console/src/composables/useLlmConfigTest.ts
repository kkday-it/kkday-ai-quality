// 「一組 model + 旋鈕能不能跑」的測試連線邏輯（與連線卡「這個 base_url + token 通不通」不同層）。
// ⚠️ 2026-07-31 起唯一消費端＝設定 › LLM 設定 的模型配置編輯器（`LlmModelConfigList.vue`），
// 各功能區頁面已不再各擺一顆測試鈕——旋鈕收斂進具名配置後，該在「編配置的地方」驗證，
// 而不是在四個用配置的地方各驗一次。
// 入參是 getter（呼叫當下才取值，避免閉包吃到舊值），本檔不關心那組值從哪個配置解析而來。
import { ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { testLlm, type LlmPingResult } from '@/api';
import type { LlmKnobs } from '@/features/settings/types';

type Knobs = LlmKnobs;

/**
 * 用「當前表單值」（provider + 旋鈕）即時測試 LLM 連線，不落庫；對齊設定面板
 * `/settings/test-llm` 唯一測試端點，讓使用者能驗證 reasoning_effort / temperature 等旋鈕調整
 * 是否真的對該 model 生效，而不只是連線通不通。
 *
 * 配置一有變動即清空上次結果——測試結果只對「當時那組配置」有效，留著會讓使用者以為**現在**這組
 * （其實已改過、未重測）也驗證通過了。
 * @param getProvider 呼叫當下取得目前 provider（避免閉包吃到舊值）。
 * @param getKnobs 呼叫當下取得目前旋鈕值。
 * @returns `testing`（測試中）、`testResult`（最近一次結果，供 LlmConfigTestResult 顯示）、`onTest`（觸發測試）。
 */
export function useLlmConfigTest(getProvider: () => string, getKnobs: () => Knobs) {
  const testing = ref(false);
  const testResult = ref<LlmPingResult | null>(null);

  // 測試中不清（onTest 自己會先清一次），否則進行中的測試會被自身的 knobs 讀取誤觸
  watch(
    [getProvider, () => ({ ...getKnobs() })],
    () => {
      if (!testing.value) testResult.value = null;
    },
    { deep: true },
  );

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
