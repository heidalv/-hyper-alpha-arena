# ATAS V2 前端组件

ATAS V2新一代策略中心的前端界面，提供完整的可视化管理和交互功能。

## 📦 组件结构

### 核心组件

1. **ATASV2Page.tsx** - 主入口页面
   - 账户选择器
   - 系统状态展示
   - 标签页切换（仪表板 / 交易工具）

2. **ATASV2Dashboard.tsx** - 监控仪表板
   - 总资产展示
   - 健康度评分
   - 风险监控
   - 系统指标
   - 4个标签页：概览 / 健康度 / 风险监控 / 系统指标

3. **TradingTools.tsx** - 交易工具
   - 交易风险检查工具
   - 最优仓位计算工具
   - 实时结果展示

### 辅助组件（已有）

4. **ATASLayout.tsx** - 布局容器
5. **StrategyDesigner.tsx** - 策略设计器
6. **CodeEditor.tsx** - 代码编辑器
7. **AIAssistant.tsx** - AI助手
8. **FactorBrowser.tsx** - 因子浏览器

## 🚀 功能特性

### 1. 监控仪表板

#### 概览卡片（顶部）
- **总资产** - 实时显示账户总价值和现金
- **健康度** - 综合评分（0-100）
- **风险监控** - 活跃预警数量
- **系统状态** - CPU使用率和健康状态

#### 投资组合概览
- 总价值、可用资金、持仓数量、现金比例
- 持仓明细列表（币种、数量、价格、盈亏）

#### 健康度评分
- 综合评分（整体健康状况）
- 四个子维度：
  * 表现得分 (Performance)
  * 风险得分 (Risk)
  * 稳定得分 (Stability)
  * 流动得分 (Liquidity)

#### 风险监控
- 风险预警列表
- 预警等级（Critical / Warning / Info）
- 预警分类和详细信息
- 时间戳

#### 系统指标
- CPU使用率
- 内存使用率
- 磁盘使用率
- 活跃策略数
- 持仓总数
- 今日盈亏

### 2. 交易工具

#### 交易风险检查
**输入**：
- 交易品种（如 BTC）
- 交易方向（买入 / 卖出）
- 数量
- 价格

**输出**：
- 风险检查结果（通过 / 未通过）
- 风险等级（low / medium / high）
- 违规项列表
- 警告项列表
- 详细风险指标

#### 最优仓位计算
**输入**：
- 交易品种
- 入场价格
- 计算方法：
  * 固定比例 (fixed_ratio)
  * 固定金额 (fixed_amount)
  * Kelly公式 (kelly)
  * ATR基础 (atr_based)
  * 波动率调整 (volatility_adjusted)
- 资金比例（0-1）
- 止损价格（可选）

**输出**：
- 建议数量
- 持仓价值
- 风险金额
- 止损价格（如果提供）
- 计算方法说明

## 🔌 API集成

### 使用的API端点

```typescript
// 基础路径
const API_BASE = '/api/atas/v2';

// 账户相关
GET  /account/{id}/portfolio      // 获取投资组合
GET  /account/{id}/health          // 获取健康度评分
GET  /account/{id}/risk-monitor    // 风险监控状态
GET  /account/{id}/metrics         // 系统监控指标

// 交易工具
POST /account/{id}/check-trade     // 检查交易风险
POST /account/{id}/calculate-position // 计算仓位

// 系统信息
GET  /health                       // 健康检查
GET  /info                         // 系统信息
```

## 🎨 UI设计特点

### 颜色方案
- **主色调**: 蓝色/紫色渐变
- **健康度颜色**:
  * 绿色 (≥80): 优秀
  * 黄色 (60-79): 良好
  * 红色 (<60): 需要关注
- **风险预警颜色**:
  * 红色: Critical
  * 黄色: Warning
  * 蓝色: Info

### 响应式设计
- 桌面端: 完整功能展示
- 移动端: 自适应布局（待优化）

### 交互特性
- 10秒自动刷新数据
- 手动刷新按钮
- 实时数据更新
- Toast通知提示

## 📝 使用示例

### 在路由中使用

```typescript
// main.tsx
import { ATASV2Page } from '@/components/atas-v2';

// 添加路由
{currentPage === 'atas-v2' && (
  <Suspense fallback={<div>加载中...</div>}>
    <ATASV2Page />
  </Suspense>
)}
```

### 侧边栏菜单

```typescript
// Sidebar.tsx
const mainNav: NavItem[] = [
  { 
    label: 'ATAS V2 策略中心', 
    page: 'atas-v2', 
    icon: Zap 
  },
  // ...
];
```

### 独立使用组件

```tsx
import { ATASV2Dashboard, TradingTools } from '@/components/atas-v2';

// 仪表板
<ATASV2Dashboard accountId={1} accountName="My Account" />

// 交易工具
<TradingTools accountId={1} />
```

## 🔧 开发指南

### 依赖组件
需要确保以下UI组件已安装：
- Card, CardContent, CardHeader, CardTitle
- Button
- Input
- Label
- Select
- Tabs
- Badge

### 图标库
使用 `lucide-react`:
```typescript
import { 
  Activity, TrendingUp, Shield, Heart, 
  AlertTriangle, BarChart3, Zap 
} from 'lucide-react';
```

### 状态管理
组件内部使用 `useState` 和 `useEffect` 管理状态：
- 自动数据刷新（10秒间隔）
- 错误处理和Toast通知
- Loading状态管理

### 扩展建议
1. 添加WebSocket实时数据推送
2. 集成图表可视化（Recharts）
3. 添加更多交易工具功能
4. 增强移动端体验
5. 添加数据导出功能

## 📊 数据流

```
用户操作
  ↓
前端组件 (React)
  ↓
API请求 (/api/atas/v2/*)
  ↓
后端执行器 (atas_v2_executor.py)
  ↓
数据库查询 (Account/Position/Order)
  ↓
业务逻辑处理
  ↓
返回结果
  ↓
前端展示
```

## 🎯 未来计划

- [ ] 回测功能界面
- [ ] 策略编辑器集成
- [ ] 实时图表展示
- [ ] 批量操作支持
- [ ] 高级筛选和搜索
- [ ] 数据导出功能
- [ ] 移动端优化
- [ ] 暗黑模式完善

## 📖 相关文档

- [后端API文档](../../../backend/api/atas_v2_routes.py)
- [执行器文档](../../../backend/services/atas_v2_executor.py)
- [测试文档](../../../test_atas_v2_integration.py)
