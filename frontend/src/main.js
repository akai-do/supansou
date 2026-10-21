import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { initTheme } from './theme.js'
import router from './router'
import { loadSiteSettings } from './site.js'

initTheme()

// 先取一次公开站点配置，让路由守卫能正确处理 ALISO_UI=0 的回滚路径。
// 接口失败/超时会回退默认值，不会阻塞页面挂载。
loadSiteSettings().finally(() => {
  createApp(App).use(router).mount('#app')
})
