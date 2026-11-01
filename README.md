# SONIX Hub

วิเคราะห์ตลาด XAUUSD ทุก 1 ชม. แล้วเผยแพร่เป็น JSON ให้ EA (SONIX AI) ดึงไปใช้

- Python ล้วน (stdlib) **ไม่ต้อง pip install อะไร**
- 2 โหมด: **Server (API, แนะนำ)** หรือ **Static (GitHub Actions)**
- MT4/MT5 ดึงได้ทั้งคู่ — รูปแบบ JSON เหมือนกันเป๊ะ

## ทำไมใช้ `http.server` (stdlib) ทำ Server

งานนี้เป็นงาน read-mostly: build หนัก 1 ครั้ง/ชม. (LLM 10-30 วิ) แต่ EA ยิงอ่านทุกวินาที/kline
จึงไม่ต้องใช้ framework async — ใช้ `ThreadingHTTPServer` + cache ในหน่วยความจำก็พอ

| ทางเลือก | เหมาะกับ | หมายเหตุ |
|---|---|---|
| **stdlib `http.server` (ที่ทำไว้)** | งานนี้ | ไม่มี dependency, RAM ~60 MB, Docker image ~120 MB |
| FastAPI + uvicorn | โตขึ้นเป็นหลาย symbol / หลาย user / websocket | ต้อง pip install, ต้องมี worker manager |
| Flask + gunicorn | ทีมถนัด Flask | เท่า ๆ กัน แต่เพิ่ม dependency |

ถ้าวันหนึ่งต้องรับ concurrent หลักพัน หรือมี auth หลายระดับ ค่อยย้ายไป FastAPI — โครงสร้าง
`hub/feed.py` + `hub/store.py` ย้ายได้เลย ไม่ต้องรื้อ

## สถาปัตยกรรม Server

```
scheduler thread ──ทุก 5 นาที│ถ้า stale──> Feed.refresh()  ──> run.build() ──> LLM + ข้อมูลตลาด
                                              │                                │
                                              └── atomic swap ──> memory ──────┴──> public/v1/*.json
                                                                     │
EA ──GET /v1/xauusd.json────────────────────────────────────────────>│ (อ่าน cache, ไม่บล็อก)
```

- **Stale-while-revalidate**: feed หมดอายุ → ตอบของเก่าทันที + สั่ง rebuild เบื้องหลัง
- **Single-flight**: build ทีละตัวเท่านั้น ต่อให้มี request เข้ามาพร้อมกัน 1,000 ตัว
- **Atomic write**: เขียน temp file แล้ว `os.replace` → ไม่มีทางอ่านไฟล์ครึ่ง ๆ
- **Cold start**: ถ้ายังไม่เคยมี feed จะ build ให้ตอน request แรก (หรือตอบ `503` ถ้าใช้ `--no-refresh`)

## Endpoints

| Method | Path | Auth | คำอธิบาย |
|---|---|---|---|
| GET | `/v1/xauusd.json` | ✔ | feed หลักที่ EA ใช้ (ETag / 304 / gzip / Cache-Control) |
| GET | `/v1/history.json` | ✔ | ประวัติ 14 วัน (bias/score/ราคา) สำหรับ backtest |
| GET | `/healthz` | – | liveness + อายุ feed (ใช้กับ Docker/systemd/Render) |
| GET | `/v1/meta` | – | diagnostics: uptime, builds, fails, last_error |
| GET/POST | `/admin/refresh` | ✔ | สั่ง rebuild ทันที (202, จำกัด 1 ครั้ง/60 วิ, `?force=1` ข้าม limit) |
| GET | `/` | – | index ของ endpoint |

Header ที่ตอบกลับ: `ETag`, `Last-Modified`, `Cache-Control`, `X-Feed-Age`, `X-Feed-Stale`, `X-Feed-Provider`

## Auth (ไม่บังคับ)

`HUB_TOKEN` ว่าง = **ไม่ต้องใช้ token เลย** ตัด `?k=...` ออกจาก URL ทิ้งได้เลย

