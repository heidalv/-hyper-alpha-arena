$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:8000'
$paths = @(
  '/api/health',
  '/api/paper/summary',
  '/api/paper/positions',
  '/api/paper/orders',
  '/api/arbitrage-paper/accounts',
  '/api/trading/lanes',
  '/api/trading/portfolio/summary',
  '/api/trading/positions',
  '/api/trading/opportunities?limit=5',
  '/api/trading/risk/summary',
  '/api/trading/risk/breakers',
  '/api/trading/config/fees',
  '/api/trading/config/lanes/mm_asterdex',
  '/api/trading/lanes/mm_asterdex/shadow',
  '/api/trading/lanes/mm_asterdex/shadow/report?days=7',
  '/api/trading/portfolio/attribution?days=7',
  '/api/trading/portfolio/series?days=7'
)
foreach ($p in $paths) {
  try {
    $r = Invoke-WebRequest "$base$p" -TimeoutSec 25 -UseBasicParsing
    "{0,-58} {1}  {2} bytes" -f $p, $r.StatusCode, $r.Content.Length
  } catch {
    "{0,-58} ERR {1}" -f $p, $_.Exception.Message.Substring(0, [Math]::Min(80, $_.Exception.Message.Length))
  }
}
