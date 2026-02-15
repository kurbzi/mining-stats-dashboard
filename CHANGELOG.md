# Changelog

## v1.0.2 – 2026-02-16

- Added configurable **fiat currency** support:
  - New `CURRENCY_CODE` and `CURRENCY_SYMBOL` options in the config.
  - Prices are fetched from CoinGecko using `CURRENCY_CODE` (e.g. GBP, USD, EUR).
- Added configurable **temperature thresholds**:
  - `TEMP_ORANGE_AT` and `TEMP_RED_AT` control when temps change colour in the UI.
- Generalised miner **“what am I mining?”** display:
  - Now shows generic stratum / pool information (host:port / worker), not coin-specific text.
- Improved **coin + logo handling**:
  - Uses CoinGecko for prices and logos, with fallbacks for CAS/QUAI.
- Improved **blocks & weekly stats persistence**:
  - More robust JSON loading/saving with backup files.
- Added **Miner of the Week** summary line in the ticker.
- Added **maintenance countdown** to the ticker, driven by `MAINTENANCE_CYCLE_DAYS`.
- Added **aggregate stats** ticker items:
  - Total blocks, active miners, total hashrate, total estimated power, average J/Th, average & max temps.
- UI polish:
  - Live clock (local time) in the header.
  - Responsive layout with rotating last row for large rigs.
  - “Block found” full-screen popup when any miner’s block count increases.
  - Displays correctly on PC/Laptop Browsers to show more Miners


