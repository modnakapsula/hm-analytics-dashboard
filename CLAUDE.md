# H&M Analytics Dashboard — Project Context for Claude

## Šta je ovaj projekat

Flask web aplikacija koja omogućava analitiku H&M fashion dataseta putem prirodnog jezika.
Korisnik upiše pitanje → Gemini generiše SQL → BigQuery izvršava → Gemini interpretira rezultate → Plotly prikazuje grafikon.

## Stack

- **Backend:** Flask (Python), CB conda okruženje (`C:\Users\Biljana\.conda\envs\CB`)
- **AI:** Google Gemini 2.0 Flash (`google-genai` SDK — novi, ne `google-generativeai` koji je deprecated)
- **Baza:** Google BigQuery (kolonska, star schema)
- **Slike:** Google Cloud Storage (`hm-dataset-bucket`)
- **Frontend:** Vanilla HTML/CSS/JS + Plotly
- **Keš:** JSON fajlovi u `cache/` folderu

## Pokretanje lokalno

```powershell
cd "C:\Python\hm-analytics-dashboard"
& "C:\Users\Biljana\.conda\envs\CB\python.exe" app.py
# → http://localhost:5000
```

Flask se pokreće sa CB conda Python-om, ne sistemskim `python` (koji nije na PATH-u).

## Struktura fajlova

```
hm-analytics-dashboard/
├── app.py                  ← Flask backend (Gemini + BigQuery + Plotly + keš)
├── upload_images.py        ← Skripta za upload slika na GCS (jednokratno)
├── templates/
│   └── index.html          ← Frontend (Početna / Novi upit / Istorija / O projektu)
├── cache/                  ← JSON keš fajlovi (NIJE u gitu)
├── requirements.txt
├── Procfile                ← Railway deploy (gunicorn)
├── .env                    ← Lokalni env (NIJE u gitu)
├── .env.example            ← Template bez pravih ključeva
├── service-account.json    ← GCP service account key (NIJE u gitu)
└── CLAUDE.md               ← Ovaj fajl
```

## Environment varijable (.env)

```
GEMINI_API_KEY=...
GOOGLE_APPLICATION_CREDENTIALS=C:\Python\hm-analytics-dashboard\service-account.json
BIGQUERY_PROJECT=abiding-operand-409723
```

## GCP Resursi

| Resurs | Vrednost |
|--------|----------|
| GCP Project | `abiding-operand-409723` |
| BigQuery dataset | `hm_dataset` |
| GCS Bucket | `hm-dataset-bucket` |
| Service account | `ais-gemini-key-05716559cbd8458@1030606472500.iam.gserviceaccount.com` |

### IAM role na service accountu

- `BigQuery Data Viewer`
- `BigQuery Job User`
- `Storage Object Admin`

## BigQuery tabele

```
abiding-operand-409723.hm_dataset.articles
  article_id, product_code, prod_name, product_type_name, product_group_name,
  colour_group_name, department_name, index_group_name, section_name, garment_group_name

abiding-operand-409723.hm_dataset.customers
  customer_id, FN, Active, club_member_status, fashion_news_frequency, age, postal_code

abiding-operand-409723.hm_dataset.transactions_train   ← particionisana po t_dat
  t_dat (DATE), customer_id, article_id, price, sales_channel_id

abiding-operand-409723.hm_dataset.sample_submission
  customer_id, prediction
```

**Važno:** `transactions_train` uvek filtriraj po `t_dat` u WHERE klauzuli da ne skenira celu tabelu (31M redova).

## GCS Slike

Lokalni izvor: `C:\Datasets\h-and-m-personalized-fashion-recommendations\images`

Struktura na GCS: `gs://hm-dataset-bucket/images/{prve_3_cifre_article_id}/{article_id}.jpg`

Primer: article_id `0108775` → `gs://hm-dataset-bucket/images/010/0108775.jpg`

Upload skripta (`upload_images.py`) uploaduje 105.100 slika (~28GB) sa 8 paralelnih threadova.
Sigurna za ponovni pokretanje — preskače već uploadovane fajlove.

## Keš sistem

