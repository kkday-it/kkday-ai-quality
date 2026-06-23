<script setup lang="ts">
import { ref } from 'vue';
import { getFindings, diagnose, patchStatus } from '../api/client';

const prodId = ref('150665');
const rows = ref<any[]>([]);
const loading = ref(false);

const load = async () => {
  rows.value = await getFindings(prodId.value);
};
const run = async () => {
  loading.value = true;
  try {
    await diagnose(prodId.value);
    await load();
  } finally {
    loading.value = false;
  }
};
const setStatus = async (row: any, s: string) => {
  await patchStatus(row.finding_id, s);
  row.status = s;
};

const color = (v: string) =>
  ({
    content_unclear: 'orange',
    content_missing: 'red',
    real_config_issue: 'magenta',
    customer_misread: 'gray',
    escalate_ops: 'blue',
  } as Record<string, string>)[v] || 'gray';

load();
</script>

<template>
  <a-card>
    <a-space>
      <a-input v-model="prodId" style="width: 220px" placeholder="商品 prod_oid（如 150665）" />
      <a-button type="primary" :loading="loading" @click="run">拉評論並判決</a-button>
      <a-button @click="load">查詢結果</a-button>
    </a-space>

    <a-empty v-if="!rows.length" style="margin-top: 40px" description="尚無判決，輸入 prod_oid 後點「拉評論並判決」" />

    <a-list v-else :data="rows" style="margin-top: 16px">
      <template #item="{ item }">
        <a-list-item>
          <a-card style="width: 100%">
            <a-space wrap>
              <a-tag :color="color(item.verdict)">{{ item.verdict }}</a-tag>
              <a-tag>{{ item.dimension }}</a-tag>
              <a-tag v-if="item.finding?.suspected_field && item.finding.suspected_field !== 'none'" color="arcoblue">
                {{ item.finding.suspected_field }}
              </a-tag>
              <a-tag :color="item.status === 'confirmed' ? 'green' : item.status === 'fixed' ? 'cyan' : item.status === 'dismissed' ? 'gray' : undefined">
                {{ item.status }}
              </a-tag>
              <span style="color: #86909c; font-size: 12px">信心 {{ (item.confidence ?? 0).toFixed?.(2) ?? item.confidence }}</span>
            </a-space>
            <p style="margin: 8px 0">{{ item.finding?.problem_summary }}</p>
            <p style="color: #86909c; font-size: 13px">
              建議：<b>{{ item.finding?.recommended_action }}</b> — {{ item.finding?.action_detail }}
            </p>
            <a-space>
              <a-button size="mini" type="outline" status="success" @click="setStatus(item, 'confirmed')">確認</a-button>
              <a-button size="mini" type="outline" @click="setStatus(item, 'dismissed')">忽略</a-button>
              <a-button size="mini" type="outline" status="warning" @click="setStatus(item, 'fixed')">已修</a-button>
            </a-space>
          </a-card>
        </a-list-item>
      </template>
    </a-list>
  </a-card>
</template>
