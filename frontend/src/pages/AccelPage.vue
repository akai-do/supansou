<script>
// 网盘加速页：BDUSS 设置 → 分享链接解析 → 文件勾选 → aria2 任务管理
// 请求约定：走 /api/accel/*，错误为 { error }；远程部署时带 X-Access-Token 头
import { API } from '../constants.js'

export default {
  data() {
    return {
      // 账号设置
      status: {
        bduss_set: false, bduss_hint: '', download_dir: '',
        token_required: false,
        aria2: { online: false, version: '', mode: '', dir: '' },
      },
      statusError: null,
      settingsError: null,
      bdussInput: '',
      tokenInput: localStorage.getItem('dps-accel-token') || '',
      isStation: null,          // null=未知 true=站长 false=访客（403）
      saving: false,
      // 解析口令管理（仅站长）
      passInfo: { enabled: false, valid_today: 0, entries: [], file: '', today: '' },
      passNew: '',
      passCustom: '',
      passError: null,
      passBusy: false,
      _passLoaded: false,
      // 扫码登录
      qr: { visible: false, imgurl: '', status: 'none', error: '', timer: null },
      // 解析
      linkText: '',
      pwdInput: '',
      parsing: false,
      parseError: null,
      share: null,        // { surl, pwd, files }
      crumbs: [],         // [{ name, dir }]
      dirLoading: false,
      // 选择（跨目录保留：fid → {fid,name,size,path}）
      selected: {},
      // 下载
      saveDir: localStorage.getItem('dps-accel-dir') || '',
      submitting: false,
      // 任务
      tasks: [],
      aria2Online: true,
      toast: '',
      _toastTimer: null,
      _taskTimer: null,
    }
  },
  computed: {
    currentFiles() { return this.share ? this.share.files : [] },
    selectedList() { return Object.values(this.selected) },
    selectedCount() { return this.selectedList.length },
    selectedSize() { return this.selectedList.reduce((s, f) => s + (f.size || 0), 0) },
    allChecked() {
      const files = this.currentFiles.filter(f => !f.is_dir)
      return files.length > 0 && files.every(f => this.selected[f.fid])
    },
    onlineText() {
      const a = this.status.aria2
      if (!a) return ''
      if (!a.online) return 'aria2 离线'
      return `aria2 ${a.version}（${a.mode === 'external' ? '外部 RPC' : '内置'}）`
    },
    qrStateText() {
      return {
        waiting: '请用手机「百度」或「百度网盘」App 扫一扫',
        scanned: '已扫码，请在手机上确认',
        confirmed: '登录成功',
        expired: '二维码已过期',
        error: '出错了',
        none: '二维码加载中…',
      }[this.qr.status] || ''
    },
  },
  async mounted() {
    await this.loadStatus()
    this.refreshTasks()
    this._taskTimer = setInterval(() => {
      if (!document.hidden) this.refreshTasks()
    }, 1500)
  },
  beforeUnmount() {
    clearInterval(this._taskTimer)
    clearInterval(this.qr.timer)
  },
  methods: {
    // ===== 通用请求 =====
    async req(path, opts = {}) {
      const headers = { 'Content-Type': 'application/json' }
      const token = localStorage.getItem('dps-accel-token') || ''
      if (token) headers['X-Access-Token'] = token
      const resp = await fetch(`${API}/accel${path}`, { ...opts, headers })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error(data.error || `请求失败（${resp.status}）`)
      }
      return data
    },
    toastMsg(m) {
      this.toast = m
      clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toast = '' }, 2200)
    },

    // ===== 账号 =====
    async loadStatus() {
      this.statusError = null
      try {
        this.status = await this.req('/status')
        this.isStation = true
        if (!this.saveDir && this.status.download_dir) {
          this.saveDir = this.status.download_dir
        }
        if (!this._passLoaded) {
          this._passLoaded = true
          this.loadPass()
        }
      } catch (e) {
        // 403 = 访客身份（远程访问但没填/填错站长令牌）—— 不是"坏了"，
        // 而是"还没以站长身份登录"，页面上给明确提示并露出令牌输入框。
        this.isStation = false
        this.statusError = /403|无权/.test(e.message)
          ? '当前是访客身份：只需在下面的「站长身份」里填入令牌即可管理设置。'
          : e.message
      }
    },
    async loadPass() {
      this.passBusy = true
      this.passError = null
      try {
        this.passInfo = await this.req('/pass')
      } catch (e) {
        this.passError = e.message
      }
      this.passBusy = false
    },
    async newPass(permanent, custom) {
      this.passBusy = true
      this.passError = null
      this.passNew = ''
      try {
        const d = await this.req('/pass', {
          method: 'POST',
          body: JSON.stringify({
            permanent: !!permanent,
            pass: (custom || '').trim() || undefined,
          }),
        })
        this.passNew = d.pass
        this.passCustom = ''
        await this.loadPass()
        this.toastMsg(permanent ? '已添加长期口令' : '已添加今日口令')
      } catch (e) {
        this.passError = e.message
      }
      this.passBusy = false
    },
    async delPass(ent) {
      if (!confirm(`吊销第 ${ent.index} 行口令？它立刻失效，其他口令不受影响。`)) return
      this.passBusy = true
      this.passError = null
      try {
        await this.req(`/pass?index=${ent.index}`, { method: 'DELETE' })
        await this.loadPass()
        this.toastMsg(`已吊销第 ${ent.index} 行`)
      } catch (e) {
        this.passError = e.message
      }
      this.passBusy = false
    },
    async saveBduss() {
      this.saving = true
      this.settingsError = null
      try {
        const d = await this.req('/bduss', {
          method: 'PUT',
          body: JSON.stringify({ bduss: this.bdussInput.trim() }),
        })
        localStorage.setItem('dps-accel-token', this.tokenInput.trim())
        this.bdussInput = ''
        await this.loadStatus()
        this.toastMsg(`BDUSS 已保存（${d.bduss_hint}）`)
      } catch (e) {
        this.settingsError = e.message
      }
      this.saving = false
    },
    async clearBduss() {
      if (!confirm('确定清除已保存的 BDUSS？已完成的下载任务不受影响。')) return
      try {
        await this.req('/bduss', { method: 'DELETE' })
        await this.loadStatus()
        this.toastMsg('BDUSS 已清除')
      } catch (e) {
        this.settingsError = e.message
      }
    },
    persistToken() {
      localStorage.setItem('dps-accel-token', this.tokenInput.trim())
    },

    // ===== 扫码登录 =====
    qrToggle() { this.qr.visible ? this.hideQr() : this.startQr() },
    hideQr() {
      this.qr.visible = false
      clearInterval(this.qr.timer)
      this.qr.status = 'none'
    },
    async startQr() {
      this.qr.visible = true
      this.qr.error = ''
      this.qr.status = 'none'
      clearInterval(this.qr.timer)
      try {
        const d = await this.req('/qr/start', { method: 'POST' })
        this.qr.imgurl = d.imgurl
        this.qr.status = 'waiting'
        this.qr.timer = setInterval(() => this.pollQr(), 2500)
      } catch (e) {
        this.qr.error = e.message
      }
    },
    async pollQr() {
      if (!this.qr.visible) return
      try {
        const d = await this.req('/qr/status')
        this.qr.status = d.status
        if (d.status === 'confirmed') {
          clearInterval(this.qr.timer)
          this.qr.visible = false
          this.toastMsg(`扫码登录成功（${d.bduss_hint}）`)
          await this.loadStatus()
        } else if (d.status === 'error') {
          clearInterval(this.qr.timer)
          this.qr.error = d.error || '扫码登录失败'
        }
      } catch (e) { /* 单次轮询失败忽略，下轮再试 */ }
    },

    // ===== 解析 / 目录 =====
    shareUrl() {
      return this.share ? `https://pan.baidu.com/s/${this.share.surl}` : ''
    },
    async parse() {
      if (!this.linkText.trim()) return
      this.parsing = true
      this.parseError = null
      try {
        const d = await this.req('/parse', {
          method: 'POST',
          body: JSON.stringify({ url: this.linkText, pwd: this.pwdInput || undefined }),
        })
        this.share = { surl: d.surl, pwd: d.pwd, files: d.files }
        this.pwdInput = d.pwd || this.pwdInput
        this.crumbs = [{ name: '根目录', dir: '/' }]
      } catch (e) {
        this.parseError = e.message
      }
      this.parsing = false
    },
    async fetchDir(dir) {
      const d = await this.req('/list-dir', {
        method: 'POST',
        body: JSON.stringify({ url: this.shareUrl(), pwd: this.share.pwd, dir }),
      })
      this.share.files = d.files
    },
    async enterDir(f) {
      this.dirLoading = true
      this.parseError = null
      try {
        await this.fetchDir(f.path)
        this.crumbs.push({ name: f.name, dir: f.path })
      } catch (e) {
        this.parseError = e.message
      }
      this.dirLoading = false
    },
    async crumbTo(i) {
      if (!this.share || i === this.crumbs.length - 1) return
      this.dirLoading = true
      this.parseError = null
      try {
        await this.fetchDir(this.crumbs[i].dir)
        this.crumbs = this.crumbs.slice(0, i + 1)
      } catch (e) {
        this.parseError = e.message
      }
      this.dirLoading = false
    },

    // ===== 选择 =====
    toggle(f) {
      if (this.selected[f.fid]) delete this.selected[f.fid]
      else this.selected[f.fid] = { fid: f.fid, name: f.name, size: f.size, path: f.path }
    },
    toggleAll() {
      const files = this.currentFiles.filter(f => !f.is_dir)
      if (this.allChecked) {
        for (const f of files) delete this.selected[f.fid]
      } else {
        for (const f of files) {
          this.selected[f.fid] = { fid: f.fid, name: f.name, size: f.size, path: f.path }
        }
      }
    },
    persistDir() {
      localStorage.setItem('dps-accel-dir', this.saveDir.trim())
    },

    // ===== 下载 =====
    async startDownload() {
      this.submitting = true
      this.parseError = null
      try {
        const d = await this.req('/download', {
          method: 'POST',
          body: JSON.stringify({
            url: this.shareUrl(),
            pwd: this.share.pwd,
            items: this.selectedList,
            save_dir: this.saveDir.trim(),
          }),
        })
        this.toastMsg(`已开始 ${d.tasks.length} 个下载任务${d.skipped ? `（跳过 ${d.skipped} 项）` : ''}`)
        this.selected = {}
        this.refreshTasks()
      } catch (e) {
        this.parseError = e.message
      }
      this.submitting = false
    },

    // ===== 任务 =====
    async refreshTasks() {
      try {
        const d = await this.req('/tasks')
        this.tasks = d.tasks || []
        this.aria2Online = !!d.aria2_online
      } catch (e) {
        this.aria2Online = false
      }
    },
    async pauseTask(t) {
      try { await this.req(`/tasks/${t.id}/pause`, { method: 'POST' }) } catch (e) { this.toastMsg(e.message) }
      this.refreshTasks()
    },
    async resumeTask(t) {
      try { await this.req(`/tasks/${t.id}/resume`, { method: 'POST' }) } catch (e) { this.toastMsg(e.message) }
      this.refreshTasks()
    },
    async delTask(t) {
      if (!confirm(`删除任务「${t.name}」？已下载的文件会保留。`)) return
      try { await this.req(`/tasks/${t.id}`, { method: 'DELETE' }) } catch (e) { this.toastMsg(e.message) }
      this.refreshTasks()
    },

    // ===== 展示 =====
    fmtSize(b) {
      if (!b || b <= 0) return '0 B'
      if (b < 1024) return b + ' B'
      if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'
      if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'
      return (b / 1073741824).toFixed(2) + ' GB'
    },
    fmtSpeed(b) { return this.fmtSize(b) + '/s' },
    pct(t) {
      if (t.status === 'done') return 100
      if (!t.size) return 0
      return Math.min(100, Math.round((t.completed || 0) / t.size * 100))
    },
    statusText(s) {
      return { downloading: '下载中', paused: '已暂停', done: '已完成', error: '失败' }[s] || s
    },
    statusCls(s) {
      return { done: 'badge-alive', error: 'badge-dead' }[s] || 'badge-warn'
    },
  },
}
</script>

