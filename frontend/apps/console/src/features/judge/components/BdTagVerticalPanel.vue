<script setup lang="ts">
/**
 * 商品垂直分類編輯面板：PM/Vertical 兩個選項池（可拖曳排序）+ BD 分工代碼 → {note, pm, vertical} 表格編輯。
 *
 * 內容結構為 `{pms:[PM名,...], verticals:[Vertical名,...], items:{bd_tag代碼:{note,pm,vertical}}}`
 * （見 config/global/bd_tag_vertical.json）。`pms`/`verticals` 為獨立可配置選項池（各自可增刪+拖曳
 * 排序，不隨 items 增減自動變動；可含尚未指派給任何代碼的新值），每個代碼的 PM/Vertical 欄從此二池
 * 下拉選擇，避免自由輸入導致的錯字/不一致（Vertical 值會直接變成前台篩選器選項，錯字風險最高）。
 * **Vertical 選項池的陣列順序＝前台歸因列表工具列篩選下拉的顯示順序**（後端 `all_verticals()` 保序
 * 回傳，`verticalFilter.store.ts` 直接沿用，不再另有一份本地順序機制）——此處拖曳排序即全站唯一
 * 排序入口。
 * 選項池用受控 `a-tag closable` 清單取代 `a-input-tag`：Arco InputTag 內部 DOM 無法掛 SortableJS，
 * 手風琴/拖排場景一律走此 workaround（見 `useListDragSort`）。
 * bd_tag 清單為表格（非手風琴）：依代碼升冪排列，`9999`（未分類/其他）固定墊底。
 * emit 介面對齊（`{json, valid}`），由 `BdTagVerticalSettingsPanel`（「配置」抽屜）包一層
 * save / 歷史 / 恢復默認版本化管線。`_comment`/`_meta` 原樣保留，不因編輯遺失。
 */
import { computed, ref, watch } from 'vue';
import { Modal } from '@arco-design/web-vue';
import { IconDelete, IconDragDotVertical, IconPlus } from '@arco-design/web-vue/es/icon';
import { ScrollFadeArea } from '@/components';
import { useListDragSort } from '@/composables';

interface BdTagVerticalItem {
  note?: string;
  pm: string;
  vertical: string;
}
interface BdTagVerticalContent {
  pms: string[];
  verticals: string[];
  items: Record<string, BdTagVerticalItem>;
  [k: string]: unknown;
}

const props = defineProps<{ content: Record<string, unknown> }>();
const emit = defineEmits<{ (e: 'change', payload: { json: unknown; valid: boolean }): void }>();

/** 深拷貝（JSON 法）。不可用 structuredClone：Vue reactive proxy 會拋 DataCloneError。 */
function deepClone<T>(o: T): T {
  return JSON.parse(JSON.stringify(o));
}

// 本地深拷貝為編輯 model（不直接改 prop）；pms/verticals 缺欄時安全補空陣列（舊版本內容相容）。
const model = ref<BdTagVerticalContent>(normalize(props.content));
const newCode = ref('');

function normalize(c: Record<string, unknown>): BdTagVerticalContent {
  const cloned = deepClone(c) as Partial<BdTagVerticalContent>;
  return {
    ...cloned,
    pms: Array.isArray(cloned.pms) ? cloned.pms : [],
    verticals: Array.isArray(cloned.verticals) ? cloned.verticals : [],
    items: cloned.items ?? {},
  };
}

watch(
  () => props.content,
  (c) => {
    model.value = normalize(c);
  },
  { immediate: true },
);

/** 代碼顯示順序：升冪排列，`9999`（未分類/其他）固定墊底。 */
const codes = computed(() =>
  Object.keys(model.value.items ?? {}).sort((a, b) => {
    if (a === '9999') return 1;
    if (b === '9999') return -1;
    return a.localeCompare(b);
  }),
);
/** 表格資料列（代碼 + 對應欄位攤平，供 a-table 直接消費；就地編輯仍透過 model.items[code] 雙向綁定）。 */
const tableRows = computed(() => codes.value.map((code) => ({ code })));
const pmOptions = computed(() => model.value.pms.map((v) => ({ value: v, label: v })));
const verticalOptions = computed(() => model.value.verticals.map((v) => ({ value: v, label: v })));

/** 結構驗證：代碼非空、pm/vertical 皆非空字串（note 選填）。 */
const valid = computed(() => {
  const items = model.value.items ?? {};
  return Object.entries(items).every(
    ([code, v]) => code.trim().length > 0 && v.pm.trim().length > 0 && v.vertical.trim().length > 0,
  );
});

