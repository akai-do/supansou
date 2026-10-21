<script setup>
import AppIcon from './AppIcon.vue'
import { siteState } from '../site.js'

defineProps({
  title: { type: String, default: '暂无结果' },
  text: { type: String, default: '换个关键词试试，或检查一下筛选条件。' },
  showDemand: { type: Boolean, default: true },
  retry: { type: Boolean, default: false },
})
const emit = defineEmits(['retry'])
</script>

<template>
  <div class="empty-state">
    <div class="empty-icon"><AppIcon name="search" :size="40" /></div>
    <p class="empty-title">{{ title }}</p>
    <p class="empty-text">{{ text }}</p>
    <div class="empty-actions">
      <router-link v-if="showDemand && siteState.settings.features?.demand_enabled"
                   to="/demand" class="btn primary">去需求板登记</router-link>
      <button v-if="retry" class="btn" @click="emit('retry')">重试</button>
    </div>
  </div>
</template>