<template>
  <div>
    <div class="page-title">
      <h2>⚡ 网盘加速</h2>
      <p>粘贴百度网盘分享链接 → 自动解析直链 → aria2 多线程下载（用你自己的账号，无需转存）</p>
    </div>

    <div class="error-msg" v-if="statusError">{{ statusError }}</div>

    <!-- 站长身份（常显）：
         以前这个输入框藏在「BDUSS 未设置」的卡片里，一旦设好 BDUSS，
         远程用户就再也找不到填令牌的地方（只能手改 localStorage）。 -->
    <div class="acc-card">
      <div class="acc-head">
        站长身份
        <span class="badge" :class="isStation === true ? 'badge-alive' : 'badge-check'">
          {{ isStation === true ? '已登录（站长）' : isStation === false ? '未登录（访客）' : '检测中…' }}
        </span>
      </div>
      <p class="acc-dim">
        本站用「站长令牌」区分站长与访客：<b>留空时只有本机访问算站长</b>；
        公网部署必须在这里填入与服务端 <code>ACCEL_TOKEN</code> 一致的令牌。
        <b>令牌绝不要发给别人</b>——给朋友的是「解析口令」（页面底部可管理）。
      </p>
      <input class="acc-input" v-model="tokenInput" @change="persistToken"
             placeholder="站长令牌（与服务端 ACCEL_TOKEN 一致；本机可留空，失焦即保存）" />
      <div class="acc-dim" v-if="isStation === false">
        访客身份下看不到账号/加速/任务等设置，只能使用「直链解析」和「下载提速」页。
      </div>
    </div>

    <!-- ① 账号凭证 -->
    <div class="acc-card" v-if="!status.bduss_set">
      <div class="acc-head">① 设置你的百度账号凭证（BDUSS）</div>
      <p class="acc-dim">只保存在本机数据库，用于以你的账号身份解析直链；<b>不需要也不要求输入账号密码</b>。</p>
      <div class="acc-guide">
        获取方法：浏览器登录
        <a href="https://pan.baidu.com" target="_blank" rel="noopener">pan.baidu.com</a>
        → 按 F12 → 应用/存储 → Cookies → 复制 <code>BDUSS</code> 的值。
      </div>
      <input class="acc-input" type="password" v-model="bdussInput"
             placeholder="粘贴 BDUSS 值，或整段 Cookie 请求头（含 BDUSS= 的整行，自动提取）" />
      <button class="btn primary" @click="saveBduss" :disabled="saving || !bdussInput.trim()">
        {{ saving ? '校验中…' : '保存并校验' }}
      </button>
      <div class="error-msg" v-if="settingsError">{{ settingsError }}</div>
      <div class="acc-or"><span>或</span></div>
      <button class="btn" @click="qrToggle">{{ qr.visible ? '收起扫码' : '📱 扫码登录（推荐）' }}</button>
      <div class="acc-qrpanel" v-if="qr.visible">
        <div class="acc-qrbox">
          <img v-if="qr.imgurl && qr.status !== 'expired'" :src="qr.imgurl" alt="百度登录二维码" />
          <div class="acc-qrexpired" v-if="qr.status === 'expired'">
            二维码已过期<br />
            <button class="btn xs" @click="startQr">刷新二维码</button>
          </div>
        </div>
        <div class="acc-qrstate">{{ qrStateText }}</div>
        <div class="error-msg" v-if="qr.error">{{ qr.error }}</div>
      </div>
    </div>
    <div class="acc-card acc-ok-card" v-else>
      <span class="acc-ok">✅ BDUSS 已设置（{{ status.bduss_hint }}）</span>
      <span class="acc-dim">{{ onlineText }}</span>
      <span class="flex-gap"></span>
      <button class="btn xs danger" @click="clearBduss">清除 BDUSS</button>
    </div>

    <!-- ② 解析 -->
    <div class="acc-card">
      <div class="acc-head">② 粘贴分享链接</div>
      <textarea class="acc-input" v-model="linkText" rows="2"
                placeholder="https://pan.baidu.com/s/1xxxx?pwd=abcd（支持链接+提取码混排粘贴）"
                @keydown.enter.exact.prevent="parse"></textarea>
      <div class="acc-row">
        <button class="btn primary" @click="parse"
                :disabled="parsing || !linkText.trim() || !status.bduss_set">
          {{ parsing ? '解析中…' : '解析' }}
        </button>
        <input class="acc-input acc-pwd" v-model="pwdInput" maxlength="4"
               placeholder="提取码（自动带出，可改）" />
        <span class="acc-dim" v-if="share">已解析：surl={{ share.surl }}</span>
      </div>
      <div class="error-msg" v-if="parseError">{{ parseError }}</div>
    </div>

    <!-- ③ 文件选择 -->
    <div class="acc-card" v-if="share">
      <div class="acc-head">③ 选择要下载的文件</div>
      <div class="acc-crumbs">
        <template v-for="(c, i) in crumbs" :key="c.dir">
          <span v-if="i" class="acc-sep">/</span>
          <a href="javascript:void(0)" @click="crumbTo(i)">{{ c.name }}</a>
        </template>
      </div>
      <div class="acc-files">
        <label class="acc-file" v-for="f in currentFiles" :key="f.fid">
          <input v-if="!f.is_dir" type="checkbox" :checked="!!selected[f.fid]" @change="toggle(f)" />
          <span v-else class="acc-cb-ph"></span>
          <span class="acc-ico">{{ f.is_dir ? '📁' : '📄' }}</span>
          <span class="acc-name" :class="{ dir: f.is_dir }" @click="f.is_dir && enterDir(f)">{{ f.name }}</span>
          <span class="acc-size">{{ f.is_dir ? '目录' : fmtSize(f.size) }}</span>
        </label>
        <div class="loading" v-if="dirLoading"><span class="spinner"></span>加载中…</div>
        <div class="empty-state" v-if="!dirLoading && !currentFiles.length"><p>（空目录）</p></div>
      </div>
      <div class="acc-dlrow">
        <label class="toggle-label">
          <input type="checkbox" :checked="allChecked" @change="toggleAll" /> 本页全选
        </label>
        <input class="acc-input acc-subdir" v-model="saveDir" @change="persistDir"
               placeholder="保存目录：输入本地完整路径（如 D:\Movies）" />
        <span class="acc-dim">已选 {{ selectedCount }} 项 · {{ fmtSize(selectedSize) }}</span>
        <button class="btn primary" @click="startDownload" :disabled="submitting || !selectedCount">
          {{ submitting ? '提交中…' : '开始下载' }}
        </button>
      </div>
      <p class="acc-dim acc-savedir">默认保存目录：{{ status.download_dir }}（上方输入框填完整路径可自由指定；只填子目录名则保存到默认目录下）</p>
    </div>

    <!-- ④ 任务 -->
    <div class="acc-card">
      <div class="acc-head">
        ④ 下载任务
        <span class="badge" :class="aria2Online ? 'badge-alive' : 'badge-dead'">
          {{ aria2Online ? 'aria2 在线' : 'aria2 离线' }}
        </span>
      </div>
      <div class="acc-task" v-for="t in tasks" :key="t.id">
        <div class="acc-task-top">
          <span class="acc-name" :title="t.name">{{ t.name }}</span>
          <span class="badge" :class="statusCls(t.status)">{{ statusText(t.status) }}</span>
          <span class="acc-dim acc-speed" v-if="t.speed && t.status === 'downloading'">{{ fmtSpeed(t.speed) }}</span>
          <span class="acc-dim">{{ fmtSize(t.completed) }} / {{ fmtSize(t.size) }}（{{ pct(t) }}%）</span>
          <span class="flex-gap"></span>
          <button class="btn xs" v-if="t.status === 'downloading'" @click="pauseTask(t)">暂停</button>
          <button class="btn xs" v-if="t.status === 'paused'" @click="resumeTask(t)">继续</button>
          <button class="btn xs danger" @click="delTask(t)">删除</button>
        </div>
        <div class="acc-prog"><i :style="{ width: pct(t) + '%' }" :class="t.status"></i></div>
        <div class="acc-err" v-if="t.error">{{ t.error }}</div>
      </div>
      <div class="empty-state" v-if="!tasks.length">
        <div class="icon">🚀</div><p>暂无任务，解析并勾选文件后即可开始下载</p>
      </div>
    </div>

    <!-- ⑤ 解析口令管理（仅站长可见可操作） -->
    <div class="acc-card" v-if="isStation === true">
      <div class="acc-head">
        🔑 解析口令管理
        <span class="badge" :class="passInfo.enabled ? 'badge-alive' : 'badge-check'">
          {{ passInfo.enabled ? '已启用' : '未启用' }}
        </span>
        <span class="flex-gap"></span>
        <button class="btn xs" @click="loadPass" :disabled="passBusy">刷新</button>
      </div>
      <p class="acc-dim">
        直链解析是用<b>你的网盘账号</b>出面请求百度的，所以必须用口令限制谁能用。
        口令按行存在服务端文件里，<b>改完立刻生效、不用重启</b>；带日期的行过期自动失效。
        <br>
        ⚠ <b>配了口令但"今天"没有有效行时，所有访客都会被拒</b>（故意如此：
        宁可把朋友挡在外面，也不能因为忘更新就把账号额度敞开）。
      </p>

      <div class="acc-row">
        <button class="btn primary" @click="newPass(false)" :disabled="passBusy">生成今日口令</button>
        <button class="btn" @click="newPass(true)" :disabled="passBusy">生成长期口令</button>
        <input class="acc-input acc-pwd" v-model="passCustom" maxlength="24"
               placeholder="或自定义（≥4 位，不能含冒号）" />
        <button class="btn" @click="newPass(false, passCustom)"
                :disabled="passBusy || passCustom.trim().length < 4">用自定义的</button>
      </div>

      <div class="acc-ok" v-if="passNew">
        新口令：<code>{{ passNew }}</code> —— <b>只显示这一次</b>，请立刻复制发给朋友
      </div>
      <div class="error-msg" v-if="passError">{{ passError }}</div>

      <table class="acc-passtable" v-if="passInfo.entries && passInfo.entries.length">
        <thead>
          <tr><th>行</th><th>生效日期</th><th>今日已用</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="e in passInfo.entries" :key="e.index">
            <td>{{ e.index }}</td>
            <td>{{ e.date || '永久有效' }}</td>
            <td>{{ e.used_today }}</td>
            <td>
              <button class="btn xs danger" @click="delPass(e)">吊销</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="acc-dim" v-else>
        当前没有口令条目 —— 等于"未启用口令"，只按每 IP 每日配额限制访客。
      </div>
      <div class="acc-dim">
        服务端文件 <code>{{ passInfo.file }}</code> · 今天 {{ passInfo.today }} ·
        今日有效口令 {{ passInfo.valid_today }} 条
      </div>
    </div>

    <div class="toast" v-if="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.acc-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px; margin-bottom: 14px;
  box-shadow: var(--shadow);
}
.acc-head {
  font-size: 14px; font-weight: 600; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}
