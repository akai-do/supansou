import { reactive } from 'vue'

// 后台布局与子页面共享的当前用户状态（刷新页面后由 AdminLayout 重新拉取）。
export const adminState = reactive({
  user: null,
})

export function setAdminUser(user) {
  adminState.user = user
}
