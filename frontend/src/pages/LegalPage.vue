<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { siteState } from '../site.js'

const route = useRoute()

const PAGES = {
  'clean-network': {
    title: '净网声明',
    paragraphs: [
      '本站是一个个人部署的网盘资源索引与搜索工具，只索引公开网络上已经存在的分享链接，不存储、不上传、不制作任何文件内容。',
      '本站不提供违法和不良信息，也不鼓励用户利用本站获取或传播任何违法违规内容。对于明显违法、色情、赌博、诈骗或侵犯他人权益的内容，本站会在发现后移除相关索引。',
      '请自觉遵守所在地区的法律法规与网络服务条款，合理使用搜索、链接检测与需求板功能。不要利用本站从事任何违法活动。',
    ],
  },
  copyright: {
    title: '版权说明',
    paragraphs: [
      '本站不拥有被索引资源的版权。资源标题、链接与相关信息均来自公开搜索结果，版权归原作者及权利人所有。',
      '本站仅提供搜索与链接有效性检测，不提供文件下载、转存或存储服务。资源是否可以访问、是否需要提取码，由第三方网盘平台决定。',
      '如果你认为某条索引侵犯了你的合法权益，请提供权利证明与具体链接，我们会在核实后移除相关索引。',
    ],
  },
  disclaimer: {
    title: '免责声明',
    paragraphs: [
      '本站按“现状”提供搜索与链接检测能力，不对索引内容的准确性、完整性、可用性或合法性作出保证。',
      '外部分享链接的可用性由第三方网盘平台决定，链接失效、提取码错误或平台策略变化均可能导致资源无法访问。',
      '用户应自行判断资源内容的合法性与安全性，并承担使用外部链接产生的相应风险。本站不对因使用第三方链接造成的任何损失承担责任。',
    ],
  },
}

const page = computed(() => PAGES[route.params.type] || null)
const repoUrl = computed(() => siteState.settings.footer?.repo_url || '')
</script>

<template>
  <div class="page legal-page">
    <div class="page-title">
      <h2>{{ page ? page.title : '页面不存在' }}</h2>
      <p>最后更新：2026-10-19</p>
    </div>

    <div v-if="page" class="legal-body">
      <p v-for="(p, i) in page.paragraphs" :key="i">{{ p }}</p>
      <div class="legal-feedback">
        如对本页内容有异议，或需要移除某条索引，请通过
        <a v-if="repoUrl" :href="repoUrl" target="_blank" rel="noopener">项目仓库</a>
        <span v-else>站长联系方式</span>
        反馈，并附上具体链接与说明。
      </div>
    </div>
    <div v-else class="placeholder-card">
      <h3>没有找到这个页面</h3>
      <router-link class="btn primary" to="/">返回首页</router-link>
    </div>
  </div>
</template>
