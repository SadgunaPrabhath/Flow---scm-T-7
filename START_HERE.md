# START HERE

One page to get from "I downloaded a zip" to "the system is running".

---

## 1. Unzip and go inside

```bash
unzip project24_scm.zip
cd project24_scm
```

## 2. What you have

```
config.py                settings + credentials, all from environment variables
db.py                    MySQL connection pool, parameterised SQL, error mapping
run.py                   starts the Flask API on port 5000
api/                     REST endpoints (CRUD + analytics), validation, error handling
analytics/               formulas, demand, supplier, inventory, forecast, pipeline
viz/charts.py            10 Matplotlib charts, each with a written interpretation
sql/schema.sql           6 tables + 2 views, keys, CHECK constraints, indexes
sql/seed_data.sql        ready-made synthetic dataset (24 SKUs, 377 POs, 12k+ rows)
scripts/                 generate data, load SQL, run analysis, build docs, showcase, slide deck
tests/                   199 automated tests
docs/                    synopsis, DB design, API reference, methodology,
                         test report, demo plan, limitations, data dictionary,
                         Project24_Final_Report.docx, project_showcase.html
presentation/            Project24_Demo.pptx (14 slides)
reports/                 analysis_report.md, charts/, tables/ (pre-generated)
```

Everything in `reports/` is already generated, so you can look at results **before**
installing anything: open `docs/project_showcase.html` in any browser (fully
self-contained, no internet needed) or `reports/analysis_report.md`.

## 3. Install

Requires **Python 3.10+** and **MySQL 8 / MariaDB 10.6+**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Configure the database (3 values)

```bash
cp .env.example .env
```

Edit `.env` and set `DB_USER`, `DB_PASSWORD`, `DB_NAME`. Create the user once:

```sql
CREATE DATABASE supplychain CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'scm_app'@'localhost' IDENTIFIED BY 'your-password';
GRANT ALL PRIVILEGES ON supplychain.* TO 'scm_app'@'localhost';
FLUSH PRIVILEGES;
```

> There is **no `.env` in the zip** on purpose — it holds credentials and must never be
> committed or shared. `.env.example` shows every variable.

## 5. Load schema + data, then run

```bash
python scripts/load_sql.py sql/schema.sql
python scripts/generate_seed_data.py --load     # or: python scripts/load_sql.py sql/seed_data.sql
python scripts/run_pipeline.py --persist        # tables + charts + report
python run.py                                   # http://localhost:5000
```

Open <http://localhost:5000> — that is the API explorer; click any endpoint.

## 6. Test

```bash
python -m pytest tests -q      # expect: 199 passed
```

Integration tests skip automatically (with a reason) if MySQL is not running.

---

## 7. Before you submit — the only manual edits

1. **Team details.** Replace `‹member 1› … ‹member 5›` on the cover of
   `docs/Project24_Final_Report.docx` and in `docs/01_synopsis_and_proposal.md` §10.
2. **Report template.** If your college requires its own format, apply it to the
   `.docx` (or regenerate: `python scripts/build_documents.py` after editing
   `scripts/build_documents.py`).
3. **Screenshots.** The rubric asks for screenshots from *your* machine — take 6–8
   while running `python run.py` (list of good calls: `docs/03_api_reference.md` §6).
4. **Certificate / declaration / acknowledgement** pages if your college requires them.

## 8. Regenerating things

| Change | Command |
|---|---|
| New synthetic data (different seed/window) | `python scripts/generate_seed_data.py --seed 7 --days 365 --load` |
| Re-run analysis with other assumptions | `python scripts/run_pipeline.py --service-level 0.99 --policy zscore` |
| Rebuild charts + Markdown report | `python scripts/run_pipeline.py` |
| Rebuild Word report + PowerPoint | `python scripts/build_documents.py` |
| Rebuild the showcase site (offline, from reports/) | `python scripts/build_showcase.py` |
| Rebuild the slide deck | `python scripts/build_presentation.py` |
| Refresh the data dictionary | `python scripts/dump_data_dictionary.py` |

## 9. The one rule that must not break

The system is **decision support**: it calculates and explains, it never places,
approves or sends a purchase order. Keep that true in anything you add — the demo and
the report both state it, and a test asserts it
(`tests/test_api_analytics.py::test_run_stores_snapshot_without_creating_orders`).

## 10. If something fails

| Symptom | Cause / fix |
|---|---|
| `503 DATABASE_UNAVAILABLE` | MySQL not running, or wrong credentials in `.env` |
| `ModuleNotFoundError: flask` | virtual environment not activated |
| `Access denied for user` | the MySQL user/grant in step 4 was not created |
| Charts look empty | data not loaded — run step 5 first |
| `pytest` shows skips | MySQL is down; the pure-logic tests still pass |
