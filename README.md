# Mining Stats Dashboard (V1)

![Mining Stats Dashboard](main/dashboard.jpg)

A lightweight, self-hosted mining dashboard for NerdQAxe and BitAxe Gamma miners.

This project polls miner APIs on your local network, tracks hashrate, temperatures,
shares, blocks found, weekly performance, and displays everything in a clean,
single-page web dashboard with a scrolling coin ticker.

Designed to be:
- Easy to deploy
- Hard to break
- Safe by default
- Fully local (LAN-first)

---

## ⚠️ Disclaimer

This software is provided **as-is**, without warranty of any kind.

You use this software **entirely at your own risk**.  
The author is **not responsible** for:
- Hardware damage
- Misconfiguration
- Lost profits or rewards
- Downtime
- Data loss
- Incorrect statistics
- Financial decisions made based on this dashboard

This dashboard is for **informational purposes only** and does **not** constitute
financial or investment advice.

**Do not expose this service directly to the public internet** unless you fully
understand and accept the security implications.

---

## ✨ Features

- Live miner monitoring (LAN)
- Hashrate, temperature, shares, uptime tracking
- Block found detection (including BitAxe Gamma `blockFound` API)
- Persistent block counts across restarts
- Weekly best difficulty tracking
- Miner of the Week scoring (weekly)
- Coin price + difficulty ticker
- Discord webhook notifications
- Fully self-contained single Python file
- No database required

---

## 🧱 Supported Miners

Tested with:
- NerdQAxe (NerdOS)
- BitAxe Gamma (AxeOS)

Other miners may work if they expose similar `/api/system/info` JSON fields.

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
python3 -m pip install -r requirements.txt
2. Configure the dashboard
Open MSD.py in a text editor and edit only the CONFIG section at the top:

Replace example IP addresses with your miner IPs

Replace miner names with your own labels

(Optional) Paste your Discord webhook URL

Save the file when finished.

3. Run the dashboard
python3 MSD.py
Then open your browser and go to:

http://<server-ip>:8788
Replace <server-ip> with the IP address of the machine running MSD.py.

Troubleshooting: Blocks stuck at 0

This dashboard increments Blocks when your miner exposes a block counter in /api/system/info (often blockFound).

On some NerdOS / AxeOS builds, you may need to enable “Block Found Alerts” (or similar) in the miner UI so the counter/alerts update correctly.

If your miner does not expose a block counter, blocks will remain 0 in v1.0.0.

## 💖 Support

This project is free and open source.  
If it’s helped you keep your farm happy, you can optionally send a tip:


- BTC:bc1qdtn0pwvr9yl7gyfz9l2w874l3jflcgcqxd2yry


