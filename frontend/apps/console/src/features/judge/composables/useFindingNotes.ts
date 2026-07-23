// 歸因備註（append-only 歷史：備註人 / 時間 / 內容）——由 AttributionList.vue 下沉，
// 使頁面薄化為模板+綁定；直接呼叫 @/api 的 finding notes 端點。
import { ref } from 'vue';
import { addFindingNote, getFindingNotes, type FindingNote } from '@/api';
import { Message } from '@arco-design/web-vue';

/**
 * 單條歸因（finding）的備註抽屜狀態與操作（開抽屜載入歷史 / 送出新增）。
 *
 * @returns 抽屜開關、目前 finding id、備註列表、草稿輸入、loading/saving 狀態、
 *   `openNotes`（開抽屜並載入）、`submitNote`（送出新增）、`fmtNoteTime`（時間格式化）。
 */
export function useFindingNotes() {
  const noteOpen = ref(false);
  const noteFindingId = ref('');
  const noteList = ref<FindingNote[]>([]);
  const noteDraft = ref('');
  const noteLoading = ref(false);
  const noteSaving = ref(false);

  /** 開某條歸因的備註抽屜並載入歷史。 */
  const openNotes = async (findingId: string): Promise<void> => {
    noteFindingId.value = findingId;
    noteDraft.value = '';
    noteList.value = [];
    noteOpen.value = true;
    noteLoading.value = true;
    try {
      noteList.value = await getFindingNotes(findingId);
    } catch (e: any) {
      Message.error('載入備註失敗：' + (e?.message || e));
    } finally {
      noteLoading.value = false;
    }
  };

  /** 送出一則備註（備註人由後端登入身分帶入），成功後附加於時間軸尾端（舊到新）。 */
  const submitNote = async (): Promise<void> => {
    const content = noteDraft.value.trim();
    if (!content) return;
    noteSaving.value = true;
    try {
      const created = await addFindingNote(noteFindingId.value, content);
      noteList.value = [...noteList.value, created]; // 舊到新：新備註為最新，附加於尾端
      noteDraft.value = '';
      Message.success('已新增備註');
    } catch (e: any) {
      Message.error('新增備註失敗：' + (e?.message || e));
    } finally {
      noteSaving.value = false;
    }
  };

  /** 備註時間顯示（ISO → 'YYYY-MM-DD HH:mm:ss'）。 */
  const fmtNoteTime = (iso: string | null): string => (iso ? iso.replace('T', ' ').slice(0, 19) : '');

  return {
    noteOpen,
    noteFindingId,
    noteList,
    noteDraft,
    noteLoading,
    noteSaving,
    openNotes,
    submitNote,
    fmtNoteTime,
  };
}
