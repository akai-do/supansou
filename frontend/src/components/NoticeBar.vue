<script setup>
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  notice: { type: Object, default: () => ({ enabled: false, content: '', version: '' }) },
})

const visible = ref(false)

function storageKey() {
  return 'dps-notice-seen-' + (props.notice.version || 'default')
}

function sync() {
  if (!props.notice || !props.notice.enabled || !props.notice.content) {
    visible.value = false
    return
  }
  let seen = ''
  try { seen = localStorage.getItem(storageKey()) || '' } catch (e) { /* ignore */ }
  visible.value = seen !== '1'
}

function close() {
  visible.value = false
  try { localStorage.setItem(storageKey(), '1') } catch (e) { /* ignore */ }
}

watch(() => [props.notice.enabled, props.notice.content, props.notice.version], sync, {
  immediate: true,
})

const content = computed(() => props.notice?.content || '')
</script>

<template>
  <div v-if="visible" class="notice-bar" role="status">
    <span class="nb-icon"><AppIcon name="demand" :size="16" /></span>
    <p class="nb-text">{{ content }}</p>
    <button class="nb-close" @click="close" aria-label="关闭公告" title="关闭公告">
      <AppIcon name="close" :size="15" />
    </button>
  </div>
</template>
