<script setup lang="ts">
import { ref, computed } from 'vue';
import { getProducts, getFindings, diagnose } from '../api/client';
import FindingCard from '../components/FindingCard.vue';

const FLABEL: Record<string, string> = {
  prod_name: '商品名稱', prod_summary: '商品說明', prod_feature: '商品特色',
  prod_schedules: '行程', pkg_desc: '套餐使用說明', pkg_schedules: '方案行程', none: '（未定位欄位）',
};

const products = ref<any[]>([]);
const sel = ref('');
const rows = ref<any[]>([]);
const prodId = ref('150665');
const loading = ref(false);
const error = ref('');
const flat = (r: any) => ({ ...r.finding, finding_id: r.finding_id, prod_oid: r.prod_oid, dimension: r.dimension, verdict: r.verdict, confidence: r.confidence, status: r.status });

const loadFindings = async () => {
  if (!sel.value) return;
  rows.value = (await getFindings({ prodOid: sel.value })).filter((r: any) => r.dimension !== 'non_content').map(flat);
};
const loadProducts = async () => {
  try {
    products.value = await getProducts();
    if (!sel.value && products.value[0]) { sel.value = products.value[0].prod_oid; await loadFindings(); }
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

const groups = computed(() => {
  const g: Record<string, any[]> = {};
  rows.value.forEach((f) => { (g[f.suspected_field] = g[f.suspected_field] || []).push(f); });
  Object.values(g).forEach((arr) =>
    arr.sort((a, b) => (b.is_primary ? 1 : 0) - (a.is_primary ? 1 : 0) || b.confidence - a.confidence),
  );
  return g;
});

loadProducts();
</script>

<template>
  <div>
    <a-alert v-if="error" type="error" style="margin-bottom: 16px">{{ error }}</a-alert>
    <a-card style="margin-bottom: 16px">
      <a-space wrap>
        <span class="muted">選擇商品</span>
        <a-select v-model="sel" style="width: 300px" placeholder="有 finding 的商品" @change="loadFindings">
          <a-option v-for="p in products" :key="p.prod_oid" :value="p.prod_oid">prod {{ p.prod_oid }} · {{ p.n }} 個問題</a-option>
        </a-select>
        <a-divider direction="vertical" />
        <a-input v-model="prodId" style="width: 160px" placeholder="新商品 prod_oid" />
        <a-button type="primary" :loading="loading" @click="run">拉評論並判決</a-button>
      </a-space>
    </a-card>

    <a-card title="商品問題清單（依欄位分組）">
      <a-empty v-if="!rows.length" description="此商品近期無客訴衍生問題（或先拉評論判決）" />
      <div v-for="(items, field) in groups" :key="field" style="margin-bottom: 18px">
        <div class="fg-head">📄 {{ FLABEL[field] || field }} <span class="muted">· {{ items.length }} 個問題</span></div>
        <FindingCard v-for="f in items" :key="f.finding_id" :f="f" />
      </div>
    </a-card>
  </div>
</template>

<style scoped>
.muted { color: #86909c; font-size: 12px; }
.fg-head { font-size: 13px; color: #165dff; font-weight: 600; margin-bottom: 9px; }
</style>
