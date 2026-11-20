# CloudFront & EC2 Telegram Manager

Bot ya Telegram (menyu ya buttons, paneli moja) ya kusimamia **AWS CloudFront** na **EC2** kwenye accounts nyingi.

## Vipengele
**CloudFront** – create, list, enable/disable, purge, delete (inasubiri *Deployed*), backup/restore (10 za mwisho kwa kila distribution),
origins nyingi (domain, HTTP/HTTPS port, origin protocol), Settings (viewer protocol, cache policy, HTTP/3, compression, IPv6, price class),
custom domain + ACM (omba cheti, weka alias), analytics (GB, requests, 4xx/5xx), usage dhidi ya 1TB.

**EC2** – region switcher + scan ya regions zote, start/stop/reboot/terminate (uthibitisho mara 2), launch kutoka Launch Template,
firewall (open/close port, *CloudFront only*), Elastic IP, metrics (CPU/Network), EBS snapshot, SSM commands zilizoidhinishwa (restart x-ui/nginx, df, free…).

**🔗 EC2 ↔ CloudFront** – *Set as CF Origin* kwenye EC2, au *Pick from EC2* kwenye origin: DNS inabadilika bila kunakili.

**Arifa** – Deployed imekamilika, cheti ISSUED, EC2 imeanza/kuzima, usage 80%/95%, 5xx ya juu (backend imekufa), CPU > 90%.

## Install (VPS yoyote ya Linux)
```bash
git clone <repo-url> cfbot && cd cfbot
sudo bash install.sh            # inakuuliza bot token tu; inaweka Docker ikiwa haipo
```
Kisha tuma `/start <code>` kwenye bot (code inaonyeshwa mwishoni) – wewe ndiye mmiliki.
`--no-docker` (systemd+venv, repo iwe `/opt/cfbot`) · `--token XXX` (bila maswali) · `--uninstall`.

## Update
```bash
git pull && sudo bash install.sh     # data na config vinabaki; data ya toleo la kwanza inahamishwa yenyewe
```

## AWS IAM
Kwa kila account tengeneza IAM user na keys, kisha ambatisha:
- `iam/cloudfront.json` – CloudFront, ACM, CloudWatch (inatosha kwa CDN)
- `iam/ec2.json` – ongeza hii ukitaka EC2 (ondoa `TerminateInstances` / SSM usipotaka)

Kwenye bot: **Accounts → Add account → 🔎 Check permissions**.

## Muundo
```
src/main.py            entry + jobs
src/config.py          env & constants
src/storage.py         SQLite (accounts zilizosimbwa, tokens, watches, settings) + lock
src/core/              router (routes/flows), ui (panel, cards), aws (clients), errors
src/services/          cloudfront.py · ec2.py · metrics.py   (AWS logic pekee)
src/handlers/          home · accounts · cf_ui · domains · analytics · ec2_ui · ec2_tools · link · jobs
tests/                 offline end-to-end tests (fake AWS + fake Telegram)
iam/                   IAM policies
```
Kuongeza kipengele: andika `@route("jina")` kwenye handler mpya na uiagize kwenye `handlers/__init__.py`.

## Majaribio
```bash
pip install -r requirements.txt && python -m unittest discover -s tests -v
```

## Usalama & gharama
- Keys zimesimbwa (Fernet) kwenye `data/cfbot.db`; ufunguo ni `SECRET_KEY` (`.env`). **Ibackup `.env` na `data/`.**
- Wamiliki tu wanajibiwa. SSM inaruhusu amri za presets tu (hakuna shell huru).
- CloudWatch inatumia `GetMetricStatistics` (ndani ya free tier ya API). Public IPv4/Elastic IP hulipiwa kwa saa.
- CloudFront prefix list inahesabiwa ~55 rules kwenye security group (default limit 60).
