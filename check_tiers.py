import sys
sys.path.insert(0, 'd:/001Alpha/Hyper-Alpha-Arena/backend')
from database.connection import SessionLocal
from database.models import AIStrategy

db = SessionLocal()
strategies = db.query(AIStrategy).filter(AIStrategy.status == 'active').all()
print(f'Active strategies: {len(strategies)}')

tiers = {}
natures = {}
for s in strategies:
    t = getattr(s, 'timeframe_tier', None) or 'None'
    n = getattr(s, 'trade_nature', None) or 'None'
    tiers[t] = tiers.get(t, 0) + 1
    natures[n] = natures.get(n, 0) + 1

print(f'Tier distribution: {tiers}')
print(f'Nature distribution: {natures}')

# Check PaperPosition tier distribution
from database.models import PaperPosition
positions = db.query(PaperPosition).filter(PaperPosition.status == 'open').all()
print(f'\nOpen positions: {len(positions)}')
pos_tiers = {}
for p in positions:
    t = getattr(p, 'timeframe_tier', None) or 'None'
    pos_tiers[t] = pos_tiers.get(t, 0) + 1
print(f'Position tier distribution: {pos_tiers}')

db.close()
