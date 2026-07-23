// 導出背景 job 前端生命週期：啟動 → SSE 追進度 → 完成下載 / 可停止。多處導出（問題列表 / 初判規則）
// 共用同一套進度/停止互動，故下沉為 composable，呼叫端只需提供「starter（回 job_id）」與下載檔名。
import { computed, h, ref } from 'vue';
import { Message, Notification } from '@arco-design/web-vue';
import exportCfg from '@config/global/export.json';
import {
  cancelExport,
  downloadExport,
  exportStreamUrl,
  getSettings,
  type ExportJobSnapshot,
} from '@/api';

/** 「打開 Google Drive 上傳」的 config 內建預設：未經設定抽屜配置過偏好時的最終回退。 */
const _GDRIVE_UPLOAD_URL =
  exportCfg.gdrive_upload_folder_url || 'https://drive.google.com/drive/my-drive';

/** 取「打開 Google Drive 上傳」目的地：設定抽屜「導出偏好」（全項目共用一份）優先，未設/讀取失敗退 config 內建預設。 */
const _gdriveUploadUrl = async (): Promise<string> => {
  try {
    return (await getSettings()).gdrive_upload_folder_url || _GDRIVE_UPLOAD_URL;
  } catch {
    return _GDRIVE_UPLOAD_URL; // 設定讀取失敗不阻斷通知
  }
};

/** SSE 終態集合（見到即停止串流）。 */
const _TERMINAL = new Set(['done', 'error', 'cancelled']);

/**
 * 管理單一導出背景 job 的進度與停止（與初判歸因 job 同構，但無暫停/恢復）。
 *
 * @returns exporting（是否進行中）/ status（running|cancelling|done|error|cancelled）/
 *   progress（{processed,total}）/ pct（0–100）/ run（啟動一次導出）/ cancel（停止當前 job）。
 */
export function useExportJob() {
  const exporting = ref(false);
  /** 當前 job 狀態（由 SSE 權威更新；stop 動作先樂觀設 cancelling）。 */
  const status = ref('');
  const progress = ref({ processed: 0, total: 0 });
  /** 進度百分比（total 未知＝0 時回 0，由呼叫端顯示「準備中」）。 */
  const pct = computed(() =>
    progress.value.total ? Math.round((progress.value.processed / progress.value.total) * 100) : 0,
  );
  let jobId = '';

  // 以 SSE 長連線接收導出進度（免輪詢）；終態或連線中斷即 resolve。
  const _poll = (id: string) =>
    new Promise<void>((resolve) => {
      const es = new EventSource(exportStreamUrl(id));
      const finish = () => {
        es.close();
        resolve();
      };
      es.onmessage = (ev) => {
        const st = JSON.parse(ev.data) as ExportJobSnapshot;
        status.value = st.status || status.value;
        progress.value = { processed: st.processed || 0, total: st.total || 0 };
        if (_TERMINAL.has(st.status)) finish();
      };
      es.onerror = finish; // 連線中斷（含 done 後 server 關流）；狀態非終態則於下方判定為中斷
    });

  /** 下載完成通知：附「打開 Google Drive」捷徑（設定抽屜的導出偏好資料夾優先，手動拖曳上傳，無需帳號綁定/OAuth）。 */
  const _notifyDownloaded = async (successMessage: string) => {
    const href = await _gdriveUploadUrl();
    Notification.success({
      title: successMessage,
      content: () =>
        h(
          'a',
          { href, target: '_blank', rel: 'noopener', style: 'color: rgb(var(--primary-6))' },
          '打開 Google Drive 上傳 →',
        ),
      duration: 8000,
      closable: true,
    });
  };

  /**
   * 啟動一次導出：starter 回 {job_id} → SSE 追進度 → done 時取檔下載、cancelled/error/中斷各提示。
   * @param starter 呼叫領域 start 端點（如 startProblemsExport）回 {job_id} 的函式
   * @param downloadName 下載檔名（含副檔名；前端以本地時間戳命名，忽略後端建議名）
   * @param successMessage done 時的成功提示（預設「已導出 Excel」；非 xlsx 導出可覆寫，如資料包）
   */
  const run = async (
    starter: () => Promise<{ job_id: string }>,
    downloadName: string,
    successMessage = '已導出 Excel',
  ): Promise<void> => {
    if (exporting.value) return;
    exporting.value = true;
    status.value = 'running';
    progress.value = { processed: 0, total: 0 };
    try {
      const { job_id } = await starter();
      jobId = job_id;
      await _poll(job_id);
      if (status.value === 'done') {
        const blob = await downloadExport(job_id);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = downloadName;
        a.click();
        URL.revokeObjectURL(url);
        await _notifyDownloaded(successMessage);
      } else if (status.value === 'cancelled') {
        Message.info('已停止導出');
      } else if (status.value === 'error') {
        Message.error('導出失敗，請稍後重試');
      } else {
        Message.warning('導出連線中斷，請重試'); // SSE 掉線且未達終態，避免靜默無反饋
      }
    } catch (e: any) {
      Message.error('導出失敗：' + (e?.message || e));
    } finally {
      exporting.value = false;
      jobId = '';
    }
  };

  /** 停止當前導出 job（樂觀設 cancelling，SSE 隨後權威更新為 cancelled）。 */
  const cancel = async (): Promise<void> => {
    if (!jobId) return;
    try {
      await cancelExport(jobId);
      status.value = 'cancelling';
    } catch (e: any) {
      Message.error('停止失敗：' + (e?.message || e));
    }
  };

  return { exporting, status, progress, pct, run, cancel };
}
