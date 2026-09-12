<script>
// 全局搜索条：PC 顶栏 + 移动端 dock 共用；提交后由父级路由到 /resource
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
    focus() { this.$refs.input && this.$refs.input.focus() },
  },
}
</script>

<template>
  <div class="search-dock">
    <input ref="input" v-model="kw"
           placeholder="搜关键词，或粘贴网盘链接直接检测有效性…"
           @keydown.enter="submit" />
    <button @click="submit" aria-label="执行搜索">搜索</button>
  </div>
</template>
