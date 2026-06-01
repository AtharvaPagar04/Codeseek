# Smoke Test

Run this after a successful ingestion.

Start Qdrant:

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run ingestion:

```bash
python -m rag_ingestion.main /absolute/path/to/repo
```

For private GitHub repos, set one of these before running ingestion:

```bash
export GITHUB_TOKEN=your_token
# or
export GH_TOKEN=your_token
```

Optional incremental mode:

Set `RECREATE_COLLECTION_EACH_RUN = False` in `rag_ingestion/config.py` to keep
the collection and upsert by deterministic `chunk_id` instead of recreating it
on every run.

Verify the Qdrant collection:

```bash
python scripts/smoke_test_qdrant.py
```

The printed point count should match `Embeddings stored` from the ingestion
report. The dummy search should print up to three stored payload entries.
