import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { initTheme } from './theme.js'
import router from './router'

initTheme()
createApp(App).use(router).mount('#app')