ใช้ token ก็ต่อเมื่อเปิดพอร์ตสู่ internet จริง ๆ เท่านั้น ถ้าเข้าเงื่อนไขข้อใดข้อหนึ่งนี้ ไม่ต้องใช้:

- MT5 อยู่เครื่องเดียวกับ server → `--host 127.0.0.1` (ไม่มีใครจากนอกเข้าได้)
- จำกัด IP ใน firewall → `.\scripts\open_firewall.ps1 -RemoteAddress <IP ของ MT5>`
- วางหลัง Cloudflare / VPN

ถ้าตั้ง token แล้ว ส่งมาได้ 3 ทาง:

```
GET /v1/xauusd.json?k=<token>                  ← ใช้กับ MT4/MT5 (WebRequest ใส่ header ไม่ได้)
X-Hub-Token: <token>
Authorization: Bearer <token>
```

ถ้า `HUB_TOKEN` ว่าง = เปิดสาธารณะ (ไม่ต้องมี `?k=`)

## รันบนเครื่อง

```
copy .env.example .env      (ใส่ GEMINI_API_KEY; HUB_TOKEN เว้นว่างได้)
run_server.bat              -> http://127.0.0.1:8080/v1/xauusd.json
run_local.bat               -> เขียนไฟล์ครั้งเดียวที่ public\v1\xauusd.json (โหมด static)
```

ใส่ token เมื่อไหร่ ค่อยต่อท้าย URL ด้วย `?k=<token>`

ตัวเลือก CLI: `python -m hub.server --host 0.0.0.0 --port 8080 --out public --token xxx --ttl 3900`
`--no-refresh` = เสิร์ฟไฟล์ที่มีอยู่เท่านั้น (ใช้ตอน debug/วางหลัง cron), `--no-seed` = ไม่ดึง history จาก `FEED_URL`

## Deploy (เลือกอย่างใดอย่างหนึ่ง)

| | **Server (VPS)** | **Static (GitHub Actions)** |
|---|---|---|
| ค่าใช้จ่าย | ฟรีถ้ามี VPS อยู่แล้ว | ฟรี 100% |
| Latency ที่ EA เห็น | ชี้ตรง tools/loopback | CDN ของ GitHub เร็วมาก |
| Auth / token | มี (หรือปิดก็ได้) | ไม่มี — ไฟล์ public |
| ตรงเวลาแค่ไหน | scheduler ในโปรเซส ทุก 5 นาที | cron ของ GitHub คลาดได้ 5-30 นาที (บางครั้งมากกว่า) |
| งานดูแล | ต้องเปิดพอร์ต/ตั้ง service | ไม่ต้อง จบ |
| /healthz, /v1/meta, /admin/refresh | มี | ไม่มี |
| ข้อจำกัด | ต้องมี IP/โดเมน | repo public, ไม่มี commit 60 วัน workflow หยุด |

**1. VPS / เครื่องตัวเอง — แนะนำถ้าอยากคุมเวลาและมี diagnostics**

```bash
docker compose up -d --build      # ต้องมี .env ก่อน
docker compose logs -f
curl "http://127.0.0.1:8080/healthz"
```

หรือ `docker run -d --restart unless-stopped -p 8080:8080 --env-file .env -v sonix:/data sonix-hub:1`
(มี `Dockerfile` + healthcheck + non-root user ให้แล้ว) — Oracle Cloud Free Tier / Contabo VPS ใช้ได้
วาง Cloudflare / nginx ทำ HTTPS + cache ด้านหน้าได้

**2. Render (ฟรี, Docker) — ใช้ทดสอบได้ ยังไม่แนะนำให้ EA ตัวจริงชี้**

มี `render.yaml` ให้: New → Blueprint → เลือก repo (`region: singapore`) → ใส่ `GEMINI_API_KEY`,
`THAILLM_API_KEY` → ได้ `HUB_TOKEN` มาเองที่ Environment

ข้อจำกัด plan ฟรีที่กระทบงานนี้ตรง ๆ

