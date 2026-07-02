<template>
  <div class="login-page">
    <div class="login-box">
      <div class="login-logo">🛡</div>
      <div class="login-title">麒盾 KylinGuard</div>
      <div class="login-sub">面向麒麟操作系统的安全智能运维 Agent</div>
      <el-input v-model="user" placeholder="用户名" class="field"
                @keyup.enter="submit" />
      <el-input v-model="password" type="password" placeholder="密码"
                class="field" show-password @keyup.enter="submit" />
      <el-button type="primary" class="login-btn" :loading="busy"
                 @click="submit">登 录</el-button>
      <div v-if="error" class="login-error">{{ error }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { login } from '../composables/useAuth.js'

const user = ref('admin')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  if (!user.value || !password.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await login(user.value, password.value)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.login-page { height: 100%; display: flex; align-items: center;
  justify-content: center; background: #0d1117; }
.login-box { width: 320px; padding: 36px 32px; background: #161b22;
  border: 1px solid #21262d; border-radius: 16px; text-align: center; }
.login-logo { font-size: 40px; }
.login-title { font-size: 20px; font-weight: 700; color: #e6edf3;
  margin-top: 8px; }
.login-sub { font-size: 12px; color: #8b949e; margin: 4px 0 22px; }
.field { margin-bottom: 12px; }
.login-btn { width: 100%; margin-top: 4px; }
.login-error { color: #f85149; font-size: 12px; margin-top: 10px; }
</style>
