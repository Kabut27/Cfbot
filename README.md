# AWS Control Bot — CloudFront & EC2 Manager (v5 Ultra Modular)

Bot ya Telegram ya kisasa (UI Themes, Health Score Badges, Tree Cards, Colored Progress Bars & Sparklines) ya kusimamia **AWS CloudFront**, **EC2**, **Free Tier**, na **Usalama** kwenye accounts nyingi.

## Vipengele vya v5 Ultra
- **🎨 UI Themes & Health Score:** Chagua kati ya **🎨 Classic Pro**, **💎 Minimalist**, na **🌈 High-Contrast** kwenye *Settings*, pamoja na **Health Score Badges** (`🟢 A+ Safi` hadi `🔴 D Hatari`) na timestamps (`⏱ Live / Cache`).
- **☁️ CloudFront Full Pro:** Data + 10M Requests, 7-day Sparklines, **🔮 Traffic Forecasting**, 🩺 Origin & Edge Latency Tester, 📋 1-Click Clone, Multi-Origin Failover & 🤖 Smart 5xx Auto-Failover, HTTP/3, Read Timeout, Custom Headers, Geo Restriction, WAF WebACL, ACM SSL (Issued + Pending), Purge (Quota + Custom Paths), Backup/Restore.
- **🖥 EC2 Full Pro:** Multi-Region Scan, Start Auto-DNS Prompt, **⏰ Auto-Schedule (Sleep/Wake Timer EAT)**, Multi-SG Paginated Firewall (🔒 1-Click Lock-to-CF + TCP/UDP + Presets + Custom IP/CIDR), Elastic IP, Snapshots (Create & Delete), Launch Templates, na SSM (Presets + Custom Bash).
- **🛡 Built-in Free Tier (EC2 750h & EBS 30GB) + Cost Protection:** Hesabu kamili ya saa za EC2 (750h/mwezi) + EBS (30GB) + AWS Billing Free Tier API + **💵 Live USD Bill Watchdog** + **🧹 Zombie Resource Hunter**.
- **🛡 1-Click Security Audit:** Inasaka ports hatari zilizo wazi `0.0.0.0/0` (ALL, SSH, MySQL/Postgres/Redis/RDP, Web/Panel) na CloudFront zisizolazimisha HTTPS — ikiwa na vitufe vya kurekebisha kwa mguso mmoja (`🔒 Lock → CF`, `🗑 Close`, `🔐 Force HTTPS`).
- **🔍 Quick Search (`/find`), ⚡ Bulk Actions & 📥 Inventory CSV:** Tafuta resource yoyote kwenye accounts zote, fanya Bulk Purge / Bulk EC2 Stop & Start kwa uthibitisho, au pakua ripoti ya `.csv` moja kwa moja Telegram.

## Install / Update (VPS ya Linux)
```bash
cd Cfbot-main
sudo bash install.sh              # Docker mode (default)
sudo bash install.sh --no-docker  # systemd + venv mode (nyepesi kwa RAM ndogo)
```
