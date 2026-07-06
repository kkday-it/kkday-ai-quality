// 跨 feature 共用「清單拖動排序」composable：對容器元素掛 SortableJS，拖放結束後
// 先還原 Sortable 的 DOM 搬移（交還 Vue 以資料驅動重渲，避免 keyed patch 與被搬節點衝突），
// 再以重排後的新陣列呼叫 commit 由呼叫端持久化。
// 選 sortablejs 直用而非 @vueuse/integrations useSortable：後者僅 mount 時初始化一次，
// 面板清單常掛在 v-if 之後（設定抽屜 lazy 載入），需要「元素出現/更換即重掛」的 watch 語義。
import { onBeforeUnmount, toValue, watch, type MaybeRefOrGetter } from 'vue';
import Sortable, { type SortableEvent } from 'sortablejs';

/**
 * 依拖放事件回傳重排後的新陣列（純函式，不改原陣列）。
 * @param list 原清單
 * @param evt SortableJS 事件（oldIndex/newIndex 以 draggable 節點集合計算）
 */
export function reorderByDragEvent<T>(list: readonly T[], evt: SortableEvent): T[] {
  const { oldIndex, newIndex } = evt;
  const next = [...list];
  if (oldIndex == null || newIndex == null || oldIndex === newIndex) return next;
  const [moved] = next.splice(oldIndex, 1);
  next.splice(newIndex, 0, moved);
  return next;
}

/**
 * 還原 SortableJS 對 DOM 的搬移（把被拖節點插回原位），使後續 Vue keyed 重渲不受污染。
 * @param evt SortableJS 事件
 * @param childSel 可拖節點的子選擇器（與 Sortable options.draggable 對齊；預設任意直接子元素）
 */
export function revertSortableDom(evt: SortableEvent, childSel = '*'): void {
  const { item, from, oldIndex } = evt;
  if (oldIndex == null) return;
  const siblings = Array.from(from.querySelectorAll(`:scope > ${childSel}`)).filter(
    (n) => n !== item
  );
  from.insertBefore(item, siblings[oldIndex] ?? null);
}

/**
 * 清單拖動排序：容器出現/更換即（重）掛 SortableJS；拖放結束 → 還原 DOM → commit(新陣列)。
 * @param el 容器元素（拖曳項的直接父層；可為 getter，v-if 內容出現時自動掛載）
 * @param list 目前清單快照 getter（commit 前重取，避免閉包舊值）
 * @param commit 收到重排後新陣列（由呼叫端持久化；Vue 重渲由資料驅動）
 * @param options handle＝拖曳把手選擇器；draggable＝可拖節點選擇器（預設容器全部直接子元素）
 */
export function useListDragSort<T>(
  el: MaybeRefOrGetter<HTMLElement | null | undefined>,
  list: () => readonly T[],
  commit: (next: T[]) => void | Promise<void>,
  options: { handle?: string; draggable?: string } = {}
): void {
  const childSel = options.draggable ?? '*';
  let inst: Sortable | null = null;
  const destroy = () => {
    inst?.destroy();
    inst = null;
  };
  watch(
    () => toValue(el),
    (node) => {
      destroy();
      if (!node) return;
      inst = new Sortable(node, {
        animation: 150,
        handle: options.handle,
        draggable: options.draggable,
        onEnd: (evt) => {
          const { oldIndex, newIndex } = evt;
          if (oldIndex == null || newIndex == null || oldIndex === newIndex) return;
          revertSortableDom(evt, childSel);
          void commit(reorderByDragEvent(list(), evt));
        },
      });
    },
    { immediate: true, flush: 'post' }
  );
  onBeforeUnmount(destroy);
}