Svaki upit se kešira u `cache/{md5_pitanja}.json`. Struktura fajla:

```json
{
  "question": "...",
  "sql": "...",
  "interpretation": "...",
  "chart": { ... },
  "columns": [...],
  "rows": [...],
  "total_rows": 10,
  "from_cache": false
}
```

- `load_cache(question)` — vraća keširani rezultat ili `None`
- `save_cache(question, data)` — čuva rezultat
- `/history` endpoint — vraća sve keš fajlove sortirane po vremenu (koristi landing i istorija tab)

## Frontend — tab struktura

| Tab | Sadržaj |
|-----|---------|
| **Početna** | Landing grid sa svim prethodnim upitima i grafikonima, klik otvara rezultat |
| **Novi upit** | Input forma, SQL prikaz, interpretacija, grafikon, tabela rezultata |
| **Istorija** | Lista upita sa SQL-om i interpretacijom, klik popunjava input |
| **O projektu** | Info o stack-u i tabelama |

## Dizajn — Light tema sa malinasom

```css
--bg:      #F5F3EF   /* topla bela pozadina */
--surface: #FFFFFF
--card:    #FFFFFF
--border:  #E4DDD4
--accent:  #B82651   /* malina */
--accent2: #8B1A3C   /* tamna malina */
--text:    #1C1A1E
--muted:   #7A7585
```

SQL blok ostaje taman (#1E1A2A) — kod uvek izgleda bolje tamno.

## Gemini API — važne napomene

### SDK
Koristiti `google-genai` (novi SDK), **ne** `google-generativeai` (deprecated od 2025).

```python
from google import genai
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
```

### Model i limiti
- Koristiti `gemini-2.0-flash` — **1500 zahteva/dan** na free tier
- `gemini-2.5-flash` ima samo **20 zahteva/dan** — premalo za razvoj
- Kvota se resetuje svaki dan u ponoć UTC (01:00h srpsko vreme)

### Retry logika
App automatski ponovo pokušava na 503 (UNAVAILABLE) greške, do 4 puta sa porastom čekanja.
Na 429 (RESOURCE_EXHAUSTED) odmah vraća jasnu grešku korisniku.

## BigQuery klijent — važna napomena

Koristiti `create_bqstorage_client=False` jer service account nema `bigquery.readsessions.create`:

```python
job.result().to_dataframe(create_bqstorage_client=False)
```

## Instalirani paketi (CB okruženje)

Sve iz `requirements.txt` plus `db-dtypes` i `google-cloud-storage`:

```powershell
& "C:\Users\Biljana\.conda\envs\CB\Scripts\pip.exe" install -r requirements.txt db-dtypes google-cloud-storage
```

## Poznati problemi i rešenja

| Problem | Uzrok | Rešenje |
|---------|-------|---------|
| `gemini-1.5-flash not found` | Model deprecated | Koristiti `gemini-2.0-flash` |
| `google.generativeai` FutureWarning | SDK deprecated | Prebaciti na `google-genai` |
| `bigquery.readsessions.create` 403 | Storage API disabled | `create_bqstorage_client=False` |
| `db-dtypes` ImportError | Paket nedostaje | `pip install db-dtypes` |
| Windows `.pyd` blokada | SmartScreen | `Get-ChildItem ... -Recurse -Filter "*.pyd" \| Unblock-File` |
| Upload slika kroz browser | Browser freezes na 28GB | Koristiti `upload_images.py` skriptu |
| 429 RESOURCE_EXHAUSTED | Dnevni limit free tier | Sačekaj reset (01:00h) ili koristi keš |
| Istorija nestaje na refresh | JS memorija | Keš fajlovi su trajni, `/history` endpoint čita iz njih |

## Railway Deploy

```
Procfile: web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

ENV varijable na Railway: `GEMINI_API_KEY`, `BIGQUERY_PROJECT`, `GOOGLE_APPLICATION_CREDENTIALS` (sadržaj JSON-a kao string ili Workload Identity).

## Sledeći koraci

- [ ] Integracija slika iz GCS u rezultate tabele (po `article_id`)
- [ ] Deploy na Railway
