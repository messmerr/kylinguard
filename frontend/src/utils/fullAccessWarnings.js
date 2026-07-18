import { ElMessageBox } from 'element-plus'

const DIALOG_CLASS = 'kg-critical-confirm'

function warningOptions(confirmButtonText) {
  return {
    type: 'error',
    confirmButtonText,
    cancelButtonText: '取消',
    distinguishCancelAndClose: true,
    closeOnClickModal: false,
    closeOnPressEscape: false,
    customClass: DIALOG_CLASS,
    confirmButtonClass: 'el-button--danger',
  }
}

async function requireTypedConfirmation(phrase, title, message, confirmButtonText) {
  await ElMessageBox.prompt(message, title, {
    ...warningOptions(confirmButtonText),
    inputPlaceholder: `请输入“${phrase}”`,
    inputValidator: value => String(value || '').trim() === phrase
      || `请输入“${phrase}”完成二次确认`,
  })
}

export async function confirmFullAccessExposure({ grantsRoot = false } = {}) {
  await ElMessageBox.confirm(
    `显示后，“完全访问”会出现在所有任务的全局权限菜单中。它可以跳过 Reviewer 和逐项确认，使用当前 OS 身份执行完整 Shell、文件、网络与进程操作。${grantsRoot ? ' 当前执行身份拥有 root 权限。' : ''}`,
    '显示高风险权限入口 · 第 1/2 步',
    warningOptions('继续二次确认'),
  )
  await requireTypedConfirmation(
    '显示完全访问',
    '显示高风险权限入口 · 第 2/2 步',
    '这一步只显示入口，不会立即启用完全访问。显示后仍需再次经过独立的两阶段确认才能真正开启。',
    '确认显示入口',
  )
}

export async function confirmFullAccessEnable({
  executorIdentity = '当前 OS 身份',
  grantsRoot = false,
} = {}) {
  await ElMessageBox.confirm(
    `完全访问是全局设置，将立即作用于当前及以后创建的所有任务，并持续生效，直到你手动收回、隐藏入口、服务端关闭该能力或后端重启。启用期间不再逐项询问，也不把 Reviewer 作为执行前置条件。执行身份：${executorIdentity}。${grantsRoot ? '该身份拥有 root 权限，错误命令可能破坏整台系统。' : ''}`,
    '启用完全访问 · 第 1/2 步',
    warningOptions('我已了解，继续'),
  )
  await requireTypedConfirmation(
    '启用完全访问',
    '启用完全访问 · 第 2/2 步',
    '启用后，完整 Shell、文件、网络和进程操作可以不经逐项确认直接执行。请仅在你愿意承担当前 OS 身份全部影响范围时继续。',
    '确认启用完全访问',
  )
}
