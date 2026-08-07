import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useTranslation } from 'react-i18next'
import { useExchange } from '@/contexts/ExchangeContext'

export default function UserGuide() {
  useTranslation()
  const { currentExchange, exchanges } = useExchange()
  const currentExchangeInfo = exchanges.find(ex => ex.id === currentExchange)

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div>
        <h1 className="text-3xl font-bold mb-2">使用指南</h1>
        <p className="text-muted-foreground">
          欢迎使用 Herdalv Alpha Arena - AI 驱动的加密货币交易平台
        </p>
      </div>

      {/* 快速开始 */}
      <Card>
        <CardHeader>
          <CardTitle>🚀 快速开始</CardTitle>
          <CardDescription>三步开启 AI 交易之旅</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <h3 className="font-semibold text-lg">1. 创建 AI 交易员</h3>
            <p className="text-sm text-muted-foreground">
              前往"AI 交易员管理"页面，创建您的第一个 AI 交易员账户。可以选择不同的大模型（GPT、Claude、Deepseek 等）。
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold text-lg">2. 配置交易所</h3>
            <p className="text-sm text-muted-foreground">
              当前交易所：<span className="font-medium">{currentExchangeInfo?.displayName}</span>
            </p>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1 ml-4">
              {currentExchange === 'hyperliquid' && (
                <>
                  <li>前往"Hyperliquid 交易"页面</li>
                  <li>配置钱包地址或 API 密钥</li>
                  <li>选择主网或测试网环境</li>
                </>
              )}
              {currentExchange === 'binance' && (
                <>
                  <li>前往"币安交易"页面</li>
                  <li>配置 API Key 和 Secret</li>
                  <li>选择合约市场（现货/永续）</li>
                  <li>设置最大杠杆倍数</li>
                </>
              )}
            </ul>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold text-lg">3. 启动 AI 交易</h3>
            <p className="text-sm text-muted-foreground">
              配置完成后，AI 交易员会根据市场行情和策略配置自动进行交易决策。
            </p>
          </div>
        </CardContent>
      </Card>

      {/* 核心功能 */}
      <Card>
        <CardHeader>
          <CardTitle>✨ 核心功能</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <h3 className="font-semibold">📊 投资组合</h3>
              <p className="text-sm text-muted-foreground">
                实时查看所有 AI 交易员的资产状况、持仓、订单和交易历史。
              </p>
            </div>

            <div className="space-y-2">
              <h3 className="font-semibold">🤖 AI 交易员</h3>
              <p className="text-sm text-muted-foreground">
                管理多个 AI 交易员，每个可配置不同的大模型和交易策略。
              </p>
            </div>

            <div className="space-y-2">
              <h3 className="font-semibold">📝 提示词模板</h3>
              <p className="text-sm text-muted-foreground">
                自定义 AI 的决策提示词，调整交易风格和风险偏好。
              </p>
            </div>

            <div className="space-y-2">
              <h3 className="font-semibold">📡 信号系统</h3>
              <p className="text-sm text-muted-foreground">
                创建技术指标信号，触发 AI 交易策略。支持 RSI、MACD、布林带等。
              </p>
            </div>

            <div className="space-y-2">
              <h3 className="font-semibold">📈 K线图表</h3>
              <p className="text-sm text-muted-foreground">
                专业的 K线图分析工具，查看历史价格和技术指标。
              </p>
            </div>

            <div className="space-y-2">
              <h3 className="font-semibold">⚙️ 策略配置</h3>
              <p className="text-sm text-muted-foreground">
                配置交易对监控列表、触发间隔、信号池等策略参数。
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 交易所配置指南 */}
      <Card>
        <CardHeader>
          <CardTitle>🔧 交易所配置</CardTitle>
          <CardDescription>根据使用的交易所进行配置</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3">
            <h3 className="font-semibold text-lg">Hyperliquid 配置</h3>
            <div className="space-y-2 text-sm">
              <p><strong>测试网模式：</strong></p>
              <ul className="list-disc list-inside ml-4 space-y-1 text-muted-foreground">
                <li>免费测试，无需真实资金</li>
                <li>前往 <a href="https://app.hyperliquid-testnet.xyz" target="_blank" rel="noopener noreferrer" className="text-primary underline">Hyperliquid 测试网</a> 领取测试币</li>
                <li>复制钱包地址到平台配置</li>
              </ul>

              <p className="mt-3"><strong>主网模式：</strong></p>
              <ul className="list-disc list-inside ml-4 space-y-1 text-muted-foreground">
                <li>需要创建 Hyperliquid API 密钥</li>
                <li>在 Hyperliquid 官网创建 Builder 密钥</li>
                <li>配置 API Secret 并授权平台进行交易</li>
              </ul>
            </div>
          </div>

          <div className="space-y-3">
            <h3 className="font-semibold text-lg">币安配置</h3>
            <div className="space-y-2 text-sm">
              <p><strong>API 密钥创建：</strong></p>
              <ul className="list-disc list-inside ml-4 space-y-1 text-muted-foreground">
                <li>登录币安账户，进入 API 管理</li>
                <li>创建新的 API Key，启用"合约交易"权限</li>
                <li>设置 IP 白名单（推荐）提高安全性</li>
                <li>复制 API Key 和 Secret 到平台配置</li>
              </ul>

              <p className="mt-3"><strong>交易对格式：</strong></p>
              <ul className="list-disc list-inside ml-4 space-y-1 text-muted-foreground">
                <li>币安使用 USDT 永续合约格式（如 BTCUSDT、ETHUSDT）</li>
                <li>在"策略配置 → 交易对监控"中选择要监控的交易对</li>
                <li>支持自定义添加任意 USDT 交易对</li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 策略配置 */}
      <Card>
        <CardHeader>
          <CardTitle>⚡ 策略配置说明</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <h3 className="font-semibold">触发模式</h3>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1 ml-4">
              <li><strong>定时触发：</strong>按固定时间间隔（如 150 秒）自动执行 AI 决策</li>
              <li><strong>信号触发：</strong>绑定信号池，当技术指标满足条件时触发交易</li>
              <li><strong>混合模式：</strong>同时使用信号触发和定时兜底</li>
            </ul>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">交易对监控</h3>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1 ml-4">
              <li>选择 AI 需要关注的交易对</li>
              <li>Hyperliquid 最多 10 个，币安最多 20 个</li>
              <li>支持自定义添加交易对</li>
              <li>K线图会自动同步显示监控列表</li>
            </ul>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">全局配置</h3>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1 ml-4">
              <li><strong>采样间隔：</strong>价格数据采集频率（默认 18 秒）</li>
              <li>影响所有 AI 交易员的数据获取节奏</li>
            </ul>
          </div>
        </CardContent>
      </Card>

      {/* 常见问题 */}
      <Card>
        <CardHeader>
          <CardTitle>❓ 常见问题</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <h3 className="font-semibold">Q: AI 交易员会一直自动交易吗？</h3>
            <p className="text-sm text-muted-foreground">
              A: 只有当策略状态为"启用"时才会自动交易。您可以随时在策略配置中禁用自动交易。
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Q: 如何查看 AI 的交易决策？</h3>
            <p className="text-sm text-muted-foreground">
              A: 在投资组合页面的"AI 决策"标签页可以查看所有历史决策记录，包括推理过程。
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Q: 可以同时使用多个交易所吗？</h3>
            <p className="text-sm text-muted-foreground">
              A: 可以！通过顶部导航栏切换不同的交易所，每个交易所独立配置和管理。
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Q: 提示词模板如何使用？</h3>
            <p className="text-sm text-muted-foreground">
              A: 在"提示词模板"页面可以自定义 AI 的决策提示词。支持动态变量（如当前价格、持仓等），用 {'{variable}'} 格式引用。
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Q: 信号系统是什么？</h3>
            <p className="text-sm text-muted-foreground">
              A: 信号系统可以创建技术指标条件（如 RSI &lt; 30），当条件满足时触发 AI 进行交易决策，实现更精准的入场时机。
            </p>
          </div>
        </CardContent>
      </Card>

      {/* 安全提示 */}
      <Card className="border-yellow-500/50 bg-yellow-500/5">
        <CardHeader>
          <CardTitle className="text-yellow-600 dark:text-yellow-500">🔒 安全提示</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <ul className="list-disc list-inside space-y-1 text-muted-foreground">
            <li>建议先在测试网环境熟悉功能后再使用主网</li>
            <li>API 密钥请妥善保管，不要泄露给他人</li>
            <li>建议为 API 设置 IP 白名单限制</li>
            <li>定期检查 AI 交易决策和账户资产变化</li>
            <li>合理设置仓位和杠杆，控制风险</li>
            <li>本地部署时请确保服务器安全</li>
          </ul>
        </CardContent>
      </Card>

      {/* 技术支持 */}
      <Card>
        <CardHeader>
          <CardTitle>💬 获取帮助</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>如果您在使用过程中遇到问题：</p>
          <ul className="list-disc list-inside space-y-1 text-muted-foreground ml-4">
            <li>查看"系统日志"页面了解运行状态</li>
            <li>检查交易所 API 配置是否正确</li>
            <li>确认网络连接正常</li>
            <li>查看 Docker 容器日志排查问题</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
