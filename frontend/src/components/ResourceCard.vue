<script>
// 资源卡片：封面 + 标题 + 彩色网盘 tag + 有效性徽章 + 操作按钮组 + 变体展开
import PosterImg from './PosterImg.vue'
import {
  DISK_LABELS, DISK_TAG_CLASS, cardDead, cardSuspect, validityBadge,
} from '../constants.js'

export default {
  components: { PosterImg },
  props: {
    item: { type: Object, required: true },
    showPoster: { type: Boolean, default: true },
    poster: { type: String, default: '' },
    keyword: { type: String, default: '' },
  },
  emits: ['report', 'toast'],
  data() {
    return { expanded: false }
  },
  computed: {
    dead() { return cardDead(this.item) },
    suspect() { return cardSuspect(this.item) },
    // 消息封面兜底：主链无图时从同资源变体里找
    msgImage() {
      return this.item.msg_image ||
        (this.item.variants || []).map(v => v.msg_image).find(Boolean) || ''
    },
    diskLabel() { return DISK_LABELS[this.item.disk_type] || this.item.disk_type || '其他' },
    tagCls() { return DISK_TAG_CLASS[this.item.disk_type] || 'tag-other' },
    vb() { return validityBadge(this.item) },
    variants() { return this.item.variants || [] },
    timeStr() {
      const ts = this.item.datetime || this.item.last_seen || ''
      if (!ts || ts.startsWith('0001')) return '时间未知'
      return ts.substring(0, 16).replace('T', ' ')
    },
    openHref() { return this.outHref(this.item) },
  },
  methods: {
    outHref(it) {
      const u = it.url || ''
      if (!/^https?:\/\//i.test(u)) return u
      return `/api/click?u=${encodeURIComponent(u)}&kw=${encodeURIComponent(this.keyword || '')}` +
        `&dt=${encodeURIComponent(it.disk_type || '')}&pw=${encodeURIComponent(it.password || '')}`
    },
    vLabel(v) { return DISK_LABELS[v.disk_type] || v.disk_type || '其他' },
    vTagCls(v) { return DISK_TAG_CLASS[v.disk_type] || 'tag-other' },
    vB(v) { return validityBadge(v) },
    async copy(text) {
      try {
        await navigator.clipboard.writeText(text)
        this.$emit('toast', '已复制到剪贴板')
      } catch (e) {
        const ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        try {
          document.execCommand('copy')
          this.$emit('toast', '已复制到剪贴板')
        } catch (_) {
          this.$emit('toast', '复制失败')
        }
        ta.remove()
      }
    },
  },
}
</script>

<template>
  <div class="res-card" :class="{ 'card-dead': dead, 'card-suspect': suspect }">
    <PosterImg v-if="showPoster" class="res-poster"
               :src="poster || msgImage" :alt="item.title" :label="diskLabel"
               :start-proxy="!poster && !!msgImage" />
    <div class="res-body">
      <div class="res-title" :title="item.title">{{ item.title || '(无标题)' }}</div>
      <div class="res-meta">
        <span class="disk-tag" :class="tagCls">{{ diskLabel }}</span>
        <span class="badge" :class="item.from_archive ? 'badge-archive' : 'badge-alive'">
          {{ item.from_archive ? '历史' : '实时' }}
        </span>
        <span v-if="vb" class="badge" :class="vb.cls" :title="vb.title">{{ vb.text }}</span>
        <span v-else-if="item.from_archive" class="badge" :class="item.alive ? 'badge-alive' : 'badge-dead'">
          {{ item.alive ? '有效' : '失效' }}
        </span>
        <span class="res-src" :title="item.source">{{ item.source }}</span>
        <span class="res-time">{{ timeStr }}</span>
      </div>
      <div class="res-url">
        <a :href="openHref" target="_blank" rel="noopener">{{ item.url }}</a>
      </div>
      <div class="res-actions">
        <a class="btn primary" :href="openHref" target="_blank" rel="noopener">打开</a>
        <button class="btn" @click="copy(item.url)">复制链接</button>
        <button v-if="item.password" class="btn" @click="copy(item.password)">复制提取码</button>
        <button v-if="/^https?:\/\//i.test(item.url || '')" class="btn danger"
                :disabled="item.dead_marked" @click="$emit('report', item)">
          {{ item.dead_marked ? '✓ 已标记' : '⚠ 失效举报' }}
        </button>
        <a v-if="variants.length" class="btn ghost" href="#"
           @click.prevent="expanded = !expanded">
          {{ expanded ? '▾' : '▸' }} 同资源其他 {{ variants.length }} 个链接
        </a>
      </div>
      <div v-if="expanded && variants.length" class="res-variants">
        <div class="variant-row" v-for="(v, j) in variants" :key="j">
          <span class="disk-tag" :class="vTagCls(v)">{{ vLabel(v) }}</span>
          <a :href="outHref(v)" target="_blank" rel="noopener">{{ v.url }}</a>
          <span v-if="v.password" class="res-pw">🔑 {{ v.password }}</span>
          <span class="badge" :class="v.from_archive ? 'badge-archive' : 'badge-alive'">
            {{ v.from_archive ? '历史' : '实时' }}
          </span>
          <span v-if="vB(v)" class="badge" :class="vB(v).cls" :title="vB(v).title">{{ vB(v).text }}</span>
          <button v-if="/^https?:\/\//i.test(v.url || '')" class="btn xs danger"
                  :disabled="v.dead_marked" :title="v.dead_marked ? '已标记失效' : '失效举报'"
                  @click="$emit('report', v)">⚠</button>
        </div>
      </div>
    </div>
  </div>
</template>
