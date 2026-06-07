import os
import json
import re
import hashlib
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, abort
from dotenv import load_dotenv
from google import genai
from google.cloud import bigquery
import plotly
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

load_dotenv()

app = Flask(__name__)

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# Lokalni folder sa slikama — kad slike budu na GCS, samo staviti USE_GCS = True
LOCAL_IMAGES = Path(r"C:\Datasets\h-and-m-personalized-fashion-recommendations\images")
USE_GCS = False
GCS_BUCKET_URL = "https://storage.googleapis.com/hm-dataset-bucket"


def image_url(article_id: str) -> str:
    padded = str(article_id).zfill(10)
    if USE_GCS:
        return f"{GCS_BUCKET_URL}/images/{padded[:3]}/{padded}.jpg"
    return f"/image/{padded}"


def cache_key(question: str) -> Path:
    h = hashlib.md5(question.strip().lower().encode()).hexdigest()
    return CACHE_DIR / f"{h}.json"


def load_cache(question: str) -> dict | None:
    path = cache_key(question)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_cache(question: str, data: dict) -> None:
    path = cache_key(question)
    payload = {"question": question, **data}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODEL = "gemini-2.0-flash"

BQ_PROJECT = os.environ.get("BIGQUERY_PROJECT", "abiding-operand-409723")
BQ_DATASET = "hm_dataset"

SCHEMA_DESCRIPTION = """
BigQuery project: abiding-operand-409723
Dataset: hm_dataset

Tables:

1. `abiding-operand-409723.hm_dataset.articles`
   - article_id STRING
   - product_code STRING
   - prod_name STRING
   - product_type_name STRING
   - product_group_name STRING
   - colour_group_name STRING
   - department_name STRING
   - index_group_name STRING
   - section_name STRING
   - garment_group_name STRING

2. `abiding-operand-409723.hm_dataset.customers`
   - customer_id STRING
   - FN FLOAT64
   - Active FLOAT64
   - club_member_status STRING
   - fashion_news_frequency STRING
   - age FLOAT64
   - postal_code STRING

3. `abiding-operand-409723.hm_dataset.transactions_train`
   - t_dat DATE (partition column)
   - customer_id STRING
   - article_id STRING
   - price FLOAT64
   - sales_channel_id INT64

4. `abiding-operand-409723.hm_dataset.sample_submission`
   - customer_id STRING
   - prediction STRING

Important notes for SQL generation:
- Always use fully qualified table names: `abiding-operand-409723.hm_dataset.table_name`
- For transactions_train, always add a WHERE clause filtering t_dat to limit partition scan,
  e.g. WHERE t_dat >= '2020-01-01'
- Use LIMIT clauses to avoid excessive data scans (default LIMIT 1000 unless aggregating)
- For aggregations that return few rows, no LIMIT needed
"""


def gemini_generate(prompt: str, retries: int = 4) -> str:
    for attempt in range(retries):
        try:
            response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            return response.text.strip()
        except Exception as e:
            err = str(e)
            if ("503" in err or "UNAVAILABLE" in err) and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                raise RuntimeError("Gemini API dnevni limit dostignut. Pokušaj sutra ili omogući billing na AI Studio.")
            raise
    raise RuntimeError("Gemini API nedostupan, pokušaj ponovo za koji minut.")


def generate_sql(question: str) -> str:
    prompt = f"""You are a BigQuery SQL expert. Given the following schema, generate a valid BigQuery SQL query to answer the user's question.

{SCHEMA_DESCRIPTION}

Rules:
- Return ONLY the SQL query, no explanations, no markdown fences, no backticks around the whole query
- Use standard BigQuery SQL syntax
- Always use fully qualified table names
- For transactions_train always filter t_dat (e.g. WHERE t_dat >= '2020-01-01') to limit partition scans
- Add LIMIT unless the query is an aggregation returning few rows

User question: {question}

SQL query:"""

    sql = gemini_generate(prompt)
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip()


def run_bigquery(sql: str) -> pd.DataFrame:
    client = bigquery.Client(project=BQ_PROJECT)
    job = client.query(sql)
    return job.result().to_dataframe(create_bqstorage_client=False)


def interpret_results(question: str, sql: str, df: pd.DataFrame) -> str:
    if df.empty:
        return "Upit nije vratio rezultate."

    sample = df.head(10).to_string(index=False)
    total_rows = len(df)

    prompt = f"""Given the user's question and the SQL query results, write one or two concise sentences in Serbian (latin script) that summarize the key insight from the data. Be specific with numbers.

User question: {question}

SQL used:
{sql}

Query results ({total_rows} rows total, showing first 10):
{sample}

Summary (in Serbian):"""

    return gemini_generate(prompt)


