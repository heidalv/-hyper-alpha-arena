/**
 * ATAS V2 因子浏览器
 * 
 * 提供因子查询、预览和性能监控功能
 */
import { useState, useEffect }from 'react'
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import {
  Search,
  Filter,
  TrendingUp,
  Activity,
  BarChart3,
  RefreshCw,
  Eye,
  Plus
} from 'lucide-react';

interface Factor {
  factor_id: string;
  name: string;
  display_name: string;
  description: string;
  category: string;
  subcategory: string;
  lookback_period: number;
  required_fields: string[];
}

interface FactorBrowserProps {
  onSelectFactor?: (factorId: string) => void;
  onAddFactor?: (factorId: string) => void;
}

export const FactorBrowser: React.FC<FactorBrowserProps> = ({
  onSelectFactor,
  onAddFactor
}) => {
  const [factors, setFactors] = useState<Factor[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [selectedFactor, setSelectedFactor] = useState<Factor | null>(null);

  // 加载因子列表
  useEffect(() => {
    loadFactors();
  }, []);

  const loadFactors = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/atas/factors');
      const data = await response.json();
      
      // 转换为数组
      const factorArray = Object.entries(data.factors || {}).map(([id, info]: [string, any]) => ({
        factor_id: id,
        ...info
      }));
      
      setFactors(factorArray);
    } catch (error) {
      console.error('Failed to load factors:', error);
    } finally {
      setLoading(false);
    }
  };

  // 过滤因子
  const filteredFactors = factors.filter(factor => {
    const matchesSearch = 
      factor.display_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      factor.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      factor.factor_id.toLowerCase().includes(searchTerm.toLowerCase());
    
    const matchesCategory = 
      selectedCategory === 'all' || factor.category === selectedCategory;
    
    return matchesSearch && matchesCategory;
  });

  // 按分类分组
  const categories = Array.from(new Set(factors.map(f => f.category)));
  const categoryCount = (cat: string) => factors.filter(f => f.category === cat).length;

  const handleSelectFactor = (factor: Factor) => {
    setSelectedFactor(factor);
    onSelectFactor?.(factor.factor_id);
  };

  return (
    <div className="flex h-full gap-4">
      {/* 左侧：因子列表 */}
      <div className="w-2/3 flex flex-col">
        <Card className="flex-1 flex flex-col">
          {/* 搜索栏 */}
          <div className="p-4 border-b border-gray-200 dark:border-gray-700">
            <div className="flex items-center gap-2 mb-4">
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="搜索因子..."
                  className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg"
                />
              </div>
              <Button variant="ghost" size="sm" onClick={loadFactors}>
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>

            {/* 分类过滤 */}
            <div className="flex items-center gap-2 flex-wrap">
              <Button
                variant={selectedCategory === 'all' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setSelectedCategory('all')}
              >
                全部 ({factors.length})
              </Button>
              {categories.map(cat => (
                <Button
                  key={cat}
                  variant={selectedCategory === cat ? 'default' : 'ghost'}
                  size="sm"
                  onClick={() => setSelectedCategory(cat)}
                >
                  {cat} ({categoryCount(cat)})
                </Button>
              ))}
            </div>
          </div>

          {/* 因子列表 */}
          <div className="flex-1 overflow-y-auto p-4">
            {loading ? (
              <div className="flex items-center justify-center h-full text-gray-400">
                <RefreshCw className="w-8 h-8 animate-spin" />
              </div>
            ) : filteredFactors.length === 0 ? (
              <div className="flex items-center justify-center h-full text-gray-400">
                <div className="text-center">
                  <Filter className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>未找到匹配的因子</p>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                {filteredFactors.map(factor => (
                  <div
                    key={factor.factor_id}
                    className={`p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                      selectedFactor?.factor_id === factor.factor_id
                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                        : 'border-gray-200 dark:border-gray-700 hover:border-blue-300'
                    }`}
                    onClick={() => handleSelectFactor(factor)}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <h4 className="font-medium">{factor.display_name}</h4>
                          <span className="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-800">
                            {factor.category}
                          </span>
                        </div>
                        <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
                          {factor.description}
                        </p>
                        <div className="flex items-center gap-4 text-xs text-gray-500">
                          <span>ID: {factor.factor_id}</span>
                          <span>周期: {factor.lookback_period}天</span>
                          <span>字段: {factor.required_fields.join(', ')}</span>
                        </div>
                      </div>
                      <div className="flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            onAddFactor?.(factor.factor_id);
                          }}
                        >
                          <Plus className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* 右侧：因子详情和预览 */}
      <div className="w-1/3 flex flex-col gap-4">
        {selectedFactor ? (
          <>
            <Card className="p-4">
              <h3 className="font-semibold mb-4">因子详情</h3>
              <div className="space-y-3">
                <div>
                  <label className="text-sm text-gray-600 dark:text-gray-400">显示名称</label>
                  <p className="font-medium">{selectedFactor.display_name}</p>
                </div>
                <div>
                  <label className="text-sm text-gray-600 dark:text-gray-400">因子ID</label>
                  <p className="font-mono text-sm">{selectedFactor.factor_id}</p>
                </div>
                <div>
                  <label className="text-sm text-gray-600 dark:text-gray-400">分类</label>
                  <p>{selectedFactor.category} / {selectedFactor.subcategory}</p>
                </div>
                <div>
                  <label className="text-sm text-gray-600 dark:text-gray-400">回溯周期</label>
                  <p>{selectedFactor.lookback_period} 天</p>
                </div>
                <div>
                  <label className="text-sm text-gray-600 dark:text-gray-400">必需字段</label>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {selectedFactor.required_fields.map(field => (
                      <span
                        key={field}
                        className="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-800"
                      >
                        {field}
                      </span>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-sm text-gray-600 dark:text-gray-400">描述</label>
                  <p className="text-sm">{selectedFactor.description}</p>
                </div>
              </div>
            </Card>

            <Card className="p-4">
              <h3 className="font-semibold mb-4">实时预览</h3>
              <div className="space-y-2">
                <Button variant="outline" className="w-full gap-2">
                  <Activity className="w-4 h-4" />
                  计算当前值
                </Button>
                <Button variant="outline" className="w-full gap-2">
                  <BarChart3 className="w-4 h-4" />
                  查看历史图表
                </Button>
                <Button variant="outline" className="w-full gap-2">
                  <TrendingUp className="w-4 h-4" />
                  性能分析
                </Button>
              </div>
            </Card>
          </>
        ) : (
          <Card className="p-4 flex items-center justify-center h-full">
            <div className="text-center text-gray-400">
              <Eye className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>选择一个因子查看详情</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};

export default FactorBrowser;
