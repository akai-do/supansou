<script>
// 下载提速页（站长）：自研选择性 Hook 的注入控制 + 闭环调速 + 实时速率曲线
// 原理：hook 百度客户端的时间 API 解除本地限速；本页监控真实速率并自动调倍率
import { API, ACCEL_DOWNLOAD_URL, ACCEL_DOWNLOAD_MIRROR, ACCEL_GUIDE_URL } from '../constants.js'

export default {
  data() {
    return {
      available: null,        // null = 还不知道（首次请求未返回）；别拿"未知"当"不可用"
      unavailableReason: '',
      loadError: null,        // 请求本身失败（后端不可达），与"组件不可用"是两回事
      localBase: '',          // '' = 用本站后端；否则 'http://127.0.0.1:5000'
      localTried: false,
      targets: [],
      injected: 0,
      enabled: false,
      factor: 5,
      auto: false,
      target: 8,
      state: '',              // 空 = 尚未从后端拿到，避免先闪一下 IDLE
      speedSrc: '',
      winSec: 60,
      speed: 0,               // B/s
      serverCooldown: 0,
      log: [],
      busy: false,
      error: null,
      history: [],            // { t, speed } 曲线数据
      _timer: null,
      _toastTimer: null,
      toast: '',
      canvas: null,
    }
  },
  computed: {
    // 加速开关的按钮文案：未注入 → 注入并开启；已开 → 关闭；已关 → 开启
    accelLabel() {
      if (!this.injected) return '注入并开启加速'
      return this.enabled ? '关闭加速' : '开启加速'
    },
    accelOn() {
      return this.injected && this.enabled
    },
    // 是否在"云端站点"上、且没探测到本机加速服务 → 需要引导下载本机小程序
    needLocalAgent() {
      const h = location.hostname
      const isLocalPage = h === '127.0.0.1' || h === 'localhost'
      return !isLocalPage && this.localTried && !this.localBase
    },
    // 当前控制的是哪台机器的加速服务（本机 / 本站，仅作提示）
    backendLabel() {
      return this.localBase ? '本机加速服务' : '本站后端'
    },
    // ⚠ Options API 下 import 进来的常量**不会自动暴露给模板**（只有 <script setup> 会），
    // 必须在 computed 里转一道；否则模板里拿到 undefined、下载按钮点了没反应。
    accelDownloadUrl() { return ACCEL_DOWNLOAD_URL },
    accelDownloadMirror() { return ACCEL_DOWNLOAD_MIRROR },
    accelGuideUrl() { return ACCEL_GUIDE_URL },
    speedText() {
      const b = this.speed
      if (b >= 1e6) return (b / 1e6).toFixed(2) + ' MB/s'
      return (b / 1e3).toFixed(0) + ' KB/s'
    },
    stateText() {
      const m = {
        RAMP: '爬坡中', HOLD: '维持中', BACKOFF: '塌陷退避',
        RECOVER: '恢复中', 'SERVER_LIMITED': '服务端限速',
      }
      return m[this.state] || this.state || '—'
    },
  },
  mounted() {
    // 先探测本机后端：加速只能在本机执行，云端页面需要反向调 127.0.0.1
    this.probeLocal().then(() => this.refresh())
    this._timer = setInterval(() => {
      if (!document.hidden) this.refresh()
    }, 1000)
    window.addEventListener('resize', this.drawCurve)
  },
  beforeUnmount() {
    clearInterval(this._timer)
    window.removeEventListener('resize', this.drawCurve)
  },
  methods: {
    // 提速是把 hook 注入到"客户端所在的这台机器"上，所以**优先连本机后端**：
    // 在 https://xiaowusu.com/booster 打开时页面来自云端，但浏览器可以反过来
    // 请求你自己的 127.0.0.1:5000 —— 于是云端页面也能真的驱动本机加速。
    // 本机后端已开 CORS（含 Chrome 私有网络访问所需的 Allow-Private-Network）。
    async probeLocal() {
      if (this.localTried) return this.localBase
      this.localTried = true
      const h = location.hostname
      if (h === '127.0.0.1' || h === 'localhost') return ''   // 本来就是本机页面
      for (const b of ['http://127.0.0.1:5000', 'http://localhost:5000']) {
        try {
          const ctl = new AbortController()
          const t = setTimeout(() => ctl.abort(), 2500)
          const r = await fetch(`${b}/api/accel/booster/status`, { signal: ctl.signal })
          clearTimeout(t)
          if (r.ok || r.status === 403) {   // 403 也说明本机后端在跑（只是没令牌）
            this.localBase = b
            break
          }
        } catch (e) { /* 换下一个地址试 */ }
      }
      return this.localBase
    },
    async req(path, opts = {}) {
      const headers = { 'Content-Type': 'application/json' }
      const token = localStorage.getItem('dps-accel-token') || ''
      if (token) headers['X-Access-Token'] = token
      const base = this.localBase ? `${this.localBase}/api` : API
      const resp = await fetch(`${base}/accel${path}`, { ...opts, headers })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) throw new Error(data.error || `请求失败（${resp.status}）`)
      return data
    },
    toastMsg(m) {
      this.toast = m
      clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toast = '' }, 2200)
    },
    async refresh() {
      try {
        const d = await this.req('/booster/status')
        this.loadError = null
        if (!d.available) {
          this.available = false
          this.unavailableReason = d.reason || ''
          return
        }
        this.available = true
        this.unavailableReason = ''
        this.targets = d.targets || []
        this.injected = d.injected || 0
        this.enabled = !!d.enabled
        if (!this.factorDirty) this.factor = d.factor || 5
        this.auto = !!d.auto
        this.target = d.target || 8
        this.state = d.state || 'IDLE'
        this.speedSrc = d.speed_src || ''
        this.winSec = d.win_sec || 60
        this.serverCooldown = d.server_cooldown || 0
        this.log = d.log || []
        // 速度：优先取状态里的实时值；历史推入
        const sp = d.speed || 0
        this.speed = sp
        this.history.push(sp)
        if (this.history.length > 120) this.history.shift()
        this.drawCurve()
      } catch (e) {
        // 请求失败 ≠ 组件不可用：不能因此断言"DLL 没构建"。
        // （以前这里直接把 available 置 false，后端没起来时会误报去构建 DLL。）
        this.available = null
        this.loadError = (e && e.message) || '无法连接后端'
      }
    },
    fmtSize(b) {
      if (!b || b <= 0) return '0 B'
      if (b < 1048576) return (b / 1024).toFixed(0) + ' KB'
      if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'
      return (b / 1073741824).toFixed(2) + ' GB'
    },
    async act(path, opts, okMsg) {
      this.busy = true
      this.error = null
      try {
        await this.req(path, opts)
        if (okMsg) this.toastMsg(okMsg)
        await this.refresh()
      } catch (e) {
        this.error = e.message
      }
      this.busy = false
    },
    // 只注入按 kernel.dll 判定出来的真下载引擎进程
    injectEngine() {
      this.act('/booster/inject-engine', { method: 'POST' }, '已注入下载引擎进程')
    },
    // 一键加速开关：没注入就注入并开启；已注入就切换开/关（hook 保留，不卸载）
    toggleAccel() {
      if (!this.injected) {
        this.act('/booster/inject-engine', { method: 'POST' }, '已注入下载引擎并开启加速')
        return
      }
      const on = !this.enabled
      this.act('/booster/enabled', { method: 'PUT', body: JSON.stringify({ enabled: on }) },
               on ? '加速已开启' : '加速已关闭（hook 保留，可随时再开）')
    },
    // 兜底：注入全部百度进程（含 CEF 渲染进程，会把 UI 时钟一起缩放，一般不要用）
    injectAllProcs() {
      this.act('/booster/inject', { method: 'POST' }, '已注入全部百度进程')
    },
    injectOne(pid) { this.act('/booster/inject', { method: 'POST', body: JSON.stringify({ pid }) }, `已注入 pid ${pid}`) },
    ejectAll() { this.act('/booster/eject', { method: 'POST' }, '已卸载全部 hook') },
    setFactor(f) {
      this.factorDirty = true
      this.factor = f
      this.act('/booster/factor', { method: 'PUT', body: JSON.stringify({ factor: f }) })
    },
    toggleAuto() {
      this.factorDirty = false
      this.act('/booster/auto', {
        method: 'PUT',
        body: JSON.stringify({ enabled: !this.auto, target: this.target }),
      }, !this.auto ? '闭环自动调速已启动' : '自动调速已停止')
    },
    setTarget(v) { this.target = v; if (this.auto) this.act('/booster/auto', { method: 'PUT', body: JSON.stringify({ enabled: true, target: v }) }) },
    drawCurve() {
      const cv = this.$refs.canvas
      if (!cv) return
      const ctx = cv.getContext('2d')
      const w = cv.width = cv.clientWidth || 600
      const h = cv.height = 140
      ctx.clearRect(0, 0, w, h)
      const hist = this.history
      const css = getComputedStyle(document.documentElement)
      const line = css.getPropertyValue('--primary').trim() || '#409eff'
      const grid = css.getPropertyValue('--border').trim() || '#333'
      // 网格
      ctx.strokeStyle = grid
      ctx.lineWidth = 1
      for (let i = 1; i < 4; i++) {
        ctx.beginPath(); ctx.moveTo(0, h * i / 4); ctx.lineTo(w, h * i / 4); ctx.stroke()
      }
      if (hist.length < 2) return
      const max = Math.max(1e6, ...hist)
      ctx.strokeStyle = line
      ctx.lineWidth = 2
      ctx.beginPath()
      for (let i = 0; i < hist.length; i++) {
        const x = i / (hist.length - 1) * w
        const y = h - hist[i] / max * (h - 6) - 2
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      }
      ctx.stroke()
      // 峰值标注
      ctx.fillStyle = css.getPropertyValue('--text-secondary').trim()
      ctx.font = '11px sans-serif'
      ctx.fillText((max / 1e6).toFixed(1) + ' MB/s', 6, 14)
    },
  },
}
</script>

