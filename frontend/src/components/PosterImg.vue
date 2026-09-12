<script>
// 海报缩略图：四级加载链，任一层失败自动降级
//   1. 浏览器直连（豆瓣图床的反爬挑战 JS 会在真实浏览器自动通过）
//   2. 本地后端代理（/api/poster/img：服务端拉图 + 子域轮换重试 + 内存缓存）
//   3. weserv 公共图片代理（部分网络不可用，仅兜底）
//   4. 首字占位块
// Telegram CDN 消息图国内直连不通，用 start-proxy 直接从本地代理开始
export default {
  props: {
    src: { type: String, default: '' },
    alt: { type: String, default: '' },
    label: { type: String, default: '' },
    startProxy: { type: Boolean, default: false },
  },
  data() {
    return {
      mode: this.initialMode(),
      state: this.src ? 'loading' : 'empty',  // loading | ok | error | empty
    }
  },
  computed: {
    url() {
      if (!this.src) return ''
      if (this.mode === 'direct') return this.src
      if (this.mode === 'weserv') {
        return `https://images.weserv.nl/?url=${encodeURIComponent(this.src.replace(/^https?:\/\//, ''))}`
      }
      return `/api/poster/img?u=${encodeURIComponent(this.src)}`
    },
  },
  watch: {
    src() {
      this.mode = this.initialMode()
      this.state = this.src ? 'loading' : 'empty'
    },
  },
  methods: {
    initialMode() {
      if (!this.src) return 'direct'
      // 非 douban 图床（如 Telegram CDN）默认先走本地代理
      return (this.startProxy || !/doubanio\.com/i.test(this.src)) ? 'proxy' : 'direct'
    },
    onErr() {
      if (this.mode === 'direct') { this.mode = 'proxy'; this.state = 'loading' }
      else if (this.mode === 'proxy') { this.mode = 'weserv'; this.state = 'loading' }
      else this.state = 'error'
    },
  },
}
</script>

<template>
  <div class="poster" :class="'st-' + state">
    <img v-if="src && state !== 'error'" :src="url" :alt="alt || ''"
         referrerpolicy="no-referrer" loading="lazy" decoding="async"
         :class="{ ld: state === 'ok' }"
         @load="state = 'ok'" @error="onErr" />
    <span v-if="state === 'error' || !src" class="poster-ph">
      {{ (alt || label || '?').trim().slice(0, 1) || '◆' }}
    </span>
  </div>
</template>
