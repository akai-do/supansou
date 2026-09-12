<script>
// 海报缩略图：优先浏览器直连豆瓣图床（反爬挑战 JS 会在浏览器自动通过，
// 与 CloudSaver 同机制）；直连失败自动回退后端代理；再失败落占位块
export default {
  props: {
    src: { type: String, default: '' },     // 豆瓣图片原始 URL
    alt: { type: String, default: '' },
    label: { type: String, default: '' },   // 占位块显示字符（无图时）
  },
  data() {
    return {
      mode: 'direct',   // direct → proxy → 占位
      state: this.src ? 'loading' : 'empty',  // loading | ok | error | empty
    }
  },
  computed: {
    url() {
      if (!this.src) return ''
      if (this.mode === 'direct') return this.src
      return `/api/poster/img?u=${encodeURIComponent(this.src)}`
    },
  },
  watch: {
    src() {
      this.mode = 'direct'
      this.state = this.src ? 'loading' : 'empty'
    },
  },
  methods: {
    onErr() {
      if (this.mode === 'direct') {
        this.mode = 'proxy'
        this.state = 'loading'
      } else {
        this.state = 'error'
      }
    },
  },
}
</script>

<template>
  <div class="poster" :class="'st-' + state">
    <img v-if="src && state !== 'error'" :src="url" :alt="alt || ''"
         referrerpolicy="no-referrer" loading="lazy"
         :class="{ ld: state === 'ok' }"
         @load="state = 'ok'" @error="onErr" />
    <span v-if="state === 'error' || !src" class="poster-ph">
      {{ (alt || label || '?').trim().slice(0, 1) || '◆' }}
    </span>
  </div>
</template>
