# 测试 order_result 的字段

# 模拟实际的 order_result
order_result = {
    'status': 'success',
    'order_id': '881398757725',
    'symbol': 'BTC/USDT',
    'side': 'buy',
    'amount': 0.00213,
    'filled': 0.002,  # 可能是字符串 '0.002'？
    'price': 91858.8,
}

print("测试1: filled 是数字 0.002")
executed_qty = order_result.get("filled_qty") or order_result.get("filled")
executed_price = order_result.get("price")
print(f"executed_qty = {executed_qty} (type: {type(executed_qty).__name__})")
print(f"executed_price = {executed_price} (type: {type(executed_price).__name__})")
print(f"bool(executed_qty) = {bool(executed_qty)}")
print(f"bool(executed_price) = {bool(executed_price)}")
print(f"if executed_price and executed_qty: {executed_price and executed_qty}")

print("\n测试2: filled 是字符串 '0.002'")
order_result['filled'] = '0.002'  # 字符串
executed_qty = order_result.get("filled_qty") or order_result.get("filled")
print(f"executed_qty = {executed_qty} (type: {type(executed_qty).__name__})")
print(f"bool(executed_qty) = {bool(executed_qty)}")
print(f"if executed_price and executed_qty: {executed_price and executed_qty}")
