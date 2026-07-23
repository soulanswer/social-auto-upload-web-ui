# PR: feat(v1.3.0) 定时任务主线合流 + 账号运营数据同步 + 视频号活动能力并入

## 概述

这次变更不是单点功能，而是一次 `hn_gx <- master` 的主线整理。

目标很明确：

- 保留 `hn_gx` 的核心能力：定时任务、结构化发布诊断日志、发布时间校验、发布结果判定、账号个性化配置语义
- 同步 `master` 的最新能力：账号运营数据同步、视频号活动搜索、统一搜索组件优化、左侧栏交互强化、标题自动填充层级语义
- 将合并后的版本面统一整理成可继续验证和发布的 `v1.3.0`

---

## PR 类型

- [x] 新功能：定时任务全链路、账号运营数据同步、视频号活动参与
- [x] 合流整合：`hn_gx` 与 `master` 主线能力兼容同步
- [x] 稳定性增强：结构化诊断日志、发布时间校验、发布结果判定
- [x] 交互优化：左侧栏互斥展开、选中态强化、标题自动填充按选中层级生效
- [x] 文档：版本号、PR 文档、changelog 重写为合并后真实状态

---

## 核心变更

### 1. 定时任务全链路继续以 `hn_gx` 为主线

- 发布中心支持导入当前配置到定时任务
- 新增定时任务列表、详情、重跑、删除、排期设置
- SSE 实时刷新任务状态
- 草稿合并和任务标题映射继续沿用 `hn_gx` 逻辑

### 2. 同步 `master` 的账号运营数据能力

- 账号管理页保留运营数据卡片展示
- `sync_profile` 统一兼容 `dict{name, avatar, stats}` 契约
- 视频号、支付宝等平台在同步资料时，同时保留 `hn_gx` 的 `storage_state` 回写和 `master` 的 `stats` 抓取

### 3. 视频号活动能力正式并入主线

- 发布页新增视频号“活动”搜索卡片
- 后端新增 `/api/channels/activities`
- 定时任务 / 草稿合并链路补齐 `channelsActivityName` 和 `channelsActivityData`
- 直接发布与定时任务发布两条链路使用同一套活动字段

### 4. 左侧栏交互和标题自动填充语义同步到 `hn_gx`

- 左侧平台改为互斥展开 / 收起
- 左侧账号选中态视觉强化
- 自动填充标题按当前选中层级生效：
  - 选中账号：只替换当前账号标题
  - 选中平台：替换当前平台及其已勾选账号标题
  - 未选中：全量替换所有平台和已勾选账号标题

### 5. 发布稳定性增强保持 `hn_gx` 优先

- 保留结构化发布诊断日志工具与请求结果日志
- 保留头条号、腾讯视频、支付宝等平台的发布时间校验与发布结果判定增强
- 保留登录校验和资料同步后的 `storage_state` 回写策略

### 6. 搜索组件与发布细节优化同步保留

- `RemoteSearchSelect` 支持 2 秒自动搜索
- 下拉项卡片式布局与紧凑间距优化
- 小红书拍摄地点改为统一公共组件
- 小红书 / 视频号标签输入稳定性修复同步保留

---

## 已接受的混合解法

这 4 个文件已经明确采用“`hn_gx` 为主、`master` 补缺”的混合策略：

- `frontend/src/views/AccountManagement.vue`
- `frontend/src/views/PublishCenter.vue`
- `backend/impl/channels/platform.py`
- `backend/impl/alipay/platform.py`

对应原则：

- `hn_gx` 负责保住主链路和行为语义
- `master` 负责补进 `hn_gx` 原本没有的新能力

---

## 重点文件

### 冲突后保留混合结果

- `frontend/src/views/PublishCenter.vue`
- `frontend/src/views/AccountManagement.vue`
- `backend/impl/channels/platform.py`
- `backend/impl/alipay/platform.py`

### 自动合并后仍需重点回归

- `backend/app.py`
- `backend/blueprints/channels_bp.py`
- `backend/impl/toutiao/platform.py`
- `backend/impl/tencent_video/platform.py`
- `frontend/src/components/AccountSidebar.vue`
- `frontend/src/stores/account.js`
- `backend/services/draft_merge.py`

---

## 需要重点验证

- 账号资料同步：
  - 支付宝 `stats + storage_state`
  - 视频号 `stats + storage_state`
- 发布中心：
  - 视频号活动
  - 视频号合集 / 位置
  - 自动填充标题按选中层级生效
- 定时任务：
  - 导入当前发布配置
  - 新建任务
  - 详情查看
  - SSE 实时刷新
  - 定时任务执行时视频号活动字段不丢失
- 平台稳定性：
  - 头条号发布时间校验与结果判定
  - 腾讯视频未绑定弹窗与定时发布时间选择
  - 小红书 / 视频号标签输入稳定性

---

## 当前状态

- 已完成 `master -> hn_gx` 的工作区合并
- 已解决 4 个文本冲突并保留混合方案
- 已把版本号和文档切到合并后的真实版本面
- 尚未创建最终 merge commit
- 尚未完成完整回归验证

---

## 建议后续动作

1. 先跑 `draft_merge` / `scheduled_tasks` 相关测试，确认视频号活动字段在定时任务链路中可用
2. 再跑前端构建，确认发布中心和账号管理页编译无误
3. 最后做一轮发布中心 + 定时任务的冒烟回归，再提交 merge commit