| อาการ | ผลกับ EA |
|---|---|
| sleep หลังไม่มี request ~15 นาที | scheduler ตาย, ตื่นมาต้อง cold start + build ใหม่ |
| filesystem เป็น ephemeral | `history.json` หายทุก restart/deploy (backtest ไม่ต่อเนื่อง) |
| ไม่มี persistent disk | แก้ไม่ได้ถ้าไม่จ่าย; `FEED_URL` ช่วยได้แค่ตอนย้ายมาจาก feed เดิม |
| 512 MB / 0.1 CPU | พอ (งานนี้ I/O bound) แต่ build ช้า |

ทางเลือกถ้าอยากอยู่ Render จริง: เปลี่ยนเป็น `plan: starter` (~$7/เดือน) + disk 1 GB แล้วตั้ง
`HUB_STATE_DIR=/data` — เท่านั้นก็เสถียร 24/7 แล้ว (แต่ VPS ราคาใกล้กันยืดหยุ่นกว่า)
หรืออยู่ plan ฟรีแล้วตั้ง external ping ทุก 10-14 นาที (UptimeRobot / cron-job.org) เพื่อลด cold start

**3. GitHub Actions — ง่ายสุด ไม่ต้องมี server เลย**

 workflow พร้อมใช้อยู่ที่ `.github/workflows/hub.yml` (สำเนาไว้ที่ `workflow/hub.yml` เผื่ออัปโหลดผ่านเว็บ)

1. อัปโหลด repo ขึ้น GitHub (public) — ต้องมี `.github/workflows/hub.yml`
2. Settings → Secrets and variables → Actions
   - Secrets: `GEMINI_API_KEY`, `THAILLM_API_KEY` (ตัวใดตัวหนึ่งก็พอ)
   - Variables (ไม่บังคับ): `GEMINI_MODEL`, `THAILLM_MODEL`, `LLM_ORDER`, `FEED_URL`
3. Actions → sonix-hub → **Run workflow** (รันมือครั้งแรก) → job `build` ต้องเขียว
4. URL ของ EA: `https://raw.githubusercontent.com/<user>/<repo>/gh-pages/v1/xauusd.json`
   - ไม่มี token, ไม่ต้องเปิด firewall, ไม่ต้องมี VPS
   - MT5 → Allow WebRequest → เพิ่ม `https://raw.githubusercontent.com`
5. ตั้ง Variable `FEED_URL` = URL ข้อ 4 → `history.json` ต่อเนื่องข้ามรอบ (ไม่งั้นเริ่มนับ 1 จุดทุกชั่วโมง)
   - `FEED_URL` = URL ของ feed **ตัวเราเอง** ไม่ใช่แหล่งข่าวภายนอก
   - โค้ดจะตัดชื่อไฟล์ออกแล้วดึง `.../v1/history.json` จากโดเมนเดียวกัน

ทางเลือก CDN อื่น (ช้ากว่า แต่ cache นานกว่า)

- GitHub Pages: `https://<user>.github.io/<repo>/v1/xauusd.json`
  (branch builder ของ GitHub เองอาจค้าง/error ได้ถ้า publish ถี่ — ใช้ได้เมื่อ site สถานะ `built`)
- jsDelivr: `https://cdn.jsdelivr.net/gh/<user>/<repo>@gh-pages/v1/xauusd.json`
  (cache 12 ชม. และ purge ถูก throttle ~1 ครั้ง/ชม. → ไม่เหมาะกับ feed รายชั่วโมง)

ข้อควรรู้ของโหมดนี้

- cron ของ GitHub เป็น best-effort: ปกติคลาด 5-15 นาที, ช่วงคนใช้เยอะอาจช้าเป็นชั่วโมง → EA ต้องเช็ค `ts` + `ttl` ทุกครั้งอยู่แล้ว
- raw CDN cache 5 นาที (`max-age=300`) → ค่าใหม่ช้าสุด ~5 นาทีหลัง publish ซึ่งอยู่ใน `ttl` 3900 อยู่แล้ว
- อย่ากด Run workflow รัว ๆ — เสีย Actions minutes โดยไม่จำเป็น (public repo ฟรีแต่ก็เปลืองเวลา)
- ตรวจว่า schedule ทำงานเองจริง: `gh run list --repo <user>/<repo> --event schedule`
  หรือ Actions → กรอง event = `schedule` — ครั้งแรกหลังสร้าง repo อาจรอถึง ~1 ชั่วโมง
