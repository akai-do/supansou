<script setup>
import { useRoute } from 'vue-router'
import AppShell from './components/AppShell.vue'

const route = useRoute()
</script>

<template>
  <!-- 后台使用独立布局，不套公开站点的头部/页脚 -->
  <router-view v-if="route.path.startsWith('/admin')" />
  <AppShell v-else />
</template>
