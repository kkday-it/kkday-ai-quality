<script setup lang="ts">
import { AccordionGroup } from '@/components';
import { ConfigJsonPanel } from '../components';
import { RULE_CONFIG_GROUPS, TAXONOMY_TREE_FILE } from '../constants';

// 🏷️ 規則 tab＝「判決邏輯層」：每一條對應一個 config/ai_judge JSON 檔（資料來源 RULE_CONFIG_GROUPS）。
// 每個 config 檔提供查看 / 編輯（ConfigJsonPanel → vanilla-jsoneditor，存回後端寫檔 + reload）。
// 新增 config 只需改 manifest，免動模板。
</script>

<template>
  <div class="flex flex-col gap-5">
    <section v-for="g in RULE_CONFIG_GROUPS" :key="g.key">
      <div class="mb-2 text-sm font-medium text-[#1d2129]">{{ g.icon }} {{ g.title }}</div>

      <AccordionGroup :default-active="g.key === 'taxonomy' ? TAXONOMY_TREE_FILE : ''">
        <a-collapse-item v-for="e in g.entries" :key="e.file" :header="e.label">
          <template #header>
            <span>{{ e.label }}</span>
            <span class="ml-2 text-xs text-[#86909c]">{{ e.desc }}</span>
          </template>

          <ConfigJsonPanel :file="e.file" />
        </a-collapse-item>
      </AccordionGroup>
    </section>
  </div>
</template>
