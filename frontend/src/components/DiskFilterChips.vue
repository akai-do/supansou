<script setup>
import { computed } from 'vue'
import { DISK_FILTERS, DISK_LABELS } from '../constants.js'

const props = defineProps({
  counts: { type: Object, default: () => ({}) },
  selected: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:selected'])

const types = computed(() => {
  const keys = Object.keys(props.counts || {})
  const ordered = DISK_FILTERS.filter(t => keys.includes(t))
  const extras = keys
    .filter(t => !DISK_FILTERS.includes(t))
    .sort((a, b) => (props.counts[b] || 0) - (props.counts[a] || 0))
  return [...ordered, ...extras].filter(t => {
    // 0 结果的类型默认隐藏；已选中的保留，便于用户取消
    return (props.counts[t] || 0) > 0 || props.selected.includes(t)
  })
})

function label(t) {
  return DISK_LABELS[t] || t || '其他'
}

function toggle(t) {
  const next = props.selected.includes(t)
    ? props.selected.filter(x => x !== t)
    : [...props.selected, t]
  emit('update:selected', next)
}
</script>

<template>
  <div class="disk-chips-wrap">
    <div class="disk-chips" role="group" aria-label="网盘类型筛选">
      <button v-for="t in types" :key="t" class="chip"
              :class="{ active: selected.includes(t) }"
              @click="toggle(t)" :aria-pressed="selected.includes(t)"
              :title="`${label(t)}：${counts[t] || 0} 个资源`">
        {{ label(t) }} <span class="chip-num">{{ counts[t] || 0 }}</span>
      </button>
    </div>
    <span class="chips-hint">左右滑动</span>
  </div>
</template>