<template>
  <div>
    <div class="page-title">
      <h2>下载提速</h2>
      <p>自研选择性时间 Hook：解除百度网盘客户端的本地限速，闭环自动调速防塌陷（非会员可用）</p>
    </div>

    <!-- 云端站点上没探测到本机加速服务 → 引导下载本机小程序 -->
    <div class="bst-card bst-guide" v-if="needLocalAgent">
      <div class="bst-head">⚠ 未检测到本机加速服务</div>
      <p class="bst-guide-p">
        加速必须在<b>你自己的电脑</b>上执行：百度的限速是百度网盘客户端进程内的
        “按时间计的令牌桶”，而网页无法从外部往别的进程注入 DLL。
        所以需要先在本机跑一个小程序（就下面这个，13MB，无需安装 Python）。
      </p>
      <ol class="bst-steps">
        <li>下载并解压「本机加速器」到任意目录</li>
        <li>双击 <b>启动加速器.bat</b> —— 杀软/SmartScreen 拦截时选「仍要运行 / 允许」</li>
        <li>等窗口出现「监听: http://127.0.0.1:5000」，回到本页刷新，点「注入并开启加速」</li>
      </ol>
      <div class="bst-guide-btns">
        <a class="btn primary" :href="accelDownloadUrl" download>下载本机加速器（13MB）</a>
        <a class="btn" :href="accelGuideUrl" target="_blank" rel="noopener">使用说明</a>
        <a class="bst-guide-mirror" :href="accelDownloadMirror" target="_blank" rel="noopener">GitHub 备用下载</a>
      </div>
      <p class="bst-guide-note">
        装好后本页会自动连到本机服务（会显示「本机加速服务」标记），
        以后打开这个网址就能直接控制你自己的客户端。首次之后无需再下载。
      </p>
      <div class="bst-guide-detail" v-if="unavailableReason">
        服务器侧说明：{{ unavailableReason }}
      </div>
    </div>

    <!-- 本机页面但确实不可用（例如缺 DLL）→ 报技术原因 -->
    <div class="error-msg" v-if="available === false && !needLocalAgent">
      booster 组件不可用{{ unavailableReason ? '：' + unavailableReason : '' }}
    </div>
    <div class="error-msg" v-if="loadError">无法读取加速状态：{{ loadError }}</div>
    <div class="error-msg" v-if="error">{{ error }}</div>

    <!-- 实时状态 -->
    <div class="bst-card">
      <div class="bst-top">
        <div class="bst-speed">{{ speedText }}</div>
        <div class="bst-meta">
          <div>状态：<b>{{ stateText }}</b></div>
          <div>当前倍率：<b>{{ factor }}x</b> · 注入进程：<b>{{ injected }}</b></div>
          <div>
            控制目标：<b>{{ backendLabel }}</b>
            <span v-if="localBase" class="bst-tag-ok">已连接你本机</span>
          </div>
          <div v-if="speedSrc" class="g-hint">
            速度来源：{{ speedSrc === 'nic' ? '网卡（系统级，可信）' : speedSrc }}
            · 判定窗口 {{ winSec }}s
          </div>
          <div v-if="serverCooldown > 0" class="bst-warn">
            服务端限速冷却中（{{ Math.round(serverCooldown) }}s）
          </div>
        </div>
        <div class="flex-gap"></div>
        <span class="bst-badge" v-if="available !== null" :class="accelOn ? 'on' : 'off'">
          {{ accelOn ? '加速开启中' : '加速未开启' }}
        </span>
        <button class="btn" @click="toggleAccel" :disabled="busy || available !== true">
          {{ accelLabel }}
        </button>
        <button class="btn danger" @click="ejectAll" :disabled="busy">卸载 hook</button>
      </div>
      <canvas ref="canvas" class="bst-canvas"></canvas>
      <div class="bst-controls">
        <label class="bst-slider">
          倍率 <b>{{ factor }}x</b>
          <input type="range" min="1" max="16" step="0.5" v-model.number="factor"
                 @change="setFactor(factor)" />
        </label>
        <label class="bst-auto">
          <input type="checkbox" :checked="auto" @change="toggleAuto" /> 闭环自动调速
        </label>
        <label class="bst-target">
          目标 <input type="number" min="1" max="64" v-model.number="target"
                      @change="setTarget(target)" /> MB/s
        </label>
      </div>
      <div class="bst-log" v-if="log.length">
        <div v-for="(l, i) in log.slice().reverse()" :key="i">{{ l }}</div>
      </div>
    </div>

    <!-- 目标进程 -->
    <div class="bst-card">
      <div class="bst-head">
        目标进程（{{ targets.length }}）
        <span class="flex-gap"></span>
        <button class="btn xs" @click="injectEngine" :disabled="busy">一键注入下载引擎</button>
        <button class="btn xs" @click="injectAllProcs" :disabled="busy">注入全部进程</button>
      </div>
      <div class="bst-targets">
        <div class="bst-target" v-for="t in targets" :key="t.pid">
          <span class="bst-dot" :class="t.injected ? 'on' : ''"></span>
          <span>{{ t.name }}</span>
          <span class="bst-pid">pid {{ t.pid }}</span>
          <span class="badge" v-if="t.engine" :class="t.module_ok ? 'badge-alive' : 'badge-check'"
                :title="t.module_ok ? '已加载 kernel.dll，确认是下载引擎' : 'kernel.dll 未加载，这是按流量猜的'">
            {{ t.module_ok ? '下载引擎' : '疑似引擎' }}
          </span>
          <span class="flex-gap"></span>
          <span class="badge" :class="t.injected ? 'badge-alive' : 'badge-check'">
            {{ t.injected ? '已注入' : '未注入' }}
          </span>
          <button class="btn xs" v-if="!t.injected" @click="injectOne(t.pid)">注入</button>
        </div>
        <div class="empty-state" v-if="!targets.length">
          <p>未发现百度网盘客户端进程 —— 请先打开客户端</p>
        </div>
      </div>
    </div>

    <!-- 使用说明 -->
    <div class="g-card bst-help">
      <div class="g-label">使用说明</div>
      <ol class="bst-steps">
        <li>打开百度网盘客户端（任意账号均可，无需会员）；</li>
        <li>在客户端里正常开始一个下载任务 —— <b>没有正在下载的任务时加不了速</b>；</li>
        <li>点 <b>「注入并开启加速」</b>：只对加载了 kernel.dll 的那个 baidunetdiskhost.exe
            生效（同名进程有两个，另一个只跑视频插件）；</li>
        <li>注入后这个按钮会变成 <b>「关闭加速」/「开启加速」</b>，随时开关、不用卸载——
            想停掉提速点「关闭加速」就行；「卸载 hook」是把 DLL 从进程里摘掉；</li>
        <li>「闭环自动调速」可选：速度衰减时自动降倍率、稳定时自动上探，避免塌陷归零。</li>
      </ol>
      <p class="g-hint">
        原理：百度客户端的限速是本地按时间计的令牌桶，本工具 hook 客户端进程的时间 API
        使限速时钟加速（默认仅时钟组生效，超时/定时器保持真值——这是相对 OpenSpeedy
        解决"塌陷归零"的关键差异）。速率仍受服务端策略与宽带上限约束；加速行为违反
        百度用户协议，账号风险自负，建议用小号。
      </p>
    </div>

    <div class="toast" v-if="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.bst-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px; margin-bottom: 14px;
  box-shadow: var(--shadow);
}
.bst-head {
  font-size: 14px; font-weight: 600; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}
