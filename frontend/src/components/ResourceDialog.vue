<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import ResourceQr from './ResourceQr.vue'
import AppIcon from './AppIcon.vue'
import { DISK_LABELS, DISK_TAG_CLASS, validityBadge } from '../constants.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  card: { type: Object, default: null },
  item: { type: Object, default: null },
  keyword: { type: String, default: '' },
})
const emit = defineEmits(['close', 'toast', 'report'])

const current = computed(() => props.item || props.card || {})
const title = computed(() => props.card?.title || current.value.title || '(无标题)')
const diskType = computed(() => current.value.disk_type || '')
const diskLabel = computed(() => DISK_LABELS[diskType.value] || diskType.value || '其他')
const tagCls = computed(() => DISK_TAG_CLASS[diskType.value] || 'tag-other')
const badge = computed(() => validityBadge(current.value))
const url = computed(() => String(current.value.url || ''))
const password = computed(() => String(current.value.password || ''))
const canOpen = computed(() => /^https?:\/\//i.test(url.value))

const openHref = computed(() => {
  const u = url.value
  if (!canOpen.value) return u
  return `/api/click?u=${encodeURIComponent(u)}&kw=${encodeURIComponent(props.keyword || '')}` +
    `&dt=${encodeURIComponent(current.value.disk_type || '')}` +
    `&pw=${encodeURIComponent(current.value.password || '')}` +
    `&src=${encodeURIComponent(current.value.source || '')}`
})

function close() { emit('close') }

async function copy(text) {
  const value = String(text || '')
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    emit('toast', '已复制到剪贴板')
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = value
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      emit('toast', '已复制到剪贴板')
    } catch (_) {
      emit('toast', '复制失败')
    }
    ta.remove()
  }
}

function report() {
  emit('report', current.value)
  close()
}

function onKeydown(e) {
  if (props.open && e.key === 'Escape') close()
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="open" class="dialog-overlay" @click.self="close">
    <div class="resource-dialog" role="dialog" aria-modal="true"
         aria-labelledby="rd-title">
      <div class="rd-head">
        <div>
          <h3 id="rd-title">{{ title }}</h3>
          <div class="rd-meta">
            <span class="disk-tag" :class="tagCls">{{ diskLabel }}</span>
            <span v-if="badge" class="badge" :class="badge.cls" :title="badge.title">{{ badge.text }}</span>
            <span v-if="current.source" class="rd-source">{{ current.source }}</span>
          </div>
        </div>
        <button class="rd-close" @click="close" aria-label="关闭">
          <AppIcon name="close" :size="18" />
        </button>
      </div>

      <div class="rd-body">
        <ResourceQr :url="url" :password="password" />

        <div class="rd-detail">
          <div class="rd-row">
            <span class="rd-label">分享链接</span>
            <a v-if="canOpen" class="rd-url" :href="openHref" target="_blank"
               rel="noopener">{{ url }}</a>
            <span v-else class="rd-url">{{ url || '—' }}</span>
          </div>
          <div class="rd-row" v-if="password">
            <span class="rd-label">提取码</span>
            <code class="rd-pw">{{ password }}</code>
            <button class="btn xs" @click="copy(password)">复制</button>
          </div>
          <div class="rd-row" v-if="current.datetime || current.last_seen">
            <span class="rd-label">时间</span>
            <span>{{ String(current.datetime || current.last_seen).substring(0, 16).replace('T', ' ') }}</span>
          </div>
        </div>
      </div>

      <div class="rd-actions">
        <a v-if="canOpen" class="btn primary" :href="openHref" target="_blank" rel="noopener">
          <AppIcon name="link" :size="15" /> 打开链接
        </a>
        <button class="btn" @click="copy(url)">
          <AppIcon name="copy" :size="15" /> 复制链接
        </button>
        <button v-if="password" class="btn" @click="copy(password)">
          <AppIcon name="copy" :size="15" /> 复制提取码
        </button>
        <button v-if="canOpen" class="btn danger" @click="report">
          <AppIcon name="close" :size="14" /> 失效举报
        </button>
      </div>
      <p class="rd-note">直链与复制是主路径；二维码只编码分享链接，不包含提取码或账号信息。</p>
    </div>
  </div>
</template>
