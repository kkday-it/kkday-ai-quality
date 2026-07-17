<script setup lang="ts">
/**
 * 指標資料來源表：指標 → 落後/領先 → DAP 來源表 → 外部儀表板連結。
 * url 為空時連結欄顯示「—」，避免渲染空 href。
 */
import { IconLaunch } from '@arco-design/web-vue/es/icon';
import type { SourceRow } from '../types';

defineProps<{ rows: SourceRow[] }>();
</script>

<template>
  <a-table :data="rows" :pagination="false" :bordered="{ cell: true }" size="small">
    <template #columns>
      <a-table-column title="指標" data-index="metric" :width="200" />
      <a-table-column title="類型" :width="100">
        <template #cell="{ record }">
          <a-tag size="small" :color="record.kind === '落後指標' ? 'red' : 'arcoblue'">{{
            record.kind
          }}</a-tag>
        </template>
      </a-table-column>
      <a-table-column title="DAP 資料來源">
        <template #cell="{ record }">
          <code class="text-xs text-[#4e5969]">{{ record.dapTable }}</code>
        </template>
      </a-table-column>
      <a-table-column title="外部儀表板" :width="160">
        <template #cell="{ record }">
          <a-link
            v-if="record.url"
            :href="record.url"
            target="_blank"
            :hoverable="false"
            class="text-xs"
          >
            {{ record.dashboard }}<icon-launch class="ml-0.5" />
          </a-link>
          <span v-else class="text-xs text-[#c9cdd4]">—</span>
        </template>
      </a-table-column>
    </template>
  </a-table>
</template>
