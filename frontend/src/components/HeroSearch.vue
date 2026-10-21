<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  // 路由关键词（/search 用）：真变化时覆盖输入框，不干扰用户正在输入
  initial: { type: String, default: '' },
  // 未搜索态自动聚焦（/search 用）；首页不聚焦
  autoFocus: { type: Boolean, default: false },
  placeholder: { type: String, default: '搜电影、剧集、教程、软件、电子书…' },
})
const emit = defineEmits(['search'])

const kw = ref(props.initial || '')
const inputRef = ref(null)

watch(() => props.initial, (value) => {
  const next = value || ''
  if (next !== kw.value) kw.value = next
})

function submit() {
  const value = kw.value.trim()
  if (!value) return
  emit('search', value)
}

function clear() {
  kw.value = ''
  if (inputRef.value) inputRef.value.focus()
}

function maybeFocus() {
  if (!props.autoFocus || kw.value) return
  nextTick(() => inputRef.value && inputRef.value.focus())
}

onMounted(maybeFocus)
watch(() => props.autoFocus, (v) => { if (v) maybeFocus() })

defineExpose({ focus: () => inputRef.value && inputRef.value.focus() })
</script>

<template>
  <form class="hero-search" @submit.prevent="submit" role="search">
    <input ref="inputRef" v-model="kw" class="hs-input" type="search"
           :placeholder="placeholder"
           aria-label="搜索网盘资源" autocomplete="off" />
    <button v-if="kw" type="button" class="hs-clear" aria-label="清空输入" @click="clear">×</button>
    <button class="hs-btn" type="submit" aria-label="执行搜索" title="搜索">
      <AppIcon name="search" :size="18" />
    </button>
  </form>
</template>
