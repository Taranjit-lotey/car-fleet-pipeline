# Car Fleet Pipeline

A batch data pipeline that simulates autonomous vehicle telemetry, processes it at scale, and produces an analytics-ready dataset in BigQuery. Built on GCP using Cloud Storage, Dataproc, Cloud Composer, and BigQuery, with Looker as the reporting layer.

---

## What it does

The pipeline generates synthetic driving event data from a simulated fleet of vehicles equipped with a full self-driving (FSD) stack. Each run produces a batch of telemetry records covering things like speed, GPS coordinates, weather conditions, neural network predictions, and whether the driver overrode the model. That raw data lands in GCS, gets cleaned and transformed by a Spark job on Dataproc, and is loaded into BigQuery for downstream analysis.

The whole thing is orchestrated by Cloud Composer (managed Airflow). The simulator itself runs as a step in the DAG via the Airflow `PythonOperator`, so there is no need to kick it off manually.

---

## Pipeline architecture

```
Simulator (PythonOperator)
        |
        v
Cloud Storage (raw NDJSON)
        |
        v
Dataproc (PySpark — clean, transform, partition)
        |
        v
BigQuery (analytics dataset)
        |
        v
Looker (dashboards / reports)
```

---

## Simulator

`simulator/generate_events.py` is the data source for the pipeline. It produces a newline-delimited JSON file where each line is one driving event from one vehicle.

### FSD logic

Each event records what the neural network predicted (`nn_prediction`) and what the driver actually did (`driver_action`). About 8% of the time the driver overrides the model, which sets `intervened = true` and tags the record with an `intervention_type` — either `brake_override` or `steering_override`. This is the core signal the analytics layer is built around: understanding where and under what conditions the model and driver disagree.

Speed limits, camera occlusion probability, and visibility range are all conditioned on road type and weather, so the data has realistic correlations rather than being purely random.

### Bad record injection

A configurable fraction of records (`--bad-record-rate`, default 5%) are intentionally corrupted before being written to disk. Corruptions include null vehicle IDs, invalid timestamps, out-of-range sensor values, and null coordinates. This is done to exercise the data quality checks in the Spark transformation step and to make sure the pipeline handles malformed input without failing silently.

### Running it standalone

```bash
python simulator/generate_events.py \
  --num-vehicles 50 \
  --num-events 10000 \
  --output-dir ./data/raw \
  --bad-record-rate 0.05
```

| Argument | Default | Description |
|---|---|---|
| `--num-vehicles` | 50 | Number of unique vehicle IDs to simulate |
| `--num-events` | 10000 | Total records to generate |
| `--output-dir` | `./data/raw` | Where to write the output file |
| `--bad-record-rate` | 0.05 | Fraction of records to corrupt |

Output is a single timestamped NDJSON file (`driving_events_YYYYMMDD_HHMMSS.json`). The format is compatible with both BigQuery direct load and Spark ingestion.

In the full pipeline this script is called by the Airflow `PythonOperator` in the DAG, which passes the arguments and then hands the output path to the next task for upload to GCS.

---

## GCP setup

| Service | Purpose |
|---|---|
| Cloud Storage | Staging area for raw simulator output and Spark job artifacts |
| Dataproc | Runs the PySpark transformation job |
| Cloud Composer | Orchestrates the full pipeline end to end |
| BigQuery | Final destination for cleaned, partitioned data |
| Looker | Reporting and dashboards on top of BigQuery |

---

## Data

`data/raw/` holds locally generated files when running the simulator outside of Composer. In production this directory is replaced by a GCS bucket.
