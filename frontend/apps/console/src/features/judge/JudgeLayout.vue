<script setup lang="ts">
// /judge 模組容器：目前僅作為子視圖（upload / analytics / product）的路由出口。
// 頂部 tab 由殼層固定渲染（見 nav.ts），故此處保持 passthrough，
// 後續若需模組層級 chrome（側欄、權限守衛等）再於此擴充。
</script>

<template>
  <router-view />
</template>
