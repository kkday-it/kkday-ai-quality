<script setup lang="ts">
/**
 * 歸因列表篩選欄共用元件：三處（工具列 / 導出彈窗 / 初判目標篩選）共用此一份 UI，
 * 新增／調整篩選欄只需改本檔（單一真相）。控制項依 `fields` 條件渲染，直接雙向改 reactive `model`，
 * 任一控制項變更 emit `change`（呼叫端據此 reload / refreshCount）。
 *
 * 設計：model 為呼叫端持有的 reactive AttributionFilters，本元件就地 mutate（同一參照，變更即傳回）——
 * 避免 10 欄各寫 v-model/emit 的樣板；options 自 constant 衍生，l1 動態選項由 prop 注入。
 */
import { computed, toRef } from 'vue';
import type { CascadeNode } from '@/api';
import {
  HAS_EXTERNAL_OPTS,
  POLARITY_FILTER_OPTS,
  STAGE_OPTS,
  STATUS_OPTS,
  TIER_OPTS,
  type AttributionFilters,
  type FilterField,
} from '../constants';

const props = withDefaults(
  defineProps<{
    /** 篩選狀態（reactive；本元件就地 mutate，同參照傳回呼叫端）。 */
    model: AttributionFilters;
    /** 要渲染的欄位（順序即顯示順序）。 */
    fields: FilterField[];
    /** 歸因分類級聯選項（L1→L2 樹；有 taxonomy 欄時必給，來自 getTaxonomyCascade）。 */
    cascadeOptions?: CascadeNode[];
    /** 判決模型選項（有 model 欄時必給，來自 getJudgmentModels；注入模式，元件不發請求）。 */
    modelOptions?: { value: string; label: string }[];
    /** 控制項尺寸。 */
    size?: 'mini' | 'small' | 'medium' | 'large';
  }>(),
  { cascadeOptions: () => [], modelOptions: () => [], size: 'small' },
);

const emit = defineEmits<{ change: [] }>();
const onChange = () => emit('change');

/**
 * model prop 的同參照別名：呼叫端持有的 reactive 物件，就地 mutate 即回傳（設計見檔頂註）。
 * 用 toRef 取別名而非直接 `v-model="model.x"`，以避開 vue/no-mutating-props（別名指向同一物件，
 * 語義不變），同時保留「單一 reactive 物件、免逐欄 emit」的設計。
 */
const state = toRef(props, 'model');

/** 各欄位的 a-col flex 基寬：統一放寬（~雙倍）；dateRange 拆成起/迄兩個獨立 date-picker，各占一欄。 */
const FIELD_FLEX: Record<FilterField, string> = {
  polarity: '190px',
  stage: '190px',
  tier: '190px',
  status: '190px',
  model: '210px',
  taxonomy: '230px',
  hasExternal: '190px',
  dateRange: '190px',
  recOid: '190px',
  prodOid: '190px',
  orderOid: '190px',
};
const has = (f: FilterField) => props.fields.includes(f);

/** 兩行分組：第一行＝精確查詢 id + 反饋時間；第二行＝各下拉維度篩選。只渲染 fields 命中的欄。 */
const PRIMARY_FIELDS: FilterField[] = ['recOid', 'prodOid', 'orderOid', 'dateRange'];
const SECONDARY_FIELDS: FilterField[] = [
  'polarity',
  'stage',
  'tier',
  'status',
  'model',
  'taxonomy',
  'hasExternal',
];
const hasPrimary = computed(() => PRIMARY_FIELDS.some((f) => props.fields.includes(f)));
const hasSecondary = computed(() => SECONDARY_FIELDS.some((f) => props.fields.includes(f)));

/**
 * 反饋時間起 / 迄：拆成兩個獨立 a-date-picker，支援只填單邊（只起 / 只迄 / 起～迄）。
 * 底層仍存 model.dateRange=[from, to]（兩端皆空時歸零為 []），filtersToParams 據此各自送 dateFrom/dateTo。
 */
const dateFrom = computed<string | undefined>({
  get: () => state.value.dateRange?.[0] || undefined,
  set: (v) => setDate(0, v),
});
const dateTo = computed<string | undefined>({
  get: () => state.value.dateRange?.[1] || undefined,
  set: (v) => setDate(1, v),
});
/** 寫入某一端日期並正規化 dateRange（兩端皆空 → []；起 > 迄自動對調），變更後 emit change。 */
function setDate(idx: 0 | 1, v: string | undefined): void {
  const next: [string, string] = [
    state.value.dateRange?.[0] ?? '',
    state.value.dateRange?.[1] ?? '',
  ];
  next[idx] = v ?? '';
  // disabledDate 已擋面板選取；此為鍵盤輸入等繞過面板路徑的兜底（YYYY-MM-DD 字串可直接比大小）
  if (next[0] && next[1] && next[0] > next[1]) next.reverse();
  state.value.dateRange = next[0] || next[1] ? next : [];
  onChange();
}

/** Date → 本地 'YYYY-MM-DD'（與 value-format 對齊，供比較與快捷設值）。 */
function fmtDay(d: Date): string {
  const pad = (v: number): string => String(v).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 起/迄互斥禁選：起 picker 禁選晚於迄、迄 picker 禁選早於起（另一端未填則不限制）。 */
const disableFromDate = (current?: Date): boolean =>
  !!current && !!dateTo.value && fmtDay(current) > dateTo.value;
const disableToDate = (current?: Date): boolean =>
  !!current && !!dateFrom.value && fmtDay(current) < dateFrom.value;

/** 反饋時間快捷：近 N 天（含今天共 N 天）。a-range-picker 兩端必填（Arco Vue 無 allowEmpty），故日期用兩個獨立 picker + 此快捷。 */
const DATE_SHORTCUTS: { label: string; days: number }[] = [
  { label: '近7天', days: 7 },
  { label: '近30天', days: 30 },
  { label: '近90天', days: 90 },
  { label: '近365天', days: 365 },
];
/** 設反饋時間為近 n 天 [今天-(n-1), 今天]（本地日期 YYYY-MM-DD），emit change。 */
function applyRecentDays(n: number): void {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - (n - 1));
  state.value.dateRange = [fmtDay(from), fmtDay(to)];
  onChange();
}
</script>