def build_chart(df: pd.DataFrame, question: str) -> dict | None:
    if df.empty or len(df.columns) < 1:
        return None

    cols = list(df.columns)
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    categorical_cols = [c for c in cols if c not in numeric_cols]

    fig = None

    # Two columns: one categorical + one numeric → bar chart
    if len(categorical_cols) >= 1 and len(numeric_cols) >= 1:
        x_col = categorical_cols[0]
        y_col = numeric_cols[0]
        plot_df = df[[x_col, y_col]].dropna().head(30)
        fig = px.bar(
            plot_df,
            x=x_col,
            y=y_col,
            title=question[:80],
            labels={x_col: x_col.replace("_", " ").title(), y_col: y_col.replace("_", " ").title()},
            color_discrete_sequence=["#B82651"],
        )
        fig.update_layout(
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            font_color="#1C1A1E",
            title_font_size=14,
            title_font_color="#1C1A1E",
            margin=dict(l=40, r=20, t=50, b=80),
            xaxis_tickangle=-35,
            xaxis=dict(gridcolor="#EDE8E2", linecolor="#E4DDD4"),
            yaxis=dict(gridcolor="#EDE8E2", linecolor="#E4DDD4"),
        )

    # Only numeric columns and few rows → could be a single-value result
    elif len(numeric_cols) >= 1 and len(categorical_cols) == 0 and len(df) <= 5:
        fig = go.Figure(
            go.Indicator(
                mode="number",
                value=float(df[numeric_cols[0]].iloc[0]),
                title={"text": numeric_cols[0].replace("_", " ").title(), "font": {"color": "#7A7585"}},
                number={"font": {"color": "#B82651", "size": 60}},
            )
        )
        fig.update_layout(
            paper_bgcolor="#FFFFFF",
            font_color="#1C1A1E",
            height=250,
        )

    # Two numeric columns → scatter
    elif len(numeric_cols) >= 2:
        plot_df = df[numeric_cols[:2]].dropna().head(500)
        fig = px.scatter(
            plot_df,
            x=numeric_cols[0],
            y=numeric_cols[1],
            title=question[:80],
            color_discrete_sequence=["#B82651"],
        )
        fig.update_layout(
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            font_color="#1C1A1E",
            xaxis=dict(gridcolor="#EDE8E2", linecolor="#E4DDD4"),
            yaxis=dict(gridcolor="#EDE8E2", linecolor="#E4DDD4"),
        )

    if fig is None:
        return None

    return json.loads(plotly.io.to_json(fig))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/image/<article_id>")
def serve_image(article_id):
    padded = str(article_id).zfill(10)
    img_path = LOCAL_IMAGES / padded[:3] / f"{padded}.jpg"
    if img_path.exists():
        return send_file(img_path, mimetype="image/jpeg")
    abort(404)


@app.route("/history")
def history():
    items = []
    for f in sorted(CACHE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append({
                "question": data.get("question", ""),
                "sql": data.get("sql", ""),
                "interpretation": data.get("interpretation", ""),
                "total_rows": data.get("total_rows", 0),
                "chart": data.get("chart"),
                "columns": data.get("columns", []),
                "rows": data.get("rows", []),
                "time": f.stat().st_mtime,
            })
        except Exception:
            continue
    return jsonify(items)


@app.route("/query", methods=["POST"])
def query():
    data = request.get_json()
    question = (data or {}).get("question", "").strip()

    if not question:
        return jsonify({"error": "Pitanje ne sme biti prazno."}), 400

    cached = load_cache(question)
    if cached:
        cached["from_cache"] = True
        return jsonify(cached)

    try:
        sql = generate_sql(question)
    except Exception as e:
        return jsonify({"error": f"Greška pri generisanju SQL-a: {e}"}), 500

    try:
        df = run_bigquery(sql)
    except Exception as e:
        return jsonify({"error": f"Greška pri izvršavanju upita: {e}", "sql": sql}), 500

    try:
        interpretation = interpret_results(question, sql, df)
    except Exception as e:
        interpretation = "(Interpretacija nije dostupna)"

    chart_json = build_chart(df, question)

    # Convert df to JSON-serializable format
    records = []
    has_images = False
    if not df.empty:
        df_display = df.head(100)
        for col in df_display.columns:
            if df_display[col].dtype == "object":
                df_display[col] = df_display[col].astype(str)
        records = df_display.to_dict(orient="records")
        if "article_id" in df.columns:
            has_images = True
            for row in records:
                row["_image_url"] = image_url(row["article_id"])

    result = {
        "sql": sql,
        "interpretation": interpretation,
        "chart": chart_json,
        "columns": list(df.columns),
        "rows": records,
        "total_rows": len(df),
        "has_images": has_images,
        "from_cache": False,
    }
    save_cache(question, result)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