.acc-dim { font-size: 12px; color: var(--text-secondary); }
.acc-guide {
  font-size: 12px; color: var(--text-secondary); line-height: 1.8;
  background: var(--bg); border: 1px dashed var(--border);
  border-radius: var(--radius); padding: 8px 12px; margin-bottom: 10px;
}
.acc-guide a { color: var(--primary); }
.acc-guide code { background: var(--bg-card-hover); padding: 1px 5px; border-radius: 4px; }
.acc-input {
  width: 100%; padding: 9px 12px; margin-bottom: 8px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-card); color: var(--text); font-size: 13px; outline: none;
}
.acc-input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-soft); }
textarea.acc-input { resize: vertical; font-family: inherit; }
.acc-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.acc-pwd { width: 170px; margin-bottom: 0; }
.acc-ok-card { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.acc-ok { font-size: 13px; color: var(--success); font-weight: 600; }
.acc-or { display: flex; align-items: center; gap: 10px; color: var(--text-secondary); font-size: 12px; margin: 6px 0 8px; }
.acc-or::before, .acc-or::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.acc-qrpanel { display: flex; flex-direction: column; align-items: center; gap: 8px; margin-top: 10px; }
.acc-qrbox {
  width: 180px; height: 180px;
  background: #fff; border: 1px solid var(--border); border-radius: var(--radius);
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
.acc-qrbox img { width: 164px; height: 164px; }
.acc-qrexpired { text-align: center; font-size: 12px; color: var(--text-secondary); line-height: 2.2; }
.acc-qrstate { font-size: 12px; color: var(--text-secondary); }

.acc-crumbs { font-size: 13px; margin-bottom: 8px; }
.acc-crumbs a { color: var(--primary); text-decoration: none; }
.acc-crumbs a:hover { text-decoration: underline; }
.acc-sep { color: var(--text-secondary); margin: 0 4px; }

.acc-files {
  border: 1px solid var(--border); border-radius: var(--radius);
  max-height: 360px; overflow-y: auto; margin-bottom: 10px;
}
.acc-file {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; font-size: 13px; cursor: pointer;
  border-bottom: 1px solid var(--border);
}
.acc-file:last-child { border-bottom: none; }
.acc-file:hover { background: var(--bg-card-hover); }
.acc-file input[type="checkbox"] {
  width: 16px; height: 16px; accent-color: var(--primary);
  flex-shrink: 0; cursor: pointer; border: 1px solid var(--border);
}
.acc-cb-ph { width: 15px; flex-shrink: 0; }
.acc-ico { flex-shrink: 0; }
.acc-name { flex: 1; min-width: 0; word-break: break-all; }
.acc-name.dir { color: var(--primary); font-weight: 500; }
.acc-size { color: var(--text-secondary); font-size: 12px; flex-shrink: 0; }

.acc-dlrow {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 6px;
}
.acc-subdir { width: 320px; margin-bottom: 0; padding: 7px 10px; }
.acc-savedir { word-break: break-all; }

.acc-task { padding: 10px 0; border-bottom: 1px solid var(--border); }
.acc-task:last-child { border-bottom: none; }
.acc-task-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.acc-prog {
  height: 6px; background: var(--bg); border-radius: 999px;
  margin-top: 7px; overflow: hidden;
}
.acc-prog i {
  display: block; height: 100%; background: var(--primary);
  border-radius: 999px; transition: width .3s;
}
.acc-prog i.done { background: var(--success); }
.acc-prog i.error { background: var(--danger); }
.acc-err { font-size: 12px; color: var(--danger); margin-top: 5px; word-break: break-all; }
.acc-speed { color: var(--primary); font-weight: 600; }

/* 解析口令管理表 */
.acc-passtable {
  width: 100%; border-collapse: collapse; margin-top: 10px;
  font-size: 12px;
}
.acc-passtable th, .acc-passtable td {
  padding: 6px 8px; text-align: left;
  border-bottom: 1px solid var(--border);
}
.acc-passtable th { color: var(--text-secondary); font-weight: 600; }
.acc-passtable td code, .acc-ok code {
  font-family: Consolas, monospace;
  background: var(--bg); padding: 1px 5px; border-radius: 4px;
}
.acc-ok {
  margin-top: 10px; font-size: 13px; color: var(--success, #16a34a);
  word-break: break-all;
}

@media (max-width: 768px) {
  .acc-subdir { width: 100%; }
}
</style>
