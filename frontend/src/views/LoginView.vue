<template>
  <div class="login-page">
    <main class="login-shell">
      <header class="login-brand">
        <span class="brand-mark"><KgLogo :size="30" /></span>
        <span class="brand-copy">
          <strong>麒盾</strong>
          <span>KylinGuard</span>
        </span>
      </header>

      <section class="login-panel" aria-labelledby="login-title">
        <div class="login-heading">
          <h1 id="login-title">登录工作台</h1>
          <p>使用管理员账户继续</p>
        </div>

        <form class="login-form" @submit.prevent="submit">
          <label class="field-label" for="login-user">用户名</label>
          <el-input id="login-user" v-model="user" class="field"
                    autocomplete="username" placeholder="输入用户名" />

          <label class="field-label" for="login-password">密码</label>
          <el-input id="login-password" v-model="password" class="field"
                    type="password" autocomplete="current-password"
                    placeholder="输入密码" show-password />

          <div v-if="error" class="login-error" role="alert">
            <KgIcon name="warning" :size="15" />
            <span>{{ error }}</span>
          </div>

          <el-button native-type="submit" type="primary" class="login-btn"
                     :loading="busy" :disabled="!user || !password">
            登录
          </el-button>
        </form>

        <div class="audit-note">
          <KgIcon name="lock" :size="15" />
          <span>后续操作将关联到当前账户并写入审计记录</span>
        </div>
      </section>

      <footer class="login-foot">麒麟操作系统 · 管理入口</footer>
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import KgIcon from '../components/KgIcon.vue'
import KgLogo from '../components/KgLogo.vue'
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
.login-page {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  overflow: auto;
  padding: var(--kg-space-8) var(--kg-space-6);
  background: var(--kg-bg-canvas);
}

.login-shell {
  width: min(100%, 400px);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
  margin-bottom: var(--kg-space-5);
  padding: 0 var(--kg-space-1);
}

.brand-mark {
  width: 44px;
  height: 44px;
  display: grid;
  flex: none;
  place-items: center;
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  color: var(--kg-accent);
  box-shadow: inset 0 1px rgb(255 255 255 / 3%);
}

.brand-copy {
  min-width: 0;
  display: grid;
  line-height: 1.15;
}

.brand-copy strong {
  color: var(--kg-text-primary);
  font-size: 18px;
  font-weight: 600;
}

.brand-copy span {
  margin-top: 4px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  letter-spacing: .04em;
}

.login-panel {
  padding: var(--kg-space-8);
  border: 1px solid var(--kg-border-default);
  border-radius: var(--kg-radius-lg);
  background: var(--kg-bg-surface-1);
  box-shadow: 0 18px 48px rgb(0 0 0 / 24%),
    inset 0 1px rgb(255 255 255 / 3%);
}

.login-heading { margin-bottom: var(--kg-space-6); }
.login-heading h1 {
  margin: 0;
  color: var(--kg-text-primary);
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
}

.login-heading p {
  margin: var(--kg-space-1) 0 0;
  color: var(--kg-text-tertiary);
  font-size: 13px;
  line-height: 20px;
}

.login-form { display: grid; }

.field-label {
  margin-bottom: 6px;
  color: var(--kg-text-secondary);
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
}

.field { margin-bottom: var(--kg-space-4); }

.login-form :deep(.el-input__wrapper) {
  min-height: 38px;
  background: var(--kg-bg-canvas);
}

.login-error {
  margin: calc(-1 * var(--kg-space-1)) 0 var(--kg-space-3);
  padding: var(--kg-space-2) var(--kg-space-3);
  display: flex;
  align-items: flex-start;
  gap: var(--kg-space-2);
  border: 1px solid var(--kg-danger-border);
  border-radius: var(--kg-radius-sm);
  background: var(--kg-danger-soft);
  color: var(--kg-danger);
  font-size: 12px;
  line-height: 18px;
}

.login-error :deep(.kg-icon) { margin-top: 1px; }

.login-btn {
  width: 100%;
  min-height: 38px;
  margin-top: var(--kg-space-1);
}

.audit-note {
  margin-top: var(--kg-space-5);
  padding-top: var(--kg-space-4);
  display: flex;
  align-items: flex-start;
  gap: var(--kg-space-2);
  border-top: 1px solid var(--kg-border-subtle);
  color: var(--kg-text-tertiary);
  font-size: 12px;
  line-height: 18px;
}

.audit-note :deep(.kg-icon) {
  margin-top: 1px;
  color: var(--kg-text-tertiary);
}

.login-foot {
  margin-top: var(--kg-space-4);
  color: var(--kg-text-tertiary);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

@media (max-height: 650px) {
  .login-page { align-items: start; padding-top: var(--kg-space-6); }
  .login-brand { margin-bottom: var(--kg-space-4); }
  .login-panel { padding: var(--kg-space-6); }
  .login-heading { margin-bottom: var(--kg-space-5); }
}
</style>