- ต้องการเวลาตรงเป๊ะ (cron GitHub ไม่มี SLA) → ใช้โหมด server บน VPS ที่มี scheduler ในโปรเซส
- ไม่มี commit 60 วัน GitHub จะปิด scheduled workflow → workflow มี job `keepalive` commit เดือนละครั้งให้แล้ว
- ไม่มี `/healthz` ให้เช็ค → ดูสถานะได้ที่ `status.errors` ในไฟล์ JSON และหน้า Actions
- workflow มี step `Validate feed` แล้ว — ไฟล์เสียจะไม่ถูก publish ทับของดี
- ต้องมี Environment `github-pages` (สร้างอัตโนมัติตอนเปิด Pages) และ Deployment branches ต้องอนุญาต `main`
  ถ้า key อยู่ใน Environment secrets แต่ job ไม่ได้ประกาศ `environment:` จะเห็นเป็น "GEMINI_API_KEY not set"

## Windows VPS: URL ที่ EA ใช้

เลือกตามว่า MT5 รันที่ไหน

**กรณี A — MT5 อยู่บน VPS เครื่องเดียวกับ server (แนะนำ, ไม่ต้องใช้ token)**

```
http://127.0.0.1:8080/v1/xauusd.json
```

ไม่ต้องเปิด firewall, ไม่ต้องมี public IP, ไม่มีใครยิงจากนอกได้, เร็วสุด (loopback)

```powershell
python -m hub.server --host 127.0.0.1 --port 8080 --out public
```

**กรณี B — MT5 อยู่อีกเครื่อง (คนละ VPS / บ้าน / broker VPS)**

```
http://<PUBLIC_IP>:8080/v1/xauusd.json
เช่น  http://203.0.113.10:8080/v1/xauusd.json
```

เปิด firewall แบบจำกัด IP ของ MT5 → ไม่ต้องใช้ token เช่นกัน (แนะนำกว่าการเปิดกว้าง + token)

```powershell
.\scripts\open_firewall.ps1 -Port 8080 -RemoteAddress 203.0.113.50
```

ถ้าจำเป็นต้องเปิดให้ทุก IP จริง ๆ ค่อยตั้ง `HUB_TOKEN` แล้วต่อท้าย URL ด้วย `?k=<token>`

- `--host 0.0.0.0` (ค่าเริ่มต้น) ถึงจะรับจากภายนอกได้ — bind `127.0.0.1` = รับเฉพาะในเครื่อง
- ตั้ง static IP / จอง IP กับผู้ให้บริการ VPS ไว้ ไม่งั้น IP เปลี่ยนแล้ว EA พัง
- MT5 list allowlist ใส่ `http://203.0.113.10` (มี `:8080` ด้วยถ้าไดอะล็อกยอมรับ)
- **http ธรรมดา token วิ่งแบบอ่านได้** → ถ้าไม่ใช่ LAN เดียวกัน ให้ทำ HTTPS (ด้านล่าง) หรือไม่ก็จำกัด IP ใน firewall

**HTTPS บน Windows (มีโดเมน)** — ใช้ Caddy หน้าพอร์ต 8080 สองบรรทัด ไม่ต้องจัดการ certificate เอง:

```
hub.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

แล้ว EA ใช้ `https://hub.example.com/v1/xauusd.json?k=<HUB_TOKEN>` (ไม่ต้องใส่พอร์ต)
ไม่มีโดเมน → ทางเลือกคือ Cloudflare Tunnel หรือ WireGuard/Tailscale ให้ MT5 กับ VPS คุยกันในวง VPN แล้วใช้ IP ภายใน

**ให้รันตลอดหลัง reboot** — Task Scheduler (Trigger: At startup, Run whether user is logged on or not,
Action: `python.exe` + `-m hub.server --port 8080 --out C:\SonixHub\public`) หรือใช้ NSSM ทำเป็น service:

