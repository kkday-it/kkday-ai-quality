<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

// 視圖 tab：由殼層渲染於固定 topbar；tab key＝路由路徑，點擊即導航，路由變動回填高亮。
defineProps<{ tabs: ReadonlyArray<{ key: string; label: string }> }>();

const route = useRoute();
const router = useRouter();
const activeTab = ref(route.path);
watch(
  () => route.path,
  (p) => (activeTab.value = p),
);
const onTab = (key: string | number) => router.push(String(key));
</script>

<template>
  <a-tabs
    :active-key="activeTab"
    type="line"
    class="border-b border-[#f0f0f0] bg-white px-3"
    @change="onTab"
  >
    <a-tab-pane v-for="t in tabs" :key="t.key" :title="t.label" />
  </a-tabs>
</template>

<style scoped>
/* Arco tabs 內部底線偽元素，utility 與元件 prop 皆無法觸及；隱藏以與自訂 border-b 對齊。 */
:deep(.arco-tabs-nav)::before {
  display: none;
}
</style>
