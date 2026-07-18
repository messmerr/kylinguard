import { h } from 'vue'
import { ElMessageBox } from 'element-plus'
import KgIcon from '../components/KgIcon.vue'

const DIALOG_CLASS = 'kg-full-access-confirm'

function fact(label, value) {
  return h('div', { class: 'kg-full-access-confirm__fact' }, [
    h('span', label),
    h('strong', value),
  ])
}

function impact(title, description) {
  return h('li', [
    h(KgIcon, { name: 'warning', size: 14 }),
    h('span', [h('strong', title), h('small', description)]),
  ])
}

function warningContent({ executorIdentity, grantsRoot }) {
  return h('div', { class: 'kg-full-access-confirm__content' }, [
    h(
      'p',
      { class: 'kg-full-access-confirm__intro' },
      '开启后，当前任务将以现有执行身份获得完整能力，其他任务不受影响。',
    ),
    h('div', { class: 'kg-full-access-confirm__facts' }, [
      fact('生效范围', '仅当前任务'),
      fact('执行身份', executorIdentity),
    ]),
    h('ul', { class: 'kg-full-access-confirm__impacts' }, [
      impact('不再逐项确认', 'Shell、文件、网络和进程操作可直接执行。'),
      impact('跳过 Reviewer 前置检查', '高风险操作不会再等待独立审核结果。'),
      impact('任务内持续生效', '直到手动收回、服务端关闭该能力或后端重启。'),
    ]),
    grantsRoot
      ? h('div', { class: 'kg-full-access-confirm__root-alert' }, [
          h(KgIcon, { name: 'warning', size: 17 }),
          h('span', [
            h('strong', '当前执行身份拥有 root 权限'),
            h('small', '错误命令可能修改系统关键文件、服务和账户。'),
          ]),
        ])
      : null,
    h(
      'p',
      { class: 'kg-full-access-confirm__note' },
      '已经开始的系统调用无法回滚。你可以随时在权限与安全页收回完全访问。',
    ),
  ])
}

export async function confirmFullAccessEnable({
  executorIdentity = '当前 OS 身份',
  grantsRoot = false,
} = {}) {
  await ElMessageBox.confirm(
    warningContent({ executorIdentity, grantsRoot }),
    '开启完全访问？',
    {
      confirmButtonText: '确认开启',
      cancelButtonText: '取消',
      distinguishCancelAndClose: true,
      closeOnClickModal: false,
      customClass: DIALOG_CLASS,
      confirmButtonClass: 'el-button--danger',
    },
  )
}
