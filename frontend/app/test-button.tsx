import { Button } from '@/components/ui/button'

export default function ButtonTest() {
  return (
    <div className="p-8 space-y-8">
      <div>
        <h2 className="text-xl font-bold mb-4">Button Variant 测试</h2>
        
        <div className="flex flex-wrap gap-4">
          <Button variant="selected">Selected Variant (应该是蓝色)</Button>
          <Button variant="default">Default Variant</Button>
          <Button variant="outline">Outline Variant</Button>
        </div>
      </div>

      <div>
        <h2 className="text-xl font-bold mb-4">交易对选择测试</h2>
        <div className="flex flex-wrap gap-2">
          {['BTC', 'ETH', 'SOL', 'AVAX', 'ARB', 'OP'].map((sym) => (
            <Button
              key={sym}
              variant={['BTC', 'ETH'].includes(sym) ? 'selected' : 'outline'}
              size="sm"
            >
              {sym}
            </Button>
          ))}
        </div>
      </div>

      <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
        <p className="text-sm">
          <strong>预期效果：</strong><br/>
          - BTC 和 ETH 应该显示为<strong>蓝色背景、白色粗体文字、2px蓝色边框</strong><br/>
          - 其他按钮应该显示为白色背景、灰色细边框
        </p>
      </div>
    </div>
  )
}