```powershell
nssm install SonixHub "C:\Python312\python.exe" "-m hub.server --port 8080 --out C:\SonixHub\public"
nssm set SonixHub AppDirectory C:\SonixHub
nssm set SonixHub AppEnvironmentExtra "HUB_TOKEN=s3cret" "GEMINI_API_KEY=..."
nssm start SonixHub
```

เช็คจากนอกเครื่อง: `curl "http://<PUBLIC_IP>:8080/healthz"` ต้องได้ `"ok":true` (build แรกอาจใช้เวลา 1-2 นาที)

## ฝั่ง EA (MQL5)

Tools → Options → Expert Advisors → ✔ Allow WebRequest → เพิ่ม host ของคุณ

```mql5
input string Hub_URL   = "https://YOUR_HOST/v1/xauusd.json";
input string Hub_Token = "";           // ค่า HUB_TOKEN บน server
input int    Hub_Timeout = 5000;       // ms

bool HubFetch(string &json)
{
   string url = Hub_URL + (StringLen(Hub_Token) > 0 ? "?k=" + Hub_Token : "");
   char   data[], result[];
   string headers = "";
   ResetLastError();
   int code = WebRequest("GET", url, headers, Hub_Timeout, data, result, headers);
   if(code != 200) { Print("Hub error ", code, " last ", GetLastError()); return false; }
   json = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return true;
}
```

EA อ่านค่าแรกที่เจอของแต่ละ key: `ver, ts, ttl, bias, score, confidence, event_risk, next_event,
next_event_ts, next_event_impact, news_sentiment, summary_en, summary_th, provider`
เช็คก่อนใช้ทุกครั้ง: `ttl` (วินาที) และ `ts` — ถ้า `now - ts > ttl` ให้ลด size/หยุดเทรด
ส่วนรายละเอียด: `drivers, components, market, flow, cot, calendar, headlines, status`

## ข้อมูลที่ใช้ (ฟรีทั้งหมด)

| โมดูล | แหล่ง | หมายเหตุ |
|---|---|---|
| `prices.py` | Yahoo Finance chart API (GC=F, DXY, ^TNX, ^VIX, SI=F, GLD) | แหล่งเดียวกับ yfinance, ไม่เป็นทางการ |
| `econ.py` | ForexFactory weekly JSON (USD High/Medium) | feed ไม่เป็นทางการ |
| `news.py` | Google News RSS, Fed, FXStreet, BLS, MarketWatch | แก้รายการได้ด้วย env `RSS_FEEDS` (คั่นด้วย `\|`) |
| `flow.py` | Binance PAXG/USDT order book + taker buy/sell | ตัวแทน order flow ของทอง (PAXG = ทอง 1 oz) |
| `cot.py` | CFTC COT (Managed Money, COMEX gold 088691) | รายสัปดาห์ |
| `llm.py` | Gemini → ThaiLLM (fallback) | ให้คะแนนข่าว + สรุป EN/TH |
| `score.py` | กฎคำนวณ (ไม่ใช้ LLM) | bias −100..+100, confidence |

## ต้องใส่อะไรบ้าง (ขั้นต่ำ)

**GitHub Actions (repo → Settings → Secrets and variables → Actions)**

| ชนิด | ชื่อ | จำเป็น? | ถ้าไม่ใส่ |
|---|---|---|---|
| Secret | `GEMINI_API_KEY` | ควรใส่ | ยังรันได้ แต่ข่าวไม่ถูกให้คะแนน (`news=0`, `provider="none"`, `degraded=true`) |
| Secret | `THAILLM_API_KEY` | ไม่ | ใช้เป็น fallback ของ Gemini |
| Variable | `FEED_URL` | ควรใส่ | `history.json` เริ่มนับ 1 จุดใหม่ทุกชั่วโมง |
| Variable | `RSS_FEEDS` | ไม่ | ใช้รายการข่าวในตัว (บางแหล่ง 403 จาก IP ของ GitHub) |
| Variable | `GEMINI_MODEL`, `THAILLM_MODEL`, `THAILLM_BASE`, `LLM_ORDER` | ไม่ | ใช้ค่า default ใน `config.py` |

