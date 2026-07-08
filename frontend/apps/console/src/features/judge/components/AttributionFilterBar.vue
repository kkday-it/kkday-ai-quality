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
import type { L1DomainOpt } from '@/api';
import {
  HAS_EXTERNAL_OPTS,
  POLARITY_FILTER_OPTS,
  SCORE_OPTS,
  STAGE_OPTS,
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
    /** L1 歸因域動態選項（有 l1 欄時必給）。 */
    l1Options?: L1DomainOpt[];
    /** 控制項尺寸。 */
    size?: 'mini' | 'small' | 'medium' | 'large';
  }>(),
  { l1Options: () => [], size: 'small' },
);

const emit = defineEmits<{ change: [] }>();
const onChange = () => emit('change');

/**
 * model prop 的同參照別名：呼叫端持有的 reactive 物件，就地 mutate 即回傳（設計見檔頂註）。
 * 用 toRef 取別名而非直接 `v-model="model.x"`，以避開 vue/no-mutating-props（別名指向同一物件，
 * 語義不變），同時保留「單一 reactive 物件、免逐欄 emit」的設計。
 */
const state = toRef(props, 'model');

const L1_OPTS = computed(() =>
  (props.l1Options ?? []).map((d) => ({ value: d.code, label: `${d.label}（${d.count}）` })),
);

/** 各欄位的 a-col flex 基寬：普通欄統一放寬（~雙倍），日期區間欄約四倍（容起迄兩日）。 */
const FIELD_FLEX: Record<FilterField, string> = {
  polarity: '190px',
  stage: '190px',
  score: '190px',
  tier: '190px',
  l1: '190px',
  hasExternal: '190px',
  dateRange: '380px',
  recOid: '190px',
  prodOid: '190px',
  orderOid: '190px',
};
const has = (f: FilterField) => props.fields.includes(f);
</script>

<template>
  <a-row :gutter="[8, 8]" align="center">
    <!-- 精確查詢 id 置前（rec/prod/order；支援逗號分隔多值一起查）-->
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
    <a-col v-if="has('score')" :flex="FIELD_FLEX.score">
      <a-select
        v-model="state.score"
        multiple
        :size="size"
        allow-clear
        :max-tag-count="2"
        placeholder="星等"
        class="w-full"
        :options="SCORE_OPTS"
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
    <a-col v-if="has('l1')" :flex="FIELD_FLEX.l1">
      <a-select
        v-model="state.l1"
        :size="size"
        allow-clear
        placeholder="L1 歸因域"
        class="w-full"
        :options="L1_OPTS"
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
    <a-col v-if="has('dateRange')" :flex="FIELD_FLEX.dateRange">
      <a-range-picker
        v-model="state.dateRange"
        :size="size"
        value-format="YYYY-MM-DD"
        class="w-full"
        :placeholder="['反饋時間起', '反饋時間迄']"
        @change="onChange"
      />
    </a-col>
    <!-- 呼叫端可插入額外控制項（分頁選取 / 計數 / 重置）於篩選欄同一 Grid 右側 -->
    <slot />
  </a-row>
</template>
