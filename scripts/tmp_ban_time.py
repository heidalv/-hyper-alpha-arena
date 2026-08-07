from datetime import datetime, timezone, timedelta
ban_ms = 1785830397092
ban_dt = datetime.fromtimestamp(ban_ms / 1000, tz=timezone.utc)
print("ban until UTC :", ban_dt.isoformat())
print("ban until +8 :", (ban_dt + timedelta(hours=8)).isoformat())
print("now UTC      :", datetime.now(timezone.utc).isoformat())
print("剩余(秒)      :", ban_ms / 1000 - datetime.now(timezone.utc).timestamp())
