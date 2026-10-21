<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  hot: { type: Array, default: () => [] },
  history: { type: Array, default: () => [] },
  titles: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const rows = [
  { key: 'hot', label: '热门', emoji: '🔥', dir: 'left', empty: '暂无热门词，先搜一个试试' },
  { key: 'history', label: '历史', emoji: '📜', dir: 'right', empty: '暂无本机搜索历史' },
  { key: 'local', label: '本地', emoji: '📁', dir: 'left', empty: '本地索引还在积累中' },
]

// 跑马灯速度：像素 / 秒，三行统一，避免快慢不一
const SPEED = 28

const root = ref(null)
const items = {
  hot: () => props.hot.map(w => ({
    text: w.text,
    value: w.text,
    title: w.count ? `索引命中 ${w.count} 次` : '',
  })),
  history: () => props.history.map(h => ({
    text: h.keyword,
    value: h.keyword,
    title: h.last_at ? `上次搜索 ${h.last_at}` : '',
  })),
  local: () => props.titles.map(t => ({ text: t, value: t, title: '' })),
}

let observer = null

function measure() {
  const rootEl = root.value
  if (!rootEl || props.loading) return
  rootEl.querySelectorAll('.qd-scroll').forEach(el => {
    const track = el.querySelector('.qd-track')
    const group = el.querySelector('.qd-group')
    if (!track || !group) return
    // group 自带右内边距，两段拼起来的宽度正好是 group 宽度的两倍，
    // 所以动画位移 -50% 就是一整段，循环处无缝
    const distance = group.scrollWidth
    const overflow = distance > el.clientWidth + 2
    if (overflow) {
      const duration = Math.max(6, distance / SPEED)
      track.style.setProperty('--qd-duration', `${duration.toFixed(2)}s`)
      el.classList.add('is-marquee')
    } else {
      el.classList.remove('is-marquee')
      track.style.removeProperty('--qd-duration')
    }
  })
}

function pause(el) { el.classList.add('is-paused') }
function resume(el) { el.classList.remove('is-paused') }

onMounted(() => {
  root.value?.querySelectorAll('.qd-scroll').forEach(el => {
    el.addEventListener('pointerenter', () => pause(el))
    el.addEventListener('pointerleave', () => resume(el))
    el.addEventListener('pointercancel', () => resume(el))
    el.addEventListener('touchstart', () => pause(el), { passive: true })
    el.addEventListener('touchend', () => resume(el), { passive: true })
    el.addEventListener('focusin', () => pause(el))
    el.addEventListener('focusout', () => resume(el))
  })

  measure()
  // 字体加载完、窗口缩放、数据换行后都要重新量一次宽度
  observer = new ResizeObserver(() => measure())
  if (root.value) observer.observe(root.value)
  root.value?.querySelectorAll('.qd-scroll').forEach(el => observer.observe(el))
})

watch(() => [props.loading, props.hot, props.history, props.titles], () => {
  nextTick(measure)
})

onUnmounted(() => {
  observer?.disconnect()
  observer = null
})
</script>

<template>
  <section ref="root" class="quick-discovery" aria-label="快捷发现">
    <div v-for="row in rows" :key="row.key" class="qd-row">
      <div class="qd-label">
        <span class="qd-emoji" aria-hidden="true">{{ row.emoji }}</span>
        <span>{{ row.label }}</span>
      </div>
      <div class="qd-scroll" :data-dir="row.dir">
        <div v-if="loading" class="qd-track">
          <div class="qd-group">
            <span v-for="i in 6" :key="i" class="qd-chip qd-skeleton"></span>
          </div>
        </div>
        <div v-else-if="items[row.key]().length" class="qd-track">
          <div v-for="copy in 2" :key="copy" class="qd-group"
               :class="{ 'qd-group--clone': copy === 2 }"
               :aria-hidden="copy === 2 ? 'true' : undefined">
            <button v-for="(item, i) in items[row.key]()" :key="row.key + '-' + copy + '-' + i"
                    class="qd-chip"
                    :tabindex="copy === 2 ? -1 : undefined"
                    :title="item.title"
                    @click="emit('select', item.value)">
              {{ item.text }}
            </button>
          </div>
        </div>
        <span v-else class="qd-empty">{{ row.empty }}</span>
      </div>
    </div>
  </section>
</template>
