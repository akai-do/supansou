<script>
// 资源卡片（轻量版）：状态 + 标题 + 网盘来源 + 日期 + 同名变体
// 完整链接、分享渠道、实时/历史标识及次要操作统一收进资源弹窗。
import PosterImg from './PosterImg.vue'
import AppIcon from './AppIcon.vue'
import {
  DISK_LABELS, DISK_TAG_CLASS, cardDead, cardSuspect, validityBadge,
} from '../constants.js'

export default {
  components: { PosterImg, AppIcon },
  props: {
    item: { type: Object, required: true },
    showPoster: { type: Boolean, default: true },
    poster: { type: String, default: '' },
    keyword: { type: String, default: '' },
  },
  emits: ['report', 'toast', 'open'],
  data() {
    return { activeIndex: 0 }
  },
  computed: {
    variants() { return this.item.variants || [] },
    links() { return [this.item, ...this.variants] },
    current() { return this.links[this.activeIndex] || this.item },
    dead() { return cardDead(this.item) },
    suspect() { return cardSuspect(this.item) },
    // 消息封面兜底：当前链接无图时从其他链接里找
    msgImage() {
      return this.current.msg_image ||
        this.links.map(v => v.msg_image).find(Boolean) || ''
    },
    diskLabel() { return DISK_LABELS[this.current.disk_type] || this.current.disk_type || '其他' },
    tagCls() { return DISK_TAG_CLASS[this.current.disk_type] || 'tag-other' },
    vb() { return validityBadge(this.current) },
    // 仅保留日期，降低卡片信息密度
    timeStr() {
      const ts = this.current.datetime || this.current.last_seen || ''
      if (!ts || ts.startsWith('0001')) return '时间未知'
      return ts.substring(0, 10)
    },
  },
  methods: {
    vLabel(v) { return DISK_LABELS[v.disk_type] || v.disk_type || '其他' },
    vB(v) { return validityBadge(v) },
    isDeadLink(v) {
      return (v.validity || '') === 'dead' || (v.alive === 0 && v.validity !== 'ok')
    },
    chipTitle(v, i) {
      const b = this.vB(v)
      const state = b ? b.text : (v.alive === 1 ? '未检测' : '状态未知')
      return `链接 ${i + 1}：${this.vLabel(v)} · ${state}`
    },
    selectVariant(i) {
      this.activeIndex = i
    },
    openDialog() {
      this.$emit('open', { card: this.item, item: this.current })
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
      <div class="res-head">
        <div class="res-heading">
          <span v-if="vb" class="badge res-badge" :class="vb.cls" :title="vb.title">
            {{ vb.text }}
          </span>
          <span v-else-if="current.alive === 1" class="badge res-badge badge-alive">有效</span>
          <span v-else-if="current.alive === 0" class="badge res-badge badge-dead">失效</span>
          <div class="res-title" :title="item.title">{{ item.title || '(无标题)' }}</div>
        </div>
        <button type="button" class="btn primary res-get" @click="openDialog">
          获取资源
        </button>
      </div>

      <div class="res-meta">
        <span class="disk-tag" :class="tagCls">
          <AppIcon name="cloud" :size="12" />{{ diskLabel }}
        </span>
        <span class="res-time">{{ timeStr }}</span>
      </div>

      <!-- 同名变体：主链接为 1，其余依次编号；点击切换，不刷新页面 -->
      <div v-if="links.length > 1" class="res-variant-chips" role="group"
           aria-label="同资源链接变体">
        <span class="rv-label">同名更多结果</span>
        <button v-for="(link, i) in links" :key="i" type="button"
                class="variant-chip"
                :class="{
                  active: activeIndex === i,
                  dead: isDeadLink(link),
                  suspect: (link.validity || '') === 'suspect',
                }"
                :title="chipTitle(link, i)"
                :aria-label="chipTitle(link, i)"
                :aria-pressed="activeIndex === i"
                @click="selectVariant(i)">{{ i + 1 }}</button>
      </div>
    </div>
  </div>
</template>
