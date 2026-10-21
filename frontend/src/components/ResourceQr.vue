<script setup>
import { computed, ref, watch } from 'vue'
import qrcode from 'qrcode-generator'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  url: { type: String, default: '' },
  // 仅用于提示“提取码在下方单独展示”，绝不编码进二维码
  password: { type: String, default: '' },
})

const dataUrl = ref('')
const error = ref('')

// 二维码只编码“分享地址”：去掉 URL 里可能附带的提取码参数与锚点。
// 直链/复制链接仍然使用完整 URL（含提取码），不受这里影响。
const PASSWORD_KEYS = new Set(['pwd', 'password', 'pass', 'code', 'extract_code', '提取码'])

function qrTarget(raw) {
  const value = String(raw || '').trim()
  if (!/^https?:\/\//i.test(value)) return value
  try {
    const url = new URL(value)
    for (const key of [...url.searchParams.keys()]) {
      if (PASSWORD_KEYS.has(key.toLowerCase())) url.searchParams.delete(key)
    }
    url.hash = ''
    return url.toString()
  } catch (e) {
    return value
  }
}

const encodedUrl = computed(() => qrTarget(props.url))

function build() {
  dataUrl.value = ''
  error.value = ''
  const value = encodedUrl.value
  if (!value) {
    error.value = '没有可编码的分享链接'
    return
  }
  try {
    // 自动选择版本；容错级别 M。
    const qr = qrcode(0, 'M')
    qr.addData(value, 'Byte')
    qr.make()
    dataUrl.value = qr.createDataURL(6, 12)
  } catch (e) {
    error.value = '链接过长，二维码生成失败，请直接复制链接'
  }
}

watch(() => props.url, build, { immediate: true })
</script>

<template>
  <div class="resource-qr" :data-qr-url="encodedUrl">
    <div class="rq-box">
      <img v-if="dataUrl" :src="dataUrl" alt="资源分享链接二维码" class="rq-img" />
      <div v-else class="rq-fallback">
        <AppIcon name="qr" :size="34" />
        <span>{{ error || '二维码生成中…' }}</span>
      </div>
    </div>
    <p class="rq-hint">
      使用手机相机或网盘 App 扫码打开分享链接
      <span v-if="password">· 提取码请使用下方按钮复制</span>
    </p>
  </div>
</template>
