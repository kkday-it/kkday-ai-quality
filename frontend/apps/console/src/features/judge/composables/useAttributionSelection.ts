// 歸因列表跨頁選取：勾選累積 + 分頁批選（1,2,3~5）。rowKey=source_id，選取即業務 key。
// 自 useAttributionList 下沉；依賴（本頁列 / 每頁大小 / 篩選查詢）由呼叫端注入。
import { computed, ref, type Ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getProblems, type GetProblemsParams } from '@/api';
import type { ProblemRow } from '../constants';
import { parsePageSpec } from '../utils';

/** useAttributionSelection 的注入依賴。 */
interface SelectionDeps {
  /** 當前頁的 review 列（onSelectionChange 用以識別本頁 key）。 */
  rows: Ref<ProblemRow[]>;
  /** 每頁大小（分頁批選換算 offset/limit）。 */
  pageSize: Ref<number>;
  /** 目前生效的篩選+排序查詢（不含 limit/offset；分頁批選按此撈對應頁 id）。 */
  filterQuery: () => GetProblemsParams;
}

/**
 * 跨頁選取狀態與操作。
 * @returns selectedKeys（＝selectedRowKeys）、runCount、clearSelection、onSelectionChange、pageSpec、selectPages
 */
export function useAttributionSelection(deps: SelectionDeps) {
  const { rows, pageSize, filterQuery } = deps;

  const selectedKeys = ref<string[]>([]); // source_id（該來源特徵 id）
  const runCount = computed(() => selectedKeys.value.length); // 已選 review 數
  const clearSelection = () => (selectedKeys.value = []);
  /** 表格 selectedRowKeys＝業務 selectedKeys（rowKey=source_id，一列一 review，無需映射）。 */
  const selectedRowKeys = selectedKeys;
  /** 表格勾選變更（rowKey=source_id）：合併保留非本頁既有選取（跨頁）。 */
  const onSelectionChange = (keys: (string | number)[]) => {
    const pageGroups = new Set(rows.value.map((r) => String(r._group)));
    selectedKeys.value = [
      ...selectedKeys.value.filter((id) => !pageGroups.has(id)), // 保留非本頁選取
      ...keys.map((k) => String(k)), // 本頁已勾（key 即 source_id）
    ];
  };
  const pageSpec = ref('');
  /** 分頁選取（1,2,3,5 / 1~200）：依後端分頁抓對應頁的 item_id 加入選取。 */
  const selectPages = async () => {
    const spec = pageSpec.value.trim();
    const pages = parsePageSpec(spec);
    if (!pages.length) return;
    const lo = pages[0];
    const hi = pages[pages.length - 1];
    const pageSet = new Set(pages);
    const ps = pageSize.value;
    try {
      const r = await getProblems({
        ...filterQuery(),
        limit: (hi - lo + 1) * ps,
        offset: (lo - 1) * ps,
      });
      const ids: string[] = [];
      (r.rows || []).forEach((row: ProblemRow, idx: number) => {
        const gp = lo + Math.floor(idx / ps); // 該列的全域分頁號
        if (pageSet.has(gp)) ids.push(String(row._group)); // 特徵 id（source_id）
      });
      selectedKeys.value = Array.from(new Set([...selectedKeys.value, ...ids]));
      Message.success(`已選取 ${ids.length} 列（分頁 ${spec}）`);
    } catch (e: any) {
      Message.error('分頁選取失敗：' + (e?.message || e));
    }
  };

  return {
    selectedKeys,
    selectedRowKeys,
    runCount,
    clearSelection,
    onSelectionChange,
    pageSpec,
    selectPages,
  };
}
