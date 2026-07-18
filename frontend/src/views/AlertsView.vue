<template>
  <div class="kg-page alerts-page">
    <div class="kg-page-inner alerts-inner">
      <div v-if="alertsLoading" class="alerts-state" role="status" aria-live="polite">
        <span class="kg-spinner" aria-hidden="true"></span>
        <div><strong>正在读取告警配置</strong><span>正在同步待处理告警、规则、渠道与历史记录。</span></div>
      </div>

      <div v-else-if="alertsLoadError" class="alerts-state is-error" role="alert">
        <KgIcon name="warning" :size="19" />
        <div><strong>告警配置暂时未加载</strong><span>{{ alertsLoadError }}</span></div>
        <el-button :loading="alertsLoading" @click="loadAlerts">重新加载</el-button>
      </div>

      <div v-else class="alerts-tabs-shell">
        <div class="tab-actions">
          <el-button
            v-if="tab === 'pending'"
            type="primary"
            :loading="pendingAlertsAcknowledgingAll"
            :disabled="alertsLoading || pendingAlertsLoading || !pendingAlerts.length || pendingAlertAckingIds.size > 0"
            aria-label="一键确认全部待处理告警"
            @click="ackAllPending"
          >
            <KgIcon v-if="!pendingAlertsAcknowledgingAll" name="check" :size="15" />
            一键确认
          </el-button>
          <el-button
            v-else-if="tab === 'rules'"
            type="primary"
            :disabled="alertsLoading || activeSectionUnavailable"
            aria-label="新建告警规则"
            @click="openRuleDialog()"
          >
            <KgIcon name="plus" :size="15" />
            新建规则
          </el-button>
          <el-button
            v-else-if="tab === 'channels'"
            type="primary"
            :disabled="alertsLoading || activeSectionUnavailable"
            aria-label="新建推送渠道"
            @click="openChDialog()"
          >
            <KgIcon name="plus" :size="15" />
            新建渠道
          </el-button>
          <el-button
            v-else
            text
            type="danger"
            :loading="clearingHistory"
            :disabled="alertsLoading || activeSectionUnavailable || !history.length"
            aria-label="清空全部告警历史"
            @click="clearHistory"
          >清空历史</el-button>
        </div>

      <el-tabs v-model="tab" class="main-tabs">
        <el-tab-pane name="pending">
          <template #label>
            <span class="tab-label">
              待处理
              <span>{{ tabCountText(pendingAlertCount) }}</span>
            </span>
          </template>

          <section class="alerts-section" aria-label="待处理告警">
            <div
              v-if="pendingAlertsError && pendingAlertsLoaded"
              class="section-refresh-warning"
              role="status"
            >
              <KgIcon name="warning" :size="15" />
              <span>待处理告警刷新未完成，当前显示最近一次结果。</span>
              <el-button text size="small" :loading="pendingAlertsLoading" @click="refreshPendingSection">重试</el-button>
            </div>
            <div
              v-if="pendingAlertsError && !pendingAlertsLoaded"
              class="kg-empty alerts-empty is-error"
              role="alert"
            >
              <KgIcon name="warning" :size="24" />
              <strong>暂时无法读取待处理告警</strong>
              <span>{{ pendingAlertsError }}</span>
              <el-button :loading="pendingAlertsLoading" @click="refreshPendingSection">重新加载</el-button>
            </div>
            <template v-else-if="pendingAlerts.length">
              <el-table :data="pagedPendingAlerts" class="wide-table alert-table pending-table">
                <el-table-column label="产生时间" width="150">
                  <template #default="{ row }"><span class="time-text">{{ fmtTime(row.ts) }}</span></template>
                </el-table-column>
                <el-table-column label="告警" min-width="290">
                  <template #default="{ row }">
                    <div class="pending-copy">
                      <strong>{{ row.title }}</strong>
                      <span>{{ row.message }}</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="当前值" width="92">
                  <template #default="{ row }"><code class="condition">{{ row.metric || '—' }}</code></template>
                </el-table-column>
                <el-table-column label="严重度" width="90">
                  <template #default="{ row }">
                    <span class="severity" :class="row.severity">
                      <span class="severity-dot"></span>{{ severityLabel(row.severity) }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column label="" width="94" align="right">
                  <template #default="{ row }">
                    <el-button
                      text
                      :loading="pendingAlertAckingIds.has(row.id)"
                      :disabled="pendingAlertsAcknowledgingAll || pendingAlertAckingIds.has(row.id)"
                      :aria-label="`确认告警 ${row.title}`"
                      @click="ackPending(row)"
                    >确认</el-button>
                  </template>
                </el-table-column>
              </el-table>

              <div class="compact-list pending-compact">
                <article v-for="alert in pagedPendingAlerts" :key="alert.id" class="compact-record pending-record">
                  <div class="compact-head">
                    <strong>{{ alert.title }}</strong>
                    <span class="severity" :class="alert.severity">
                      <span class="severity-dot"></span>{{ severityLabel(alert.severity) }}
                    </span>
                    <time>{{ fmtTime(alert.ts) }}</time>
                  </div>
                  <div class="compact-meta">
                    <span>{{ alert.metric || '暂无指标值' }}</span>
                    <span>{{ alert.kind || '系统状态' }}</span>
                  </div>
                  <p class="compact-message">{{ alert.message }}</p>
                  <div class="compact-actions">
                    <el-button
                      text
                      :loading="pendingAlertAckingIds.has(alert.id)"
                      :disabled="pendingAlertsAcknowledgingAll || pendingAlertAckingIds.has(alert.id)"
                      @click="ackPending(alert)"
                    >确认告警</el-button>
                  </div>
                </article>
              </div>

              <div class="alerts-pagination">
                <el-pagination
                  v-model:current-page="pendingPage"
                  :page-size="PAGE_SIZE"
                  :total="pendingAlerts.length"
                  :pager-count="5"
                  layout="total, prev, pager, next"
                />
              </div>
            </template>

            <div v-else class="kg-empty alerts-empty pending-empty">
              <KgIcon name="check" :size="24" />
              <strong>当前没有待处理告警</strong>
              <span>系统检测到需要关注的风险时，会在这里提醒你。</span>
            </div>
          </section>
        </el-tab-pane>

        <el-tab-pane name="rules">
          <template #label>
            <span class="tab-label">规则 <span>{{ tabCountText(rules.length) }}</span></span>
          </template>

          <section class="alerts-section" aria-label="告警规则">
          <div v-if="ruleLoadError && rules.length" class="section-refresh-warning" role="status">
            <KgIcon name="warning" :size="15" />
            <span>规则列表刷新未完成，当前显示最近一次结果。</span>
            <el-button text size="small" :loading="ruleRefreshing" @click="refreshRules">重试</el-button>
          </div>
          <div v-if="ruleLoadError && !rules.length" class="kg-empty alerts-empty is-error" role="alert">
            <KgIcon name="warning" :size="24" />
            <strong>暂时无法读取告警规则</strong>
            <span>{{ ruleLoadError }}</span>
            <el-button :loading="ruleRefreshing" @click="refreshRules">重新加载</el-button>
          </div>
          <template v-else-if="rules.length">
            <el-table :data="pagedRules" class="wide-table alert-table">
              <el-table-column label="规则名称" prop="name" min-width="145" />
              <el-table-column label="指标" min-width="150">
                <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
              </el-table-column>
              <el-table-column label="条件" width="116">
                <template #default="{ row }"><code class="condition">{{ conditionText(row) }}</code></template>
              </el-table-column>
              <el-table-column label="严重度" width="92">
                <template #default="{ row }">
                  <span class="severity" :class="row.severity">
                    <span class="severity-dot"></span>{{ severityLabel(row.severity) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="沉默期" width="86">
                <template #default="{ row }">{{ row.silence_minutes }} 分钟</template>
              </el-table-column>
              <el-table-column label="通知" width="156">
                <template #default="{ row }">
                  <div class="notification-cell">
                    <span v-if="!ruleChannels(row).length" class="notification-empty">仅记录</span>
                    <span v-else class="channel-list">
                      <span v-for="channel in ruleChannels(row)" :key="channel.id" class="channel-chip">
                        {{ channel.name }}
                      </span>
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="84" align="left">
                <template #default="{ row }">
                  <el-switch
                    :model-value="row.enabled"
                    size="small"
                    :loading="togglingRuleId === row.id"
                    :disabled="isRuleBusy(row.id)"
                    :aria-label="`${row.name}：${row.enabled ? '停用规则' : '启用规则'}`"
                    @change="toggleRule(row)"
                  />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="176" align="center">
                <template #default="{ row }">
                  <div class="row-actions rule-row-actions">
                    <el-button text :disabled="isRuleBusy(row.id)" @click="openRuleDialog(row)">编辑</el-button>
                    <el-button
                      text
                      type="danger"
                      :loading="deletingRuleId === row.id"
                      :disabled="isRuleBusy(row.id)"
                      :aria-label="`删除规则 ${row.name}`"
                      @click="deleteRule(row.id)"
                    >删除</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>

            <div class="compact-list rules-compact">
              <article v-for="rule in pagedRules" :key="rule.id" class="compact-record">
                <div class="compact-head">
                  <strong>{{ rule.name }}</strong>
                  <span class="severity" :class="rule.severity">
                    <span class="severity-dot"></span>{{ severityLabel(rule.severity) }}
                  </span>
                  <el-switch
                    :model-value="rule.enabled"
                    size="small"
                    :loading="togglingRuleId === rule.id"
                    :disabled="isRuleBusy(rule.id)"
                    :aria-label="`${rule.name}：${rule.enabled ? '停用规则' : '启用规则'}`"
                    @change="toggleRule(rule)"
                  />
                </div>
                <div class="compact-meta">
                  <span>{{ metricLabel(rule.metric) }} {{ conditionText(rule) }}</span>
                  <span>沉默 {{ rule.silence_minutes }} 分钟</span>
                  <span>{{ channelSummary(rule) }}</span>
                </div>
                <div class="compact-actions">
                  <el-button text :disabled="isRuleBusy(rule.id)" @click="openRuleDialog(rule)">编辑</el-button>
                  <el-button
                    text
                    type="danger"
                    :loading="deletingRuleId === rule.id"
                    :disabled="isRuleBusy(rule.id)"
                    :aria-label="`删除规则 ${rule.name}`"
                    @click="deleteRule(rule.id)"
                  >删除</el-button>
                </div>
              </article>
            </div>

            <div class="alerts-pagination">
              <el-pagination
                v-model:current-page="rulesPage"
                :page-size="PAGE_SIZE"
                :total="rules.length"
                :pager-count="5"
                layout="total, prev, pager, next"
              />
            </div>
          </template>

          <div v-else class="kg-empty alerts-empty">
            <KgIcon name="bell" :size="24" />
            <strong>还没有告警规则</strong>
            <span>创建规则后，系统会按条件记录或推送告警。</span>
            <el-button @click="openRuleDialog()">新建规则</el-button>
          </div>
          </section>
        </el-tab-pane>

        <el-tab-pane name="channels">
          <template #label>
            <span class="tab-label">渠道 <span>{{ tabCountText(channels.length) }}</span></span>
          </template>

          <section class="alerts-section" aria-label="推送渠道">
          <div v-if="channelLoadError && channels.length" class="section-refresh-warning" role="status">
            <KgIcon name="warning" :size="15" />
            <span>渠道列表刷新未完成，当前显示最近一次结果。</span>
            <el-button text size="small" :loading="channelRefreshing" @click="refreshChannels">重试</el-button>
          </div>
          <div v-if="channelLoadError && !channels.length" class="kg-empty alerts-empty is-error" role="alert">
            <KgIcon name="warning" :size="24" />
            <strong>暂时无法读取推送渠道</strong>
            <span>{{ channelLoadError }}</span>
            <el-button :loading="channelRefreshing" @click="refreshChannels">重新加载</el-button>
          </div>
          <template v-else-if="channels.length">
            <el-table :data="pagedChannels" class="wide-table alert-table">
              <el-table-column label="渠道名称" prop="name" min-width="170" />
              <el-table-column label="类型" width="108">
                <template #default="{ row }">
                  <span class="type-badge">{{ channelTypeLabel(row.type) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="目标" min-width="310">
                <template #default="{ row }"><code class="target">{{ chTarget(row) }}</code></template>
              </el-table-column>
              <el-table-column label="状态" width="76" align="center">
                <template #default="{ row }">
                  <el-switch
                    :model-value="row.enabled"
                    size="small"
                    :loading="togglingChannelId === row.id"
                    :disabled="isChannelBusy(row.id)"
                    :aria-label="`${row.name}：${row.enabled ? '停用渠道' : '启用渠道'}`"
                    @change="toggleChannel(row)"
                  />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="184" align="center">
                <template #default="{ row }">
                  <div class="row-actions">
                    <el-button
                      text
                      :loading="testingChannelId === row.id"
                      :disabled="isChannelBusy(row.id)"
                      :aria-label="`测试渠道 ${row.name}`"
                      @click="testChannel(row)"
                    >测试</el-button>
                    <el-button text :disabled="isChannelBusy(row.id)" @click="openChDialog(row)">编辑</el-button>
                    <el-button
                      text
                      type="danger"
                      :loading="deletingChannelId === row.id"
                      :disabled="isChannelBusy(row.id)"
                      :aria-label="`删除渠道 ${row.name}`"
                      @click="deleteChannel(row.id)"
                    >删除</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>

            <div class="compact-list channels-compact">
              <article v-for="channel in pagedChannels" :key="channel.id" class="compact-record">
                <div class="compact-head">
                  <strong>{{ channel.name }}</strong>
                  <span class="type-badge">{{ channelTypeLabel(channel.type) }}</span>
                  <el-switch
                    :model-value="channel.enabled"
                    size="small"
                    :loading="togglingChannelId === channel.id"
                    :disabled="isChannelBusy(channel.id)"
                    :aria-label="`${channel.name}：${channel.enabled ? '停用渠道' : '启用渠道'}`"
                    @change="toggleChannel(channel)"
                  />
                </div>
                <code class="compact-target">{{ chTarget(channel) }}</code>
                <div class="compact-actions">
                  <el-button
                    text
                    :loading="testingChannelId === channel.id"
                    :disabled="isChannelBusy(channel.id)"
                    @click="testChannel(channel)"
                  >测试</el-button>
                  <el-button text :disabled="isChannelBusy(channel.id)" @click="openChDialog(channel)">编辑</el-button>
                  <el-button
                    text
                    type="danger"
                    :loading="deletingChannelId === channel.id"
                    :disabled="isChannelBusy(channel.id)"
                    @click="deleteChannel(channel.id)"
                  >删除</el-button>
                </div>
              </article>
            </div>

            <div class="alerts-pagination">
              <el-pagination
                v-model:current-page="channelsPage"
                :page-size="PAGE_SIZE"
                :total="channels.length"
                :pager-count="5"
                layout="total, prev, pager, next"
              />
            </div>
          </template>

          <div v-else class="kg-empty alerts-empty">
            <KgIcon name="activity" :size="24" />
            <strong>还没有推送渠道</strong>
            <span>未绑定渠道的规则仍会记录在告警历史中。</span>
            <el-button @click="openChDialog()">新建渠道</el-button>
          </div>
          </section>
        </el-tab-pane>

        <el-tab-pane name="history">
          <template #label>
            <span class="tab-label">历史 <span>{{ tabCountText(history.length) }}</span></span>
          </template>

          <section class="alerts-section" aria-label="告警历史">
          <div v-if="historyLoadError && history.length" class="section-refresh-warning" role="status">
            <KgIcon name="warning" :size="15" />
            <span>历史记录刷新未完成，当前显示最近一次结果。</span>
            <el-button text size="small" :loading="historyRefreshing" @click="refreshHistory">重试</el-button>
          </div>
          <div v-if="historyLoadError && !history.length" class="kg-empty alerts-empty is-error" role="alert">
            <KgIcon name="warning" :size="24" />
            <strong>暂时无法读取告警历史</strong>
            <span>{{ historyLoadError }}</span>
            <el-button :loading="historyRefreshing" @click="refreshHistory">重新加载</el-button>
          </div>
          <template v-else-if="history.length">
            <el-table
              :data="pagedHistory"
              class="wide-table alert-table history-table"
              @row-click="openHistoryDetail"
            >
              <el-table-column label="时间" width="150">
                <template #default="{ row }"><span class="time-text">{{ fmtTime(row.fired_at) }}</span></template>
              </el-table-column>
              <el-table-column label="规则" prop="rule_name" min-width="140" />
              <el-table-column label="指标" min-width="145">
                <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
              </el-table-column>
              <el-table-column label="值" width="74">
                <template #default="{ row }"><code class="condition">{{ metricValueText(row) }}</code></template>
              </el-table-column>
              <el-table-column label="严重度" width="90">
                <template #default="{ row }">
                  <span class="severity" :class="row.severity">
                    <span class="severity-dot"></span>{{ severityLabel(row.severity) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="已通知" min-width="140">
                <template #default="{ row }">
                  <span class="muted">{{ notifiedSummary(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="说明" prop="message" min-width="210">
                <template #default="{ row }"><span class="history-message">{{ row.message }}</span></template>
              </el-table-column>
              <el-table-column label="" width="48" align="right">
                <template #default="{ row }">
                  <el-button
                    text
                    circle
                    title="查看详情"
                    :aria-label="`查看告警“${row.rule_name}”详情`"
                    @click.stop="openHistoryDetail(row)"
                  ><KgIcon name="info" :size="15" /></el-button>
                </template>
              </el-table-column>
            </el-table>

            <div class="compact-list history-compact">
              <button
                v-for="item in pagedHistory"
                :key="item.id"
                type="button"
                class="compact-record history-record"
                :aria-label="`查看告警“${item.rule_name}”详情`"
                @click="openHistoryDetail(item)"
              >
                <div class="compact-head">
                  <strong>{{ item.rule_name }}</strong>
                  <span class="severity" :class="item.severity">
                    <span class="severity-dot"></span>{{ severityLabel(item.severity) }}
                  </span>
                  <time>{{ fmtTime(item.fired_at) }}</time>
                </div>
                <div class="compact-meta">
                  <span>{{ metricLabel(item.metric) }}：{{ metricValueText(item) }}</span>
                  <span>{{ notifiedSummary(item) }}</span>
                </div>
                <p class="compact-message">{{ item.message }}</p>
                <span class="compact-detail-hint">查看详情<KgIcon name="chevron" :size="12" /></span>
              </button>
            </div>

            <div class="alerts-pagination">
              <el-pagination
                v-model:current-page="historyPage"
                :page-size="PAGE_SIZE"
                :total="history.length"
                :pager-count="5"
                layout="total, prev, pager, next"
              />
            </div>
          </template>

          <div v-else class="kg-empty alerts-empty">
            <KgIcon name="check" :size="24" />
            <strong>暂时没有告警记录</strong>
            <span>规则触发后，记录会显示在这里。</span>
          </div>
          </section>
        </el-tab-pane>
      </el-tabs>
      </div>
    </div>

    <el-dialog
      v-model="historyDetailOpen"
      class="alert-history-dialog"
      title="告警详情"
      width="min(620px, calc(100vw - 28px))"
      align-center
      destroy-on-close
      @closed="clearHistoryDetail"
    >
      <template v-if="selectedHistory">
        <div class="history-detail-head">
          <span class="history-detail-icon" :class="selectedHistory.severity">
            <KgIcon name="warning" :size="18" />
          </span>
          <div>
            <strong>{{ selectedHistory.rule_name }}</strong>
            <span>{{ fmtFullTime(selectedHistory.fired_at) }}</span>
          </div>
          <span class="severity" :class="selectedHistory.severity">
            <span class="severity-dot"></span>{{ severityLabel(selectedHistory.severity) }}
          </span>
        </div>

        <dl class="history-detail-list">
          <div><dt>监控指标</dt><dd>{{ metricLabel(selectedHistory.metric) }}</dd></div>
          <div><dt>触发值</dt><dd><code>{{ metricValueText(selectedHistory) }}</code></dd></div>
          <div><dt>通知渠道</dt><dd>{{ notifiedSummary(selectedHistory) }}</dd></div>
          <div><dt>记录编号</dt><dd><code>#{{ selectedHistory.id }}</code></dd></div>
          <div><dt>规则编号</dt><dd>{{ selectedHistory.rule_id == null ? '规则已删除或不可用' : `#${selectedHistory.rule_id}` }}</dd></div>
        </dl>

        <section class="history-detail-message" aria-label="告警说明">
          <strong>告警说明</strong>
          <p>{{ selectedHistory.message || '暂无说明' }}</p>
        </section>
      </template>
      <template #footer>
        <el-button @click="historyDetailOpen = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="ruleDialog"
      :title="ruleForm.id ? '编辑规则' : '新建规则'"
      width="min(520px, calc(100vw - 28px))"
      align-center
      :close-on-click-modal="!savingRule"
      :show-close="!savingRule"
    >
      <el-form :model="ruleForm" label-position="top" class="dialog-form">
        <el-form-item label="规则名称">
          <el-input v-model="ruleForm.name" :disabled="savingRule" placeholder="例如：内存使用率过高" />
        </el-form-item>

        <el-form-item label="监控指标">
          <el-select v-model="ruleForm.metric" style="width: 100%">
            <el-option v-for="metric in METRICS" :key="metric.value" :value="metric.value" :label="metric.label" />
          </el-select>
        </el-form-item>

        <el-form-item label="触发条件">
          <div v-if="ruleForm.metric === 'failed_services'" class="static-condition">
            存在停止的自动启动服务
          </div>
          <div v-else class="condition-editor">
            <el-select v-model="ruleForm.operator" class="operator-select">
              <el-option value=">=" label=">=" />
              <el-option value=">" label=">" />
              <el-option value="<=" label="<=" />
              <el-option value="<" label="<" />
            </el-select>
            <el-input-number v-model="ruleForm.threshold" :min="0" :max="100" controls-position="right" />
            <span>%</span>
          </div>
        </el-form-item>

        <div class="form-grid">
          <el-form-item label="严重度">
            <el-radio-group v-model="ruleForm.severity">
              <el-radio value="warning">警告</el-radio>
              <el-radio value="critical">严重</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="沉默期">
            <div class="number-field">
              <el-input-number v-model="ruleForm.silence_minutes" :min="1" :max="1440" controls-position="right" />
              <span>分钟</span>
            </div>
          </el-form-item>
        </div>

        <el-form-item label="推送渠道">
          <el-select
            v-model="ruleForm.channel_ids"
            multiple
            style="width: 100%"
            placeholder="不选择则仅记录"
          >
            <el-option v-for="channel in channels" :key="channel.id" :value="channel.id" :label="channel.name" />
          </el-select>
        </el-form-item>

        <div class="enabled-row">
          <div>
            <strong>启用规则</strong>
            <span>保存后立即参与告警评估。</span>
          </div>
          <el-switch v-model="ruleForm.enabled" aria-label="启用规则" />
        </div>
      </el-form>

      <template #footer>
        <el-button :disabled="savingRule" @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingRule" @click="saveRule">保存规则</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="chDialog"
      :title="chForm.id ? '编辑渠道' : '新建渠道'"
      width="min(560px, calc(100vw - 28px))"
      align-center
      :close-on-click-modal="!savingChannel"
      :show-close="!savingChannel"
    >
      <el-form :model="chForm" label-position="top" class="dialog-form">
        <el-form-item label="渠道名称" :error="chErrors.name">
          <el-input
            v-model="chForm.name"
            :disabled="savingChannel"
            placeholder="例如：运维群 Webhook"
            :aria-invalid="Boolean(chErrors.name)"
            @input="clearChError('name')"
          />
        </el-form-item>

        <el-form-item label="渠道类型">
          <el-radio-group v-model="chForm.type" @change="clearChErrors">
            <el-radio-button value="webhook">Webhook</el-radio-button>
            <el-radio-button value="email">邮件</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="chForm.type === 'webhook'">
          <el-form-item label="URL" :error="chErrors.url">
            <el-input
              v-model="chForm.config.url"
              placeholder="https://..."
              :aria-invalid="Boolean(chErrors.url)"
              @input="clearChError('url')"
            />
          </el-form-item>
          <el-form-item label="HTTP 方法">
            <el-select v-model="chForm.config.method" style="width: 140px">
              <el-option value="POST" label="POST" />
              <el-option value="PUT" label="PUT" />
            </el-select>
          </el-form-item>
          <el-form-item label="自定义 Header" :error="headerJsonError">
            <el-input
              v-model="chForm.headersRaw"
              type="textarea"
              :rows="4"
              class="headers-input"
              placeholder='{"Authorization": "Bearer xxx"}'
              aria-label="自定义 Header JSON"
              :aria-invalid="Boolean(headerJsonError)"
              aria-describedby="header-json-help"
            />
            <span id="header-json-help" class="field-help">请输入 JSON 对象；留空表示不发送自定义 Header。</span>
          </el-form-item>
        </template>

        <template v-else>
          <div class="form-grid">
            <el-form-item label="SMTP 主机" :error="chErrors.host">
              <el-input
                v-model="chForm.config.host"
                placeholder="smtp.example.com"
                :aria-invalid="Boolean(chErrors.host)"
                @input="clearChError('host')"
              />
            </el-form-item>
            <el-form-item label="端口">
              <el-input-number v-model="chForm.config.port" :min="1" :max="65535" controls-position="right" />
            </el-form-item>
          </div>
          <el-form-item label="连接加密">
            <el-switch v-model="chForm.config.use_tls" active-text="使用 SSL/TLS" />
          </el-form-item>
          <el-form-item label="发件人账号" :error="chErrors.user">
            <el-input
              v-model="chForm.config.user"
              placeholder="noreply@example.com"
              :aria-invalid="Boolean(chErrors.user)"
              @input="clearChError('user')"
            />
          </el-form-item>
          <el-form-item label="授权码或密码" :error="chErrors.password">
            <el-input
              v-model="chForm.config.password"
              type="password"
              show-password
              :placeholder="chForm.id ? '留空则保留现有凭据' : '请输入授权码或密码'"
              :aria-invalid="Boolean(chErrors.password)"
              aria-describedby="channel-password-help"
              @input="clearChError('password')"
            />
            <span id="channel-password-help" class="field-help">
              {{ chForm.id ? '留空将保留现有凭据；填写后会替换。' : '新建邮件渠道必须填写凭据。' }}
            </span>
          </el-form-item>
          <el-form-item label="收件人" :error="chErrors.to">
            <el-input
              v-model="chForm.config.to"
              placeholder="ops@example.com"
              :aria-invalid="Boolean(chErrors.to)"
              @input="clearChError('to')"
            />
          </el-form-item>
        </template>

        <div class="enabled-row">
          <div>
            <strong>启用渠道</strong>
            <span>停用后将不再向该渠道推送。</span>
          </div>
          <el-switch v-model="chForm.enabled" aria-label="启用渠道" />
        </div>
      </el-form>

      <template #footer>
        <el-button :disabled="savingChannel" @click="chDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingChannel" @click="saveChannel">保存渠道</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import KgIcon from '../components/KgIcon.vue'
import { apiFetch } from '../composables/useApi.js'
import {
  acknowledgePendingAlert,
  acknowledgeAllPendingAlerts,
  pendingAlertAckingIds,
  pendingAlertCount,
  pendingAlerts,
  pendingAlertsError,
  pendingAlertsLoaded,
  pendingAlertsLoading,
  pendingAlertsAcknowledgingAll,
  refreshPendingAlerts,
} from '../composables/useAlerts.js'
import { alertBadgeText, resolveRuleChannels } from '../utils/alerts.js'

const tab = ref('pending')
const rules = ref([])
const channels = ref([])
const history = ref([])
const pendingPage = ref(1)
const rulesPage = ref(1)
const channelsPage = ref(1)
const historyPage = ref(1)
const historyDetailOpen = ref(false)
const selectedHistory = ref(null)
const alertsLoading = ref(true)
const alertsLoadError = ref('')
const ruleLoadError = ref('')
const channelLoadError = ref('')
const historyLoadError = ref('')
const ruleRefreshing = ref(false)
const channelRefreshing = ref(false)
const historyRefreshing = ref(false)
const savingRule = ref(false)
const togglingRuleId = ref(null)
const deletingRuleId = ref(null)
const savingChannel = ref(false)
const togglingChannelId = ref(null)
const deletingChannelId = ref(null)
const testingChannelId = ref(null)
const clearingHistory = ref(false)

const PAGE_SIZE = 10

function pageItems(items, page) {
  const start = (page - 1) * PAGE_SIZE
  return items.slice(start, start + PAGE_SIZE)
}

function clampPage(page, count) {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(count / PAGE_SIZE)))
}

const pagedPendingAlerts = computed(() => pageItems(pendingAlerts.value, pendingPage.value))
const pagedRules = computed(() => pageItems(rules.value, rulesPage.value))
const pagedChannels = computed(() => pageItems(channels.value, channelsPage.value))
const pagedHistory = computed(() => pageItems(history.value, historyPage.value))

watch(() => pendingAlerts.value.length, count => clampPage(pendingPage, count))
watch(() => rules.value.length, count => clampPage(rulesPage, count))
watch(() => channels.value.length, count => clampPage(channelsPage, count))
watch(() => history.value.length, count => clampPage(historyPage, count))

const METRICS = [
  { value: 'memory_pct', label: '内存使用率 (%)' },
  { value: 'cpu_pct', label: 'CPU 使用率 (%)' },
  { value: 'disk_pct', label: '磁盘使用率 (%)（任意盘）' },
  { value: 'failed_services', label: '停止的自动启动服务' },
]

const metricLabel = (value) => METRICS.find(metric => metric.value === value)?.label ?? value
const severityLabel = (value) => ({ warning: '警告', critical: '严重' }[value] ?? value)
const channelTypeLabel = (value) => ({ webhook: 'Webhook', email: '邮件' }[value] ?? value)
const tabCountText = (count) => alertBadgeText(count) || '0'
const ruleChannels = (rule) => resolveRuleChannels(rule.channel_ids, channels.value)
const chTarget = (channel) => channel.type === 'webhook'
  ? channel.config.url || '—'
  : `${channel.config.user || '—'} → ${channel.config.to || '—'}`

const metricValueText = (item) => item.metric === 'failed_services'
  ? (Number.parseFloat(item.metric_value) > 0 || item.metric_value === '存在' ? '存在' : '无')
  : item.metric_value

function conditionText(rule) {
  if (rule.metric !== 'failed_services') return `${rule.operator} ${rule.threshold}%`
  return rule.operator === '>=' && Number(rule.threshold) === 1
    ? '存在失败服务'
    : `${rule.operator} ${rule.threshold}（0=无，1=有）`
}

function channelSummary(rule) {
  const names = ruleChannels(rule).map(channel => channel.name)
  return names.length ? names.join('、') : '仅记录'
}

function notifiedSummary(item) {
  return item.channels_notified.length ? item.channels_notified.join('、') : '未推送'
}

function fmtTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function fmtFullTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

async function loadRules() {
  const body = await requestJson('/api/alert-rules', {}, '无法读取告警规则')
  if (!Array.isArray(body.rules)) throw new Error('告警规则数据格式不正确')
  rules.value = body.rules
}

async function loadChannels() {
  const body = await requestJson('/api/alert-channels', {}, '无法读取推送渠道')
  if (!Array.isArray(body.channels)) throw new Error('推送渠道数据格式不正确')
  channels.value = body.channels
}

async function loadHistory() {
  const body = await requestJson('/api/alert-history', {}, '无法读取告警历史')
  if (!Array.isArray(body.history)) throw new Error('告警历史数据格式不正确')
  history.value = body.history
}

async function refreshSection(loader, errorState, refreshingState) {
  refreshingState.value = true
  try {
    await loader()
    errorState.value = ''
    return true
  } catch (reason) {
    errorState.value = reason.message || '请检查后端服务后重试'
    return false
  } finally {
    refreshingState.value = false
  }
}

const refreshRules = () => refreshSection(loadRules, ruleLoadError, ruleRefreshing)
const refreshChannels = () => refreshSection(loadChannels, channelLoadError, channelRefreshing)
const refreshHistory = () => refreshSection(loadHistory, historyLoadError, historyRefreshing)
async function refreshPendingSection() {
  try {
    await refreshPendingAlerts()
    return true
  } catch {
    return false
  }
}

async function loadAlerts() {
  alertsLoading.value = true
  alertsLoadError.value = ''
  const results = await Promise.all([
    refreshPendingSection(), refreshChannels(), refreshRules(), refreshHistory(),
  ])
  const hasRetainedData = pendingAlertsLoaded.value || rules.value.length
    || channels.value.length || history.value.length
  if (results.every((result) => !result) && !hasRetainedData) {
    alertsLoadError.value = '待处理告警、规则、渠道与历史记录均暂时无法读取，请检查后端服务后重试'
  }
  alertsLoading.value = false
}

async function responseError(response, fallback) {
  try {
    const body = await response.clone().json()
    return body.detail || body.message || fallback
  } catch {
    return fallback
  }
}

async function request(url, options = {}, fallback = '操作失败') {
  const response = await apiFetch(url, options)
  if (!response.ok) throw new Error(await responseError(response, fallback))
  return response
}

async function requestJson(url, options = {}, fallback = '请求失败') {
  const response = await request(url, options, fallback)
  try {
    return await response.json()
  } catch {
    throw new Error(`${fallback}：服务器返回的数据无法解析`)
  }
}

const activeSectionUnavailable = computed(() => ({
  pending: Boolean(pendingAlertsError.value && !pendingAlertsLoaded.value),
  rules: Boolean(ruleLoadError.value && !rules.value.length),
  channels: Boolean(channelLoadError.value && !channels.value.length),
  history: Boolean(historyLoadError.value && !history.value.length),
}[tab.value]))
const isRuleBusy = () => savingRule.value
  || togglingRuleId.value != null || deletingRuleId.value != null
const isChannelBusy = () => savingChannel.value || togglingChannelId.value != null
  || deletingChannelId.value != null || testingChannelId.value != null
const isDialogCancel = (reason) => reason === 'cancel' || reason === 'close'
  || reason?.action === 'cancel' || reason?.action === 'close'

onMounted(loadAlerts)

async function ackPending(alert) {
  try {
    await acknowledgePendingAlert(alert)
    ElMessage.success('告警已确认')
  } catch (reason) {
    ElMessage.error(reason.message || '告警暂时无法确认，请重试')
  }
}

async function ackAllPending() {
  try {
    const count = await acknowledgeAllPendingAlerts()
    ElMessage.success(count ? `已确认 ${count} 条待处理告警` : '待处理告警已同步')
  } catch (error) {
    ElMessage.error(error.message || '待处理告警批量确认失败，请重试')
  }
}

function openHistoryDetail(item) {
  selectedHistory.value = item
  historyDetailOpen.value = true
}

function clearHistoryDetail() {
  selectedHistory.value = null
}

const ruleDialog = ref(false)
const ruleForm = reactive({
  id: null, name: '', metric: 'memory_pct', operator: '>=',
  threshold: 85, severity: 'warning', silence_minutes: 10,
  channel_ids: [], enabled: true,
})

function openRuleDialog(row = null) {
  if (savingRule.value) return
  if (row) {
    Object.assign(ruleForm, { ...row, channel_ids: [...row.channel_ids] })
  } else {
    Object.assign(ruleForm, {
      id: null, name: '', metric: 'memory_pct', operator: '>=',
      threshold: 85, severity: 'warning', silence_minutes: 10,
      channel_ids: [], enabled: true,
    })
  }
  ruleDialog.value = true
}

async function saveRule() {
  if (savingRule.value) return
  if (!ruleForm.name.trim()) {
    ElMessage.warning('请输入规则名称')
    return
  }
  const body = {
    name: ruleForm.name, metric: ruleForm.metric,
    operator: ruleForm.metric === 'failed_services' ? '>=' : ruleForm.operator,
    threshold: ruleForm.metric === 'failed_services' ? 1 : ruleForm.threshold,
    severity: ruleForm.severity, silence_minutes: ruleForm.silence_minutes,
    channel_ids: ruleForm.channel_ids, enabled: ruleForm.enabled,
  }
  const url = ruleForm.id ? `/api/alert-rules/${ruleForm.id}` : '/api/alert-rules'
  const method = ruleForm.id ? 'PUT' : 'POST'
  savingRule.value = true
  try {
    await request(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, '规则保存失败')
    ruleDialog.value = false
    const refreshed = await refreshRules()
    if (refreshed) ElMessage.success('规则已保存')
    else ElMessage.warning('规则已保存，但列表刷新失败；当前显示可能未更新')
  } catch (reason) {
    ElMessage.error(reason.message || '规则保存失败')
  } finally {
    savingRule.value = false
  }
}

async function toggleRule(row) {
  if (isRuleBusy(row.id)) return
  const body = {
    name: row.name, metric: row.metric, operator: row.operator,
    threshold: row.threshold, severity: row.severity,
    silence_minutes: row.silence_minutes, channel_ids: row.channel_ids,
    enabled: !row.enabled,
  }
  togglingRuleId.value = row.id
  try {
    await request(`/api/alert-rules/${row.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, '规则状态更新失败')
    if (!await refreshRules()) {
      ElMessage.warning('规则状态已更新，但列表刷新失败；当前显示可能未更新')
    }
  } catch (reason) {
    ElMessage.error(reason.message || '规则状态更新失败')
  } finally {
    togglingRuleId.value = null
  }
}

async function deleteRule(id) {
  if (isRuleBusy(id)) return
  try {
    await ElMessageBox.confirm('确定删除该规则？', '确认', { type: 'warning' })
  } catch (reason) {
    if (isDialogCancel(reason)) return
    ElMessage.error(reason.message || '无法打开删除确认')
    return
  }
  deletingRuleId.value = id
  try {
    await request(`/api/alert-rules/${id}`, { method: 'DELETE' }, '规则删除失败')
    const refreshed = await refreshRules()
    if (refreshed) ElMessage.success('规则已删除')
    else ElMessage.warning('规则已删除，但列表刷新失败；当前显示可能未更新')
  } catch (reason) {
    ElMessage.error(reason.message || '规则删除失败')
  } finally {
    deletingRuleId.value = null
  }
}

const chDialog = ref(false)
const chForm = reactive({
  id: null, name: '', type: 'webhook', enabled: true,
  config: { url: '', method: 'POST', use_tls: true, host: '', port: 465, user: '', password: '', to: '' },
  headersRaw: '',
})
const chErrors = reactive({ name: '', url: '', host: '', user: '', password: '', to: '' })
const existingChannelPassword = ref('')

function clearChErrors() {
  for (const key of Object.keys(chErrors)) chErrors[key] = ''
}

function clearChError(field) {
  chErrors[field] = ''
}

function validateChannelForm() {
  clearChErrors()

  if (!chForm.name.trim()) chErrors.name = '请输入渠道名称'

  if (chForm.type === 'webhook') {
    const rawUrl = chForm.config.url.trim()
    if (!rawUrl) {
      chErrors.url = '请输入 Webhook URL'
    } else if (!/^https?:\/\//i.test(rawUrl)) {
      chErrors.url = '请输入以 http:// 或 https:// 开头的绝对 URL'
    } else {
      try {
        const url = new URL(rawUrl)
        if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) {
          chErrors.url = '请输入有效的 http/https 绝对 URL'
        }
      } catch {
        chErrors.url = '请输入有效的 http/https 绝对 URL'
      }
    }
  } else {
    if (!chForm.config.host.trim()) chErrors.host = '请输入 SMTP 主机'
    if (!chForm.config.user.trim()) chErrors.user = '请输入发件人账号'
    if (!chForm.config.to.trim()) chErrors.to = '请输入收件人'
    if (!chForm.config.password.trim() && (!chForm.id || !existingChannelPassword.value)) {
      chErrors.password = '请输入授权码或密码'
    }
  }

  return !Object.values(chErrors).some(Boolean)
}

const headerJsonError = computed(() => {
  if (chForm.type !== 'webhook' || !chForm.headersRaw.trim()) return ''
  try {
    const parsed = JSON.parse(chForm.headersRaw)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      return '自定义 Header 必须是 JSON 对象，例如 {"X-Token":"value"}'
    }
    return ''
  } catch {
    return 'JSON 格式不正确，请检查引号、逗号和括号'
  }
})

function openChDialog(row = null) {
  if (savingChannel.value) return
  clearChErrors()
  if (row) {
    existingChannelPassword.value = row.type === 'email' ? (row.config.password ?? '') : ''
    Object.assign(chForm, {
      id: row.id, name: row.name, type: row.type, enabled: row.enabled,
      config: {
        url: row.config.url ?? '',
        method: row.config.method ?? 'POST',
        use_tls: row.config.use_tls ?? true,
        host: row.config.host ?? '',
        port: row.config.port ?? 465,
        user: row.config.user ?? '',
        password: '',
        to: row.config.to ?? '',
      },
      headersRaw: row.config.headers ? JSON.stringify(row.config.headers, null, 2) : '',
    })
  } else {
    existingChannelPassword.value = ''
    Object.assign(chForm, {
      id: null, name: '', type: 'webhook', enabled: true,
      config: { url: '', method: 'POST', use_tls: true, host: '', port: 465, user: '', password: '', to: '' },
      headersRaw: '',
    })
  }
  chDialog.value = true
}

function buildChConfig() {
  if (chForm.type === 'webhook') {
    const headers = chForm.headersRaw.trim() ? JSON.parse(chForm.headersRaw) : {}
    return { url: chForm.config.url.trim(), method: chForm.config.method, headers }
  }
  const password = chForm.config.password.trim()
    ? chForm.config.password
    : existingChannelPassword.value
  return {
    host: chForm.config.host.trim(), port: chForm.config.port,
    user: chForm.config.user.trim(), password,
    to: chForm.config.to.trim(), use_tls: chForm.config.use_tls,
  }
}

async function saveChannel() {
  if (savingChannel.value) return
  if (!validateChannelForm() || headerJsonError.value) {
    ElMessage.warning('请先修正表单中标注的问题')
    return
  }
  const body = { name: chForm.name.trim(), type: chForm.type, config: buildChConfig(), enabled: chForm.enabled }
  const url = chForm.id ? `/api/alert-channels/${chForm.id}` : '/api/alert-channels'
  const method = chForm.id ? 'PUT' : 'POST'
  savingChannel.value = true
  try {
    await request(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, '渠道保存失败')
    chDialog.value = false
    const refreshed = await refreshChannels()
    if (refreshed) ElMessage.success('渠道已保存')
    else ElMessage.warning('渠道已保存，但列表刷新失败；当前显示可能未更新')
  } catch (reason) {
    ElMessage.error(reason.message || '渠道保存失败')
  } finally {
    savingChannel.value = false
  }
}

async function toggleChannel(row) {
  if (isChannelBusy(row.id)) return
  const body = { name: row.name, type: row.type, config: row.config, enabled: !row.enabled }
  togglingChannelId.value = row.id
  try {
    await request(`/api/alert-channels/${row.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, '渠道状态更新失败')
    if (!await refreshChannels()) {
      ElMessage.warning('渠道状态已更新，但列表刷新失败；当前显示可能未更新')
    }
  } catch (reason) {
    ElMessage.error(reason.message || '渠道状态更新失败')
  } finally {
    togglingChannelId.value = null
  }
}

async function deleteChannel(id) {
  if (isChannelBusy(id)) return
  try {
    await ElMessageBox.confirm('确定删除该渠道？', '确认', { type: 'warning' })
  } catch (reason) {
    if (isDialogCancel(reason)) return
    ElMessage.error(reason.message || '无法打开删除确认')
    return
  }
  deletingChannelId.value = id
  try {
    await request(`/api/alert-channels/${id}`, { method: 'DELETE' }, '渠道删除失败')
    channels.value = channels.value.filter(channel => channel.id !== id)
    rules.value = rules.value.map(rule => ({
      ...rule,
      channel_ids: rule.channel_ids.filter(channelId => channelId !== id),
    }))
    const [channelsRefreshed, rulesRefreshed] = await Promise.all([
      refreshChannels(),
      refreshRules(),
    ])
    if (channelsRefreshed && rulesRefreshed) ElMessage.success('渠道已删除')
    else ElMessage.warning('渠道已删除，但关联列表刷新失败；当前显示可能未更新')
  } catch (reason) {
    ElMessage.error(reason.message || '渠道删除失败')
  } finally {
    deletingChannelId.value = null
  }
}

async function testChannel(row) {
  if (isChannelBusy(row.id)) return
  testingChannelId.value = row.id
  try {
    const body = await requestJson(
      `/api/alert-channels/${row.id}/test`,
      { method: 'POST' },
      '测试推送失败',
    )
    if (body.ok) {
      ElMessage.success(`测试推送成功：${body.message}`)
    } else {
      ElMessage.error(`测试推送失败：${body.message || '渠道未接受消息'}`)
    }
  } catch (reason) {
    ElMessage.error(reason.message || '测试推送失败')
  } finally {
    testingChannelId.value = null
  }
}

async function clearHistory() {
  if (clearingHistory.value) return
  try {
    await ElMessageBox.confirm('确定清空所有告警历史？', '确认', { type: 'warning' })
  } catch (reason) {
    if (isDialogCancel(reason)) return
    ElMessage.error(reason.message || '无法打开清空确认')
    return
  }
  clearingHistory.value = true
  try {
    await request('/api/alert-history', { method: 'DELETE' }, '告警历史清空失败')
    historyDetailOpen.value = false
    selectedHistory.value = null
    const refreshed = await refreshHistory()
    if (refreshed) ElMessage.success('告警历史已清空')
    else ElMessage.warning('告警历史已清空，但列表刷新失败；当前显示可能未更新')
  } catch (reason) {
    ElMessage.error(reason.message || '告警历史清空失败')
  } finally {
    clearingHistory.value = false
  }
}
</script>

<style scoped>
.alerts-inner { width: 100%; }

.alerts-tabs-shell { position: relative; }

.tab-actions {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 2;
  height: 38px;
  display: flex;
  align-items: center;
}

.tab-actions :deep(.el-button) { gap: 7px; }
.main-tabs { margin-top: 0; }
.main-tabs :deep(.el-tabs__nav-wrap) { padding-right: 132px; }
.main-tabs :deep(.el-tabs__header) { margin-bottom: var(--kg-space-3); }
.main-tabs :deep(.el-tabs__content) { overflow: visible; }

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tab-label > span {
  min-width: 18px;
  padding: 0 5px;
  border-radius: var(--kg-radius-pill);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 10px;
  line-height: 18px;
  text-align: center;
}

.alert-table { width: 100%; }

.pending-copy {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.pending-copy strong {
  overflow: hidden;
  color: var(--kg-text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-copy span {
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 11px;
  line-height: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.condition,
.target,
.time-text {
  color: var(--kg-text-secondary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
}

.target {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time-text { color: var(--kg-text-tertiary); }

.severity {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--kg-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.severity.warning { color: var(--kg-warning); }
.severity.critical { color: var(--kg-danger); }

.severity-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.notification-cell {
  min-width: 0;
  min-height: 23px;
  display: flex;
  align-items: center;
}

.channel-list { min-width: 0; display: flex; flex-wrap: wrap; gap: 4px; }

.channel-chip,
.type-badge,
.notification-empty {
  display: inline-flex;
  align-items: center;
  min-height: 21px;
  padding: 1px 6px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-xs);
  color: var(--kg-text-secondary);
  font-size: 11px;
}

.type-badge { color: var(--kg-info); }
.notification-empty {
  border-color: transparent;
  color: var(--kg-text-disabled);
}
.muted { color: var(--kg-text-disabled); font-size: 12px; }

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0;
  white-space: nowrap;
}

.row-actions :deep(.el-button + .el-button) { margin-left: 0; }
.rule-row-actions { justify-content: center; }

.history-message {
  display: block;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-table :deep(.el-table__row) { cursor: pointer; }
.history-table :deep(.el-table__row:hover .history-message) { color: var(--kg-text-secondary); }

.alerts-empty {
  min-height: 230px;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.alerts-pagination {
  display: flex;
  justify-content: flex-end;
  padding: var(--kg-space-4) 0 var(--kg-space-2);
}

.compact-list { display: none; }

.compact-record {
  width: 100%;
  padding: var(--kg-space-3) 0;
  border-bottom: 1px solid var(--kg-border-subtle);
}

.pending-record { border-left: 3px solid var(--kg-warning-border); padding-left: var(--kg-space-3); }
.pending-record:has(.severity.critical) { border-left-color: var(--kg-danger); }

.compact-head {
  display: flex;
  align-items: center;
  gap: var(--kg-space-3);
}

.compact-head strong {
  min-width: 0;
  overflow: hidden;
  color: var(--kg-text-primary);
  font-size: 13px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-head > :last-child { margin-left: auto; }
.compact-head time { margin-left: auto; color: var(--kg-text-tertiary); font-family: var(--kg-font-mono); font-size: 11px; }

.compact-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 14px;
  margin-top: 7px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.compact-target {
  display: block;
  margin-top: 7px;
  overflow: hidden;
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
}

.compact-actions :deep(.el-button + .el-button) { margin-left: 0; }

.compact-message {
  margin: 6px 0 0;
  color: var(--kg-text-secondary);
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}

.history-record {
  display: block;
  border-top: 0;
  border-right: 0;
  border-left: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--kg-motion-fast);
}

.history-record:hover { background: var(--kg-bg-surface-2); }
.history-record:focus-visible { outline: 2px solid var(--kg-focus); outline-offset: 2px; }

.compact-detail-hint {
  margin-top: 7px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--kg-accent);
  font-size: 11px;
}

:global(.alert-history-dialog .el-dialog__body) {
  max-height: calc(100vh - 190px);
  overflow-y: auto;
}

.history-detail-head {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--kg-space-3);
  padding-bottom: var(--kg-space-4);
  border-bottom: 1px solid var(--kg-border-subtle);
}

.history-detail-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border: 1px solid var(--kg-warning-border);
  border-radius: var(--kg-radius-md);
  background: var(--kg-warning-soft);
  color: var(--kg-warning);
}

.history-detail-icon.critical {
  border-color: var(--kg-danger-border);
  background: var(--kg-danger-soft);
  color: var(--kg-danger);
}

.history-detail-head > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.history-detail-head strong {
  overflow: hidden;
  color: var(--kg-text-primary);
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-detail-head > div > span {
  color: var(--kg-text-tertiary);
  font-family: var(--kg-font-mono);
  font-size: 11px;
}

.history-detail-list {
  margin: var(--kg-space-4) 0 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: var(--kg-space-6);
}

.history-detail-list > div {
  min-width: 0;
  padding: 9px 0;
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: var(--kg-space-2);
  border-bottom: 1px solid var(--kg-border-subtle);
}

.history-detail-list dt {
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.history-detail-list dd {
  min-width: 0;
  margin: 0;
  color: var(--kg-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.history-detail-list code {
  color: var(--kg-text-secondary);
  font: 11px/1.5 var(--kg-font-mono);
}

.history-detail-message { margin-top: var(--kg-space-4); }
.history-detail-message strong { color: var(--kg-text-primary); font-size: 12px; font-weight: 600; }
.history-detail-message p {
  margin: 7px 0 0;
  padding: 10px 12px;
  border-left: 3px solid var(--kg-warning-border);
  background: var(--kg-bg-surface-2);
  color: var(--kg-text-secondary);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.dialog-form :deep(.el-form-item) { margin-bottom: 17px; }
.dialog-form :deep(.el-form-item__label) { margin-bottom: 6px; color: var(--kg-text-secondary); font-size: 12px; line-height: 18px; }

.condition-editor,
.number-field {
  display: flex;
  align-items: center;
  gap: var(--kg-space-2);
  width: 100%;
}

.operator-select { width: 96px; }
.condition-editor :deep(.el-input-number) { flex: 1; width: auto; }
.condition-editor > span,
.number-field > span { color: var(--kg-text-tertiary); font-size: 12px; }

.static-condition {
  width: 100%;
  min-height: 32px;
  padding: 6px 10px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
  color: var(--kg-text-secondary);
  font-size: 12px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--kg-space-4);
}

.form-grid :deep(.el-input-number) { width: 100%; }

.enabled-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--kg-space-5);
  min-height: 54px;
  padding: 9px 12px;
  border: 1px solid var(--kg-border-subtle);
  border-radius: var(--kg-radius-md);
  background: var(--kg-bg-surface-1);
}

.enabled-row strong {
  display: block;
  color: var(--kg-text-secondary);
  font-size: 12px;
  font-weight: 550;
}

.enabled-row span {
  display: block;
  margin-top: 2px;
  color: var(--kg-text-tertiary);
  font-size: 11px;
}

.headers-input :deep(.el-textarea__inner) {
  font-family: var(--kg-font-mono);
  font-size: 11px;
}

@media (max-width: 1280px) {
  .wide-table { display: none; }
  .compact-list { display: grid; }
}

@media (max-width: 560px) {
  .main-tabs :deep(.el-tabs__nav-wrap) { padding-right: 112px; }

  .alerts-pagination {
    justify-content: center;
    overflow: hidden;
  }

  .alerts-pagination :deep(.el-pager li) { min-width: 24px; }
  .history-detail-head { grid-template-columns: 34px minmax(0, 1fr); }
  .history-detail-icon { width: 34px; height: 34px; }
  .history-detail-head > .severity { grid-column: 2; }
  .history-detail-list { grid-template-columns: 1fr; }
}
</style>