.bst-top { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.bst-speed {
  font-size: 34px; font-weight: 800; color: var(--primary);
  font-variant-numeric: tabular-nums; min-width: 150px;
}
.bst-meta { font-size: 13px; color: var(--text-secondary); line-height: 1.8; }
.bst-meta b { color: var(--text); }
.bst-warn { color: var(--warning); }
/* 加速开关状态徽标：一眼看出开没开 */
.bst-badge {
  padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  border: 1px solid var(--border);
}
.bst-badge.on { color: var(--success, #16a34a); border-color: var(--success, #16a34a); }
.bst-badge.off { color: var(--text-secondary); }

/* 「未检测到本机加速服务」引导卡 */
.bst-guide { border-left: 3px solid var(--warning); }
.bst-guide-p { font-size: 13px; line-height: 1.9; color: var(--text-secondary); margin: 6px 0 10px; }
.bst-guide-p b { color: var(--text); }
.bst-guide .bst-steps { font-size: 13px; line-height: 2; color: var(--text-secondary); padding-left: 20px; margin: 0 0 12px; }
.bst-guide-btns { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.bst-guide-btns .btn { display: inline-block; text-decoration: none; }
.bst-guide-mirror {
  font-size: 12px; color: var(--text-secondary); text-decoration: underline;
}
.bst-guide-mirror:hover { color: var(--primary); }
.bst-guide-note { font-size: 12px; line-height: 1.8; color: var(--text-secondary); margin: 10px 0 0; }
.bst-guide-detail {
  margin-top: 8px; font-size: 12px; color: var(--text-secondary);
  border-top: 1px dashed var(--border); padding-top: 8px;
}
.bst-tag-ok {
  margin-left: 6px; padding: 1px 7px; border-radius: 999px;
  background: var(--primary-soft); color: var(--primary);
  font-size: 11px; font-weight: 600;
}
/* 说明区正文要与上面的列表同字号：.g-hint 只在 GuestPage 里定义过，
   BoosterPage 之前完全没有它的样式 → 浏览器默认字号（看起来比列表大很多） */
.bst-help .g-hint,
.bst-help .bst-note {
  font-size: 13px; line-height: 1.9; color: var(--text-secondary);
  margin: 8px 0 0;
}
.bst-canvas { width: 100%; height: 140px; margin-top: 10px; border-radius: var(--radius); }
.bst-controls {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  margin-top: 10px; font-size: 13px;
}
.bst-slider input { vertical-align: middle; width: 180px; accent-color: var(--primary); }
.bst-auto input, .bst-target input { accent-color: var(--primary); }
.bst-target input {
  width: 64px; padding: 4px 6px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--bg-card); color: var(--text);
}
.bst-log {
  margin-top: 10px; padding: 8px 12px; background: var(--bg);
  border: 1px dashed var(--border); border-radius: var(--radius);
  font-size: 12px; color: var(--text-secondary); line-height: 1.7;
  max-height: 130px; overflow-y: auto;
  font-family: Consolas, monospace;
}
.bst-targets {
  border: 1px solid var(--border); border-radius: var(--radius);
  max-height: 240px; overflow-y: auto;
}
.bst-target {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 12px; font-size: 13px; border-bottom: 1px solid var(--border);
}
.bst-target:last-child { border-bottom: none; }
.bst-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-secondary); flex-shrink: 0; }
.bst-dot.on { background: var(--success); box-shadow: 0 0 5px var(--success); }
.bst-pid { color: var(--text-secondary); font-size: 12px; }
.bst-help { max-width: none; }
.bst-steps { padding-left: 18px; font-size: 13px; line-height: 2; color: var(--text-secondary); }
</style>
