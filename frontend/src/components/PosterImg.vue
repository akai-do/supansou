<script>
// 海报缩略图：四级加载链，任一层失败自动降级
//   1. 浏览器直连（豆瓣图床的反爬挑战 JS 会在真实浏览器自动通过）
//   2. 本地后端代理（/api/poster/img：服务端拉图 + 磁盘缓存 + 子域轮换重试）
//   3. weserv 公共图片代理（部分网络不可用，仅兜底）
//   4. 渐变占位块（由标题哈希决定配色，同一条目永远同色）
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
    // 占位配色：由标题哈希决定色相 —— 同一资源永远同一配色，滚动时不闪
    phVars() {
      const s = this.alt || this.label || ''
      let h = 0
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
      const a = h % 360
      const b = (a + 44) % 360
      // 用带透明度的 hsl 叠在主题底色上，亮/暗两套主题都不用单独配色
      return {
        '--ph-a': `hsl(${a} 58% 55% / .22)`,
        '--ph-b': `hsl(${b} 58% 45% / .36)`,
      }
    },
    // 首字：按**字素**取，跳过 emoji/标点/空白
    // （原来用 slice(0,1)，遇到 "📺 电视剧｜…" 会截出半个代理对，渲染成乱码方块）
    initial() {
      const s = (this.alt || this.label || '').trim()
      if (!s) return '◆'
      let gs
      try {
        gs = Array.from(new Intl.Segmenter(undefined, { granularity: 'grapheme' })
          .segment(s), (x) => x.segment)
      } catch (e) {
        gs = Array.from(s)   // 老浏览器回退：按码点切，至少不会截半代理对
      }
      return gs.find((g) => /[\p{L}\p{N}]/u.test(g)) || gs[0] || '◆'
    },
    showPh() {
      return this.state !== 'ok'
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
  <div class="poster" :class="'st-' + state" :style="phVars">
    <img v-if="src && state !== 'error'" :src="url" :alt="alt || ''"
         referrerpolicy="no-referrer" loading="lazy" decoding="async"
         :class="{ ld: state === 'ok' }"
         @load="state = 'ok'" @error="onErr" />
    <span v-if="showPh" class="poster-ph">{{ initial }}</span>
    <span v-if="showPh && label" class="poster-ph-tag">{{ label }}</span>
  </div>
</template>
