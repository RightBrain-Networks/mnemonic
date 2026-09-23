# Semantic inference admission

Application/API/MCP/dashboard 0.73.0 replaces the Phase 9 one-slot, 50 ms
admission policy. No schema migration, wire-format change, or client retry
change is required. The Claude plugin remains 0.43.0.

## Behavior and configuration

All API semantic searches (work, artifact, unified, and multi-project) and
duplicate suggestions share a fixed pool of independently loaded local models.
Each query embedding or document batch acquires a model permit and releases it
when the native call returns. Database reads, ranking, cache publication, and
response serialization do not occupy model capacity. FIFO admission lets other
waiting queries run between a request's document batches.

The existing environment prefix is retained because operators may already set
these values. Every suffix below starts with `MNEMONIC_DUPLICATE_SUGGESTION_`.

| Suffix | Default | Accepted range | Meaning |
| --- | ---: | --- | --- |
| `INFERENCE_SLOTS` | 2 | 1–4 | Independent, lazily loaded API model workers |
| `INFERENCE_THREADS` | 1 | 1–8 | Native inference threads per API model instance |
| `INFERENCE_QUEUE_SIZE` | 8 | 0–16 | Waiting model calls per API process; zero disables waiting |
| `INFERENCE_WAIT_MS` | 5000 | 1–30000 | Maximum queue wait per call, clipped to its remaining deadline |

Models are loaded only as concurrent use needs them; idle calls reuse a warm
instance. Increasing slots increases possible model memory use. Size native
threads and worker count together for the host CPU budget. Multiple API
processes each have their own pool and queue.

A full queue or an expired capacity wait preserves the existing
`capacity_exhausted` reason. Expiry of the enclosing request or external
comparison deadline remains `deadline_exceeded`. Duplicate suggestions retain
their lexical fallback; explicit semantic searches return the existing typed
503 error. The response continues to permit one retry after one second.

The suggestion request limit remains four, with a 250 ms admission wait. The
0.74.0 [response deadline amendment](duplicate-suggestion-deadlines.md) replaces
the previous 60-second deadline with a maximum 45-second backend response budget
and a 50-second MCP adapter ceiling. Internal work reserves response time and
retains lexical results before inference. External comparison
retains its five-second stage budget. Waiting and each new native call check
the applicable deadline. Cancellation wakes queued calls and prevents new
batches. An already running native call cannot be forcibly cancelled: its
worker retains the model permit until actual completion, even if the response
has already timed out. The suggestion request permit likewise remains held
until its underlying workers finish.

## Upgrade guidance

Existing explicit inference `.env` values remain effective. The response timeout
now accepts 1–45 seconds; change an older explicit
`MNEMONIC_DUPLICATE_SUGGESTION_TIMEOUT_SECONDS=60` to 45 before recreating the API.
An installation still setting
`INFERENCE_SLOTS=1` and `INFERENCE_WAIT_MS=50` keeps those choices after upgrade.
To use the new defaults, set the four fully prefixed variables to the table's
values (or remove old overrides and use Compose defaults), then rebuild/recreate
the API. Configuration is read at process startup. No database migration or
embedding-cache invalidation is needed. Query vectors and document composition
remain unchanged.

The source model lock remains per model instance. Increasing the slot count now
creates independent model instances rather than placing more callers behind one
shared model lock. Each waiting call occupies a synchronous request worker, so
the queue ceiling deliberately stays below the framework's ordinary worker pool
size; an unbounded queue would delay unrelated requests under overload.

## Reproducible sizing evidence

Measured September 22, 2026 in isolated offline containers using the existing
API image's cached `BAAI/bge-small-en-v1.5` model, with a four-CPU quota and a
4 GiB memory limit. The host was shared. Each fresh process warmed its models,
then ran three bursts of 24 synthetic requests for each workload. Mixed bursts
contained six 16-document batches and eighteen query embeddings. No database,
private content, live requests, or provider API was involved.

The script is [benchmark_semantic_inference.py](../scripts/benchmark_semantic_inference.py).
Run it with the backend environment and a populated model cache, for example:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project backend python \
  scripts/benchmark_semantic_inference.py --workers 2 --threads 1 \
  --cache-dir /path/to/cached/models
```

Each row reports the median whole-burst time across three samples and peak RSS
for that process. These are sizing observations, not production latency or
percentile guarantees; model loading is excluded from the timed bursts.

| Workers | Threads each | Query burst | Mixed burst | Peak RSS |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 4 | 752 ms | 27,902 ms | 440 MiB |
| 2 | 2 | 448 ms | 14,650 ms | 763 MiB |
| 4 | 1 | 221 ms | 10,931 ms | 1,415 MiB |
| 1 | 1 | 736 ms | 30,384 ms | 463 MiB |
| 2 | 1 | 421 ms | 15,967 ms | 765 MiB |

With the same one-thread setting, two model workers reduced the query burst by
about 43% and the mixed burst by about 47%, while increasing measured peak RSS
by about 302 MiB. Two workers with one native thread each are the default to
provide concurrency while retaining CPU and memory headroom. Four workers are
available for installations that can afford the additional memory. Heavy
batches can still exceed a configured wait; the queue and deadline failures
remain explicit.

## Regression coverage

Concurrency tests use controlled worker barriers to verify FIFO order, bounded
queue rejection, queue/request/stage deadlines, cancellation cleanup, retained
native ownership, model-pool parallelism, and worker return after failure.
Real PostgreSQL route tests verify that cache publication does not block another
semantic search, a duplicate check can wait for competing search inference, and
capacity failures during later document batches retain the correct safe reason.
Existing artifact, multi-project, fallback, and disposable-cache tests retain
coverage of the unchanged response contract.
