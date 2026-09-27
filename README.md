# Multi-Database Entity Resolution & Unified Data Repository

A server-rendered application that ingests CSV files and SQL INSERT dumps, maps different source schemas to canonical fields, and progressively resolves overlapping records into traceable master entities.

## Requirements

- Python 3.10 or newer
- A Neon PostgreSQL database and its standard PostgreSQL connection string
- No Docker or local PostgreSQL installation is required

## Setup

1. Create a Neon project/database and copy its connection string, including SSL settings where provided.
2. In Neon SQL Editor, run the complete contents of [`schema.sql`](schema.sql).
3. Create and activate a virtual environment, then install requirements:

	```powershell
	py -m venv venv
	.\venv\Scripts\Activate.ps1
	pip install -r requirements.txt
	```

4. Copy `.env.example` to `.env` and set `DATABASE_URL` to the Neon URL. `AI_API_KEY` is optional; deterministic mapping remains available without it. The optional provider endpoint and model can be set with `AI_API_URL` and `AI_MODEL`.
5. Start the application from the project root:

	```powershell
	uvicorn app.main:app --reload
	```

6. Open `http://127.0.0.1:8000`.

The app does not implement login or authentication. Anyone with access to the deployed URL can use its routes, as required for evaluator access. Keep the deployment network boundary in mind if the database contains sensitive data.

## Sample data

Install requirements, then run:

```powershell
python scripts/generate_data.py
```

The generator creates `data/database_a.csv` through `data/database_e.csv`, `data/database_d.sql`, and a 100,000-row `data/database_large.csv`. Database E is intended for the later-source demo. The large file is ignored by Git.

## Tech stack

- FastAPI and Uvicorn
- PostgreSQL on Neon via `DATABASE_URL` and psycopg 3
- Jinja2 templates with plain HTML, minimal CSS, and vanilla JavaScript
- pandas for chunked CSV processing
- python-dotenv, sqlparse, requests, and Faker

## Deployment

Live URL: _Add the deployed application URL here._

## Notes

- The schema must be loaded into Neon before the first run; the app does not create database objects automatically.
- Uploading a file creates a batch in mapping review. Records are ingested only after the mappings are confirmed.
- The upload directory is `data/uploads/`; retain it if interrupted batches may need to resume after a restart.
- See [`docs/architecture.md`](docs/architecture.md) for processing, matching, traceability, recovery, and scaling details.
