<script>
// 全局搜索条：PC 顶栏 + 移动端 dock 共用；提交后由父级路由到 /resource
// 内嵌 🔍 图标与 × 清空按钮（对齐 CloudSaver 样式）
export default {
  props: { initial: { type: String, default: '' } },
  emits: ['search'],
  data() {
    return { kw: this.initial || '' }
  },
  methods: {
    submit() {
      const kw = this.kw.trim()
      if (!kw) return
      this.$emit('search', kw)
    },
    clear() {
      this.kw = ''
      this.focus()
    },
    focus() { this.$refs.input && this.$refs.input.focus() },
  },
}
</script>

<template>
  <div class="search-dock">
    <div class="sd-field">
      <span class="sd-icon">🔍</span>
      <input ref="input" v-model="kw"
             placeholder="搜关键词，或粘贴网盘链接直接检测有效性…"
             @keydown.enter="submit" />
      <button v-if="kw" class="sd-clear" @click="clear" title="清空">×</button>
    </div>
    <button class="sd-btn" @click="submit" aria-label="执行搜索">搜索</button>
  </div>
</template>
