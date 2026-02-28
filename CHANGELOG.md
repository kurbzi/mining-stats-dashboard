Changelog

Public v1.0.3

This release rolls up a bunch of reliability + UI upgrades while keeping the project single-file, LAN-first, and hard to break.

✅ Core monitoring improvements
Stronger miner API parsing (handles more field-name variants across NerdOS/AxeOS builds).
Better “stale/offline” detection using last_seen timestamps + configurable thresholds (STALE_YELLOW_SECONDS, STALE_RED_SECONDS).
More robust difficulty formatting (SI units like K/M/G/T/P) and safer conversion logic.

🧱 Blocks & weekly tracking upgrades
Reset-safe block counting using miner-reported counters (blockFound/blocksFound/...) with protection against counter resets.
Persistent timestamps for:
last block per miner
last block across the whole farm
Weekly best difficulty tracking made reset-safe, stored separately from miner session counters.
Weekly rollover automation (Sunday night):
saves previous week’s “best difficulty”
computes Miner of the Week
resets weekly baselines
optionally restarts miners via /api/system/restart

🏆 Miner of the Week (MOTW)
Added/expanded MOTW scoring model using a weighted blend of:
blocks found that week
weekly best difficulty
hashrate vs model baseline
shares/hour vs model baseline
uptime fraction for the week
MOTW winner display in the ticker with gold highlight styling.

📊 Shares & efficiency
Added rejected share percentage computed from accepted + rejected shares.
Added power + efficiency support:
power from miner-reported watts (or derived from volts × amps when available)
efficiency shown as J/Th
Added ticker items for total power and average efficiency across all miners.

🧾 Ticker upgrades (coins + farm stats)
Configurable fiat currency + symbol (FIAT_CURRENCY, FIAT_SYMBOL).
Coin ticker shows for each coin:
price + up/down indicator
difficulty + up/down indicator
logos (CoinGecko with fallbacks)
Added farm-wide ticker stats:
total blocks
active miners / total miners
total hashrate
avg/max temperatures
total power + avg efficiency
Added maintenance countdown ticker item using MAINTENANCE_CYCLE_DAYS.

🖥️ UI & layout improvements
Miner list sorting improved:
primary: blocks
secondary: best overall difficulty
tertiary: name
Added block leader styling (gold emphasis) and clearer block display.
Responsive layout improvements for mobile and widescreen.
“Mining …” line improved using parsed stratum host/port/user where available.

🔔 Notifications
Discord webhook support for block-found alerts (optional).
Added a stacked, server-synced notification queue for in-dashboard events:
multiple events don’t overwrite each other
notifications persist and can be dismissed reliably

🗂️ Persistence & safety
Uses local JSON files with backup writes (.bak) and safe write patterns:
blocks.json
weekly_best.json
weekly_current.json
miner_of_week.json
maintenance.json
notifications.json
Config is designed so leaving optional fields blank disables features safely (no crashes, no weird half-working states).

