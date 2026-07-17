import { http } from '@/utils/request'

export const scheduledTasksApi = {
  /** 从发布页导入当前快照到定时任务列表 */
  importTask(data) {
    return http.post('/api/v2/scheduled-tasks/import', data)
  },

  /** 分页查询定时任务列表 */
  getTasks(params = {}) {
    return http.get('/api/v2/scheduled-tasks', params)
  },

  /** 设置或修改发布时间 */
  scheduleTask(id, scheduledAt) {
    return http.patch(`/api/v2/scheduled-tasks/${id}/schedule`, {
      scheduled_at: scheduledAt,
    })
  },

  /** 立即执行定时任务 */
  runNow(id) {
    return http.post(`/api/v2/scheduled-tasks/${id}/run-now`)
  },

  /** 删除或取消定时任务 */
  deleteTask(id) {
    return http.delete(`/api/v2/scheduled-tasks/${id}`)
  },

  /** 获取任务详情弹窗数据 */
  getTaskDetail(id) {
    return http.get(`/api/v2/scheduled-tasks/${id}/detail`)
  },
}
