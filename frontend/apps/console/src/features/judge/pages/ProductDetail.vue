<script setup lang="ts">
import { ref, computed } from 'vue';
import { groupBy, orderBy, mapValues } from 'lodash-es';
import { getProducts, getFindings, diagnose } from '@/api';
import { FindingCard } from '../components';
import { FIELD_LABEL as FLABEL } from '../constants';
import { flatFinding as flat } from '../utils';

const products = ref<any[]>([]);
const sel = ref('');
const rows = ref<any[]>([]);
const prodId = ref('150665');
const loading = ref(false);
const error = ref('');

const loadFindings = async () => {
  if (!sel.value) return;
  rows.value = (await getFindings({ prodOid: sel.value }))
    .filter((r: any) => r.dimension !== 'non_content')
    .map(flat);
};
const loadProducts = async () => {
  try {
    products.value = await getProducts();
    if (!sel.value && products.value[0]) {
      sel.value = products.value[0].prod_oid;
      await loadFindings();
    }
  } catch (e: any) {
    error.value = '載入商品失敗：' + (e?.message || e);
  }
};
const run = async () => {
  loading.value = true;
  error.value = '';
  try {
    await diagnose(prodId.value);
    sel.value = prodId.value;
    await loadProducts();
    await loadFindings();
  } catch (e: any) {
    error.value = '判決失敗：' + (e?.message || e);
  } finally {
    loading.value = false;
  }
};

// 依 suspected_field 分組，組內主要欄位優先、再依信心高到低（orderBy 多欄位較手寫三元式安全）。
const groups = computed(() =>
  mapValues(groupBy(rows.value, 'suspected_field'), (arr) =>
    orderBy(arr, ['is_primary', 'confidence'], ['desc', 'desc']),
  ),
);

loadProducts();
</script>

<template>
  <div>
    <a-alert v-if="error" type="error" class="mb-4">{{ error }}</a-alert>
    <a-card class="mb-4">
      <a-space wrap>
        <span class="text-xs text-[#86909c]">選擇商品</span>
        <a-select
          v-model="sel"
          class="w-[300px]"
          placeholder="有 finding 的商品"
          @change="loadFindings"
        >
          <a-option v-for="p in products" :key="p.prod_oid" :value="p.prod_oid"
            >prod {{ p.prod_oid }} · {{ p.n }} 個問題</a-option
          >
        </a-select>
        <a-divider direction="vertical" />
        <a-input v-model="prodId" class="w-40" placeholder="新商品 prod_oid" />
        <a-button type="primary" :loading="loading" @click="run">拉評論並判決</a-button>
      </a-space>
    </a-card>

    <a-card title="商品問題清單（依欄位分組）">
      <a-empty v-if="!rows.length" description="此商品近期無客訴衍生問題（或先拉評論判決）" />
      <div v-for="(items, field) in groups" :key="field" class="mb-[18px]">
        <div class="mb-[9px] text-[13px] font-semibold text-[#165dff]">
          📄 {{ FLABEL[field] || field }}
          <span class="text-xs text-[#86909c]">· {{ items.length }} 個問題</span>
        </div>
        <FindingCard v-for="f in items" :key="f.finding_id" :f="f" />
      </div>
    </a-card>
  </div>
</template>
