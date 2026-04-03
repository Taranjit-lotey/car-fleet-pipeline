# Car Fleet Pipeline

A data pipeline that simulates autonomous vehicle telemetry, processes it at scale, and produces an analytics-ready dataset in BigQuery. Supports both a batch path and a real-time streaming path. Built on GCP using Cloud Storage, Dataproc, Dataflow, Cloud Pub/Sub, Cloud Composer, and BigQuery, with Looker as the reporting layer.

---

## What it does

The pipeline generates synthetic driving event data from a simulated fleet of vehicles equipped with a full self-driving (FSD) stack. Each run produces a batch of telemetry records covering things like speed, GPS coordinates, weather conditions, neural network predictions, and whether the driver overrode the model. That raw data lands in GCS, gets cleaned and transformed by a Spark job on Dataproc, and is loaded into BigQuery for downstream analysis.

The whole thing is orchestrated by Cloud Composer (managed Airflow). The simulator itself runs as a step in the DAG via the Airflow `PythonOperator`, so there is no need to kick it off manually.

---

## Pipeline architecture

There are two paths. They share the same simulator and write to the same BigQuery dataset, but differ in how data moves and how fast it arrives.

**Batch**
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
BigQuery (bulk load)
        |
        v
Looker (dashboards / reports)
```

**Real-time**
```
Simulator (streaming mode)
        |
        v
Cloud Pub/Sub (ingestion topic)
        |
        v
Dataflow (Beam streaming job — validate, transform, window)
        |          |
        v          v
  BigQuery    Pub/Sub dead-letter topic (bad records)
  (streaming
   inserts)
        |
        v
Looker (near-real-time dashboards)
```

| Concern | Batch | Real-time |
|---|---|---|
| Ingestion | GCS (NDJSON file) | Pub/Sub topic |
| Processing | Dataproc / PySpark | Dataflow / Apache Beam |
| Orchestration | Cloud Composer (Airflow) | Dataflow job runs continuously |
| BQ write method | Bulk load job | Streaming inserts |
| Latency | Minutes to hours | Seconds |

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

In the batch pipeline this script is called by the Airflow `PythonOperator` in the DAG, which passes the arguments and then hands the output path to the next task for upload to GCS.

In streaming mode (`--mode streaming`), the simulator publishes each event as a JSON message directly to a Pub/Sub topic instead of writing to disk. The core event generation logic is the same either way.

---

## Real-time pipeline

### Pub/Sub

Two topics are needed:

- `fleet-events` — the main ingestion topic. The simulator publishes to this in streaming mode.
- `fleet-events-deadletter` — receives records that fail validation in the Dataflow job, rather than silently dropping them. Bad records can be inspected or replayed from here.

### Dataflow job

The Dataflow job is an Apache Beam streaming pipeline that:

1. Reads from the `fleet-events` subscription
2. Parses and validates each message — applying the same checks as the Spark batch job (null vehicle IDs, invalid timestamps, out-of-range sensor values, etc.)
3. Routes bad records to the dead-letter topic
4. Applies windowing (1-minute tumbling windows) for any aggregations
5. Writes clean records to BigQuery via streaming inserts

Because the Dataflow job runs continuously, it does not go through Cloud Composer. It is deployed separately and stays live as long as the simulator is publishing.

### BigQuery

Streaming inserts land in the same dataset as the batch pipeline. The two paths are differentiated by a `pipeline_mode` field (`batch` or `streaming`) added during transformation, so Looker can filter or compare them.

---

## GCP setup

| Service | Purpose |
|---|---|
| Cloud Storage | Staging area for raw simulator output and Spark job artifacts (batch path) |
| Dataproc | Runs the PySpark transformation job (batch path) |
| Cloud Pub/Sub | Event ingestion and dead-letter queue (real-time path) |
| Dataflow | Runs the Apache Beam streaming job (real-time path) |
| Cloud Composer | Orchestrates the batch pipeline end to end |
| BigQuery | Final destination for cleaned data from both paths |
| Looker | Reporting and dashboards on top of BigQuery |

---

## Data

`data/raw/` holds locally generated files when running the simulator outside of Composer. In production this directory is replaced by a GCS bucket.