/** 任一變更 → emit 整份 content（保留 _comment/_meta 等非 items/pms/verticals 欄）。 */
function commit() {
  emit('change', { json: deepClone(model.value), valid: valid.value });
}

/** 新增一列（代碼去重；已存在則忽略）。 */
function addRow() {
  const code = newCode.value.trim();
  if (!code) return;
  if (!model.value.items) model.value.items = {};
  if (code in model.value.items) return;
  model.value.items[code] = { note: '', pm: '', vertical: '' };
  newCode.value = '';
  commit();
}

/** 刪除一列。 */
function removeRow(code: string) {
  delete model.value.items[code];
  commit();
}

/**
 * 選項池變更防呆（PM/Vertical 共用）：從池中移除的值若仍被某些代碼綁定，彈二次確認；
 * 確認後才真的移除，並把那些代碼對應欄位清空（改選必填會擋存檔，逼使用者重新指派）。
 * 用 :model-value（非 v-model）手動控值——取消時不更新 model，畫面靠 model 回彈，
 * 避免 InputTag 樂觀更新導致「已經拿掉了才問你要不要拿掉」。
 * @param field 選項池欄名（pms/verticals）
 * @param itemField 對應的 item 欄名（pm/vertical）
 * @param next InputTag change 後的新陣列
 */
function applyPoolChange(field: 'pms' | 'verticals', itemField: 'pm' | 'vertical', next: string[]) {
  const current = model.value[field];
  const removed = current.filter((v) => !next.includes(v));
  if (!removed.length) {
    model.value[field] = next;
    commit();
    return;
  }
  const items = model.value.items ?? {};
  const affectedCodes = Object.entries(items)
    .filter(([, v]) => removed.includes(v[itemField]))
    .map(([code]) => code);
  if (!affectedCodes.length) {
    model.value[field] = next;
    commit();
    return;
  }
  const label = itemField === 'pm' ? 'PM' : 'Vertical';
  Modal.confirm({
    title: `刪除已綁定的 ${label}`,
    content: `「${removed.join('、')}」目前綁定在 ${affectedCodes.length} 個代碼（${affectedCodes.join('、')}）。刪除後這些代碼的 ${label} 欄會清空，需重新指派才能存檔。確定刪除？`,
    okText: '確定刪除',
    cancelText: '取消',
    onOk: () => {
      affectedCodes.forEach((code) => {
        if (removed.includes(items[code][itemField])) items[code][itemField] = '';
      });
      model.value[field] = next;
      commit();
    },
  });
}

/** 新增選項池值（Enter 送出；去空白 + 去重）。field 對應的草稿 ref 見下方 newPm/newVertical。 */
function addPoolValue(field: 'pms' | 'verticals') {
  const valueRef = field === 'pms' ? newPm : newVertical;
  const v = valueRef.value.trim();
  if (!v) return;
  if (!model.value[field].includes(v)) {
    model.value[field] = [...model.value[field], v];
    commit();
  }
  valueRef.value = '';
}
/** 移除選項池單一值（走 applyPoolChange 防呆：已綁定代碼需二次確認）。 */
function removePoolValue(field: 'pms' | 'verticals', itemField: 'pm' | 'vertical', value: string) {
  applyPoolChange(
    field,
    itemField,
    model.value[field].filter((v) => v !== value),
  );
}
const newPm = ref('');
const newVertical = ref('');

// ── 選項池拖曳排序（受控 a-tag 清單，非 a-input-tag：Arco InputTag 內部 DOM 無法掛 SortableJS）──
const pmsListRef = ref<HTMLElement | null>(null);
const verticalsListRef = ref<HTMLElement | null>(null);
useListDragSort(
  pmsListRef,
  () => model.value.pms,
  (next) => {
    model.value.pms = next;
    commit();
  },
  { draggable: '.arco-tag' },
);
useListDragSort(
  verticalsListRef,
  () => model.value.verticals,
  (next) => {
    model.value.verticals = next;
    commit();
  },
  { draggable: '.arco-tag' },
);

/** bd_tag 表格欄位（設定面板輕量小表，`pagination=false` 例外，不套 TableLayout）。
 *
 * ⚠️ 寬度總和必須 ≤ 抽屜可用寬，否則會出現橫向捲軸——`frontend-vue.md` 明令窄容器表格
 * 禁止靠橫捲硬撐。原本 220+160+160+72=612 > 640 抽屜的可用寬（約 590），實測有橫捲。
 * 現為 196+96+136+48=476（先前 550 仍有橫捲——drawer body padding 與 StickyTabs 內距吃掉的比預期多；
 * 總和壓低不吃虧：Arco 於總和 < 容器時等比拉伸，比例才是決定性的）：BD TAG 內含代碼＋可換行 textarea 故給最多；PM 值最長「N/A(Bily)」、
 * Vertical 最長「Airport Transfer」故 Vertical 略寬於 PM；操作欄只有一顆 icon 按鈕。
 * 改動任一欄或按鈕內容後，依規範用瀏覽器在 ≤1280px 視窗實量，不要估。
 */
