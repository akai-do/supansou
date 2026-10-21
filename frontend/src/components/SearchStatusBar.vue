<script setup>
import { computed } from 'vue'

const props = defineProps({
  loading: { type: Boolean, default: false },
  filtering: { type: Boolean, default: false },
  elapsedMs: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  diskTypeCount: { type: Number, default: 0 },
  cached: { type: Boolean, default: false },
  hiddenDeadCount: { type: Number, default: 0 },
  checking: { type: Boolean, default: false },
  checkedKnown: { type: Number, default: 0 },
  checkedTotal: { type: Number, default: 0 },
})

const seconds = computed(() => {
  const ms = Math.max(0, Number(props.elapsedMs) || 0)
  return (ms / 1000).toFixed(1)
})
</script>

<template>
  <div class="search-status" :class="{ 'is-loading': loading }" aria-live="polite">
    <span v-if="loading" class="ss-main">
      <span class="spinner"></span>
      {{ filtering ? '筛选中' : '搜索中' }}… 已用时 {{ seconds }}s
    </span>
    <span v-else class="ss-main">
      当前显示 <b>{{ total }}</b> 个资源 · <b>{{ diskTypeCount }}</b> 类网盘 · 用时 <b>{{ seconds }}</b>s
    </span>

    <span v-if="cached" class="ss-dim" title="TTL 内直接读取缓存">⚡ 缓存</span>
    <span v-if="checking" class="ss-check">
      🩺 检测中 {{ checkedKnown }}/{{ checkedTotal }}
    </span>
    <span v-if="hiddenDeadCount > 0" class="ss-dead">
      已隐藏 {{ hiddenDeadCount }} 条失效
    </span>
  </div>
</template>
