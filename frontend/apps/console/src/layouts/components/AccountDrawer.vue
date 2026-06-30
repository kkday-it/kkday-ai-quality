<script setup lang="ts">
// 👤 帳號抽屜：獨立於「設定」（公共配置）之外，由 topbar 的 email chip 點擊開啟。
import { Account } from '@/features/auth/pages';

const visible = defineModel<boolean>('visible', { default: false });
</script>

<template>
  <a-drawer
    v-model:visible="visible"
    placement="right"
    title="👤 帳號"
    :width="480"
    :footer="false"
    unmount-on-close
  >
    <Account />
  </a-drawer>
</template>