<template>
  <div>
    <!-- 第一行：精確查詢 id（rec/prod/order，逗號分隔多值）+ 反饋時間（起/迄可只填單邊）-->
    <a-row v-if="hasPrimary" :gutter="[8, 8]" align="center" :class="{ 'mb-2': hasSecondary }">
      <a-col v-if="has('recOid')" :flex="FIELD_FLEX.recOid">
        <a-input
          v-model="state.recOid"
          :size="size"
          allow-clear
          class="w-full"
          placeholder="評論 rec_oid 如 1,2,3"
          @press-enter="onChange"
          @clear="onChange"
        />
      </a-col>
      <a-col v-if="has('prodOid')" :flex="FIELD_FLEX.prodOid">
        <a-input
          v-model="state.prodOid"
          :size="size"
          allow-clear
          class="w-full"
          placeholder="商品 prod_oid 如 1,2,3"
          @press-enter="onChange"
          @clear="onChange"
        />
      </a-col>
      <a-col v-if="has('orderOid')" :flex="FIELD_FLEX.orderOid">
        <a-input
          v-model="state.orderOid"
          :size="size"
          allow-clear
          class="w-full"
          placeholder="訂單 order_oid 如 1,2,3"
          @press-enter="onChange"
          @clear="onChange"
        />
      </a-col>
      <!-- 反饋時間：拆兩個獨立 date-picker，只起 / 只迄 / 起～迄 皆可 -->
      <template v-if="has('dateRange')">
        <a-col :flex="FIELD_FLEX.dateRange">
          <a-date-picker
            v-model="dateFrom"
            :size="size"
            allow-clear
            value-format="YYYY-MM-DD"
            class="w-full"
            placeholder="反饋時間起"
            :disabled-date="disableFromDate"
          />
        </a-col>
        <a-col :flex="FIELD_FLEX.dateRange">
          <a-date-picker
            v-model="dateTo"
            :size="size"
            allow-clear
            value-format="YYYY-MM-DD"
            class="w-full"
            placeholder="反饋時間迄"
            :disabled-date="disableToDate"
          />
        </a-col>
        <!-- 近 N 天快捷：一鍵設定起訖 -->
        <a-col flex="none">
          <a-button-group :size="size">
            <a-button v-for="s in DATE_SHORTCUTS" :key="s.days" @click="applyRecentDays(s.days)">
              {{ s.label }}
            </a-button>
          </a-button-group>
        </a-col>
      </template>
    </a-row>

    <!-- 第二行：各下拉維度篩選 -->
    <a-row v-if="hasSecondary" :gutter="[8, 8]" align="center">
      <a-col v-if="has('polarity')" :flex="FIELD_FLEX.polarity">
        <a-select
          v-model="state.polarity"
          multiple
          :size="size"
          :max-tag-count="1"
          placeholder="情緒傾向"
          class="w-full"
          :options="POLARITY_FILTER_OPTS"
          @change="onChange"
        />
      </a-col>
      <a-col v-if="has('stage')" :flex="FIELD_FLEX.stage">
        <a-select
          v-model="state.stage"
          multiple
          :size="size"
          :max-tag-count="1"
          placeholder="判決階段"
          class="w-full"
          :options="STAGE_OPTS"
          @change="onChange"
        />
      </a-col>
      <a-col v-if="has('tier')" :flex="FIELD_FLEX.tier">
        <a-select
          v-model="state.tier"
          :size="size"
          allow-clear
          placeholder="信心分層"
          class="w-full"
          :options="TIER_OPTS"
          @change="onChange"
        />
      </a-col>
      <a-col v-if="has('status')" :flex="FIELD_FLEX.status">
        <a-select
          v-model="state.status"
          multiple
          :size="size"
          :max-tag-count="1"
          placeholder="覆核狀態"
          class="w-full"
          :options="STATUS_OPTS"
          @change="onChange"
        />
      </a-col>
      <a-col v-if="has('model')" :flex="FIELD_FLEX.model">
        <a-select
          v-model="state.model"
          multiple
          :size="size"
          :max-tag-count="1"
          placeholder="判決模型"
          class="w-full"
          :options="modelOptions"
          @change="onChange"
        />
      </a-col>
      <!-- 歸因分類：L1→L2 級聯複選。check-strictly＝任意層級可獨立勾選（值＝該節點 code），
           選 L1/L2 即代表整個子樹（後端 l1/l2_code 任一 IN 命中）。 -->
      <a-col v-if="has('taxonomy')" :flex="FIELD_FLEX.taxonomy">
        <a-cascader
          v-model="state.taxonomy"
          multiple
          check-strictly
          :size="size"
          allow-clear
          :max-tag-count="1"
          placeholder="歸因分類"
          class="w-full"
          :options="cascadeOptions"
          @change="onChange"
        />
      </a-col>
      <a-col v-if="has('hasExternal')" :flex="FIELD_FLEX.hasExternal">
        <a-select
          v-model="state.hasExternal"
          :size="size"
          allow-clear
          placeholder="外部評論"
          class="w-full"
          :options="HAS_EXTERNAL_OPTS"
          @change="onChange"
        />
      </a-col>
      <!-- 呼叫端可插入額外控制項（計數 / 重置）於第二行右側 -->
      <slot />
    </a-row>
  </div>
</template>