ตั้งที่ `https://github.com/<user>/<repo>/settings/secrets/actions`
Secrets = ค่าลับ (ถูกปิดใน log) / Variables = ค่าธรรมดา (ใครอ่าน repo ก็เห็น)

> ใส่ key ที่ **Environment** `github-pages` แทน Repository secrets ก็ได้
> แต่ job ต้องประกาศ `environment: github-pages` (มีให้แล้วใน workflow) ไม่งั้นจะเห็นเป็น "not set"
> และต้องเพิ่ม `main` ใน Environment → Deployment branches ด้วย ไม่งั้น run จะถูกปฏิเสธ

**Server (.env บน VPS / Docker)**

| ตัวแปร | จำเป็น? | หมายเหตุ |
|---|---|---|
| `GEMINI_API_KEY` (หรือ `THAILLM_API_KEY`) | ควรใส่ | เหมือนข้างบน |
| `HUB_TOKEN` | ไม่ | เว้นว่าง = URL ไม่ต้องมี `?k=` |
| `HOST` / `PORT` | ไม่ | default `0.0.0.0` / `8080` |
| `HUB_STATE_DIR` | ไม่ | default `public` (ใน Docker ตั้ง `/data`) |
| `READY_TIMEOUT`, `LOG_LEVEL` | ไม่ | `240`, `INFO` |

## ตัวแปรสภาพแวดล้อม

| ตัวแปร | ค่าเริ่มต้น | ใช้ทำอะไร |
|---|---|---|
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | LLM หลัก |
| `THAILLM_API_KEY` / `THAILLM_MODEL` / `THAILLM_BASE` | – | LLM สำรอง |
| `LLM_ORDER` | `gemini,thaillm` | ลำดับ fallback |
| `TTL_SEC` | `3900` | อายุ feed (วินาที) ก่อนถือว่า stale |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | ที่อยู่ server |
| `HUB_TOKEN` | ว่าง | token ของ EA |
| `HUB_STATE_DIR` | `public` | ที่เก็บ `v1/*.json` |
| `READY_TIMEOUT` | `240` | วินาทีที่ request แรกยอมรอ build |
| `FEED_URL` | ว่าง | **URL ของ feed ตัวเองรอบก่อน** (ไม่ใช่แหล่งข่าว) — ใช้กู้ `history.json` ตอนรันบนเครื่องใหม่/Actions ที่ไม่มีไฟล์เดิม |
| `RSS_FEEDS` / `CALENDAR_URL` / `FLOW_SYMBOL` / `COT_URL` | ตาม `config.py` | override แหล่งข้อมูล |

## ทดสอบ

```
python -m tests.test_offline   # pipeline เต็ม (fake HTTP) -> write JSON
python -m tests.test_server    # HTTP: auth, 304, gzip, history, refresh, CORS
bench.bat                      # เทียบ Gemini กับ ThaiLLM 3 รุ่น -> bench_result.json
```

## ปฏิบัติการ (ops)

- ดูสถานะเร็ว: `curl /healthz` — ถ้า `stale: true` นานผิดปกติ ให้ดู `last_error` ที่ `/v1/meta`
- ถ้า Gemini ตอบ 404/503 หรือ RSS แหล่งใดพัง: Actions → **probe-feeds** → Run workflow
  จะไล่รายชื่อโมเดลที่เรียกได้จริง + ทดสอบยิง `generateContent` และเช็ค feed ทุกแหล่งจาก IP ของ runner
- rebuild ด้วยมือ: `curl -X POST "http://HOST:8080/admin/refresh?k=TOKEN"`
- log ไม่พิมพ์ query string (กัน token หลุด) และตัด `?` ออกทุกบรรทัด
- ไฟล์ state ถูกเขียนทุกชั่วโมง → backup แค่ `v1/history.json` พอ (กู้ประวัติ backtest)
- GitHub ปิด scheduled workflow อัตโนมัติถ้า repo ไม่มีความเคลื่อนไหว 60 วัน (โหมด static เท่านั้น)