const COLUMNS = [
  { title: 'BD TAG', slotName: 'bdTag', width: 196 },
  { title: 'PM', slotName: 'pm', width: 96 },
  { title: 'Vertical', slotName: 'vertical', width: 136 },
  { title: '操作', slotName: 'actions', width: 48 },
];
</script>

<template>
  <ScrollFadeArea class="h-full p-1" content-class="flex flex-col gap-4">
    <div class="flex flex-none flex-col gap-3 rounded-lg border p-3">
      <div>
        <div class="mb-1 text-xs font-medium">PM 選項池（拖曳排序）</div>
        <div ref="pmsListRef" class="flex flex-wrap items-center gap-1.5">
          <a-tag
            v-for="v in model.pms"
            :key="v"
            closable
            class="cursor-move"
            @close="removePoolValue('pms', 'pm', v)"
          >
            <template #icon><IconDragDotVertical /></template>
            {{ v }}
          </a-tag>
          <a-input
            v-model="newPm"
            size="mini"
            style="width: 160px"
            placeholder="輸入 PM 名稱後 Enter"
            @press-enter="addPoolValue('pms')"
          />
        </div>
      </div>
      <div>
        <div class="mb-1 text-xs font-medium">Vertical 選項池（拖曳排序＝前台篩選器顯示順序）</div>
        <div ref="verticalsListRef" class="flex flex-wrap items-center gap-1.5">
          <a-tag
            v-for="v in model.verticals"
            :key="v"
            closable
            class="cursor-move"
            @close="removePoolValue('verticals', 'vertical', v)"
          >
            <template #icon><IconDragDotVertical /></template>
            {{ v }}
          </a-tag>
          <a-input
            v-model="newVertical"
            size="mini"
            style="width: 160px"
            placeholder="輸入 Vertical 名稱後 Enter"
            @press-enter="addPoolValue('verticals')"
          />
        </div>
      </div>
      <div class="text-[11px] leading-snug text-[var(--color-text-3)]">
        兩份選項池各自獨立維護（增刪不影響下方表格既有指派）；拖曳把手調整排序，Vertical
        順序即前台歸因列表 工具列篩選下拉的顯示順序。下方表格的 PM/Vertical
        欄從此二池下拉選擇。刪除已被代碼綁定的值會二次 確認，確認後對應代碼的欄位會清空待重新指派。
      </div>
    </div>

    <div class="flex flex-none items-center gap-2">
      <a-input
        v-model="newCode"
        style="width: 160px"
        placeholder="新代碼（如 0030）"
        @press-enter="addRow"
      />
      <a-button type="primary" @click="addRow">
        <template #icon><IconPlus /></template>
        新增代碼
      </a-button>
    </div>

    <a-empty v-if="!codes.length" description="尚無代碼，於上方新增第一筆" />
    <a-table
      v-else
      :data="tableRows"
      :columns="COLUMNS"
      :pagination="false"
      row-key="code"
      size="small"
    >
      <template #bdTag="{ record }">
        <div class="flex flex-col gap-1">
          <span class="font-mono font-medium">{{ record.code }}</span>
          <a-textarea
            v-model="model.items[record.code].note"
            size="mini"
            :auto-size="{ minRows: 1, maxRows: 4 }"
            placeholder="選填，如「郊區行程」"
            @change="commit"
          />
        </div>
      </template>
      <template #pm="{ record }">
        <a-select
          v-model="model.items[record.code].pm"
          size="mini"
          placeholder="選 PM"
          allow-search
          :options="pmOptions"
          @change="commit"
        />
      </template>
      <template #vertical="{ record }">
        <a-select
          v-model="model.items[record.code].vertical"
          size="mini"
          placeholder="選 Vertical"
          allow-search
          :options="verticalOptions"
          @change="commit"
        />
      </template>
      <template #actions="{ record }">
        <a-popconfirm :content="`刪除代碼「${record.code}」？`" @ok="removeRow(record.code)">
          <a-button size="mini" type="text" status="danger">
            <template #icon><IconDelete /></template>
          </a-button>
        </a-popconfirm>
      </template>
    </a-table>
  </ScrollFadeArea>
</template>
