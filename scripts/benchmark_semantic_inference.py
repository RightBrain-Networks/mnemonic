"""Measure isolated local-model throughput using public synthetic text only.

Run each worker/thread configuration in its own process to compare peak RSS.
Model files must already be cached; this benchmark never accesses the database.
"""

import argparse
import json
import resource
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from statistics import median
from time import monotonic

from fastembed import TextEmbedding

MODEL = "BAAI/bge-small-en-v1.5"
QUERY = "Represent this sentence for searching relevant passages: semantic inference capacity"
DOCUMENT = (
    "A local search service embeds work items and compares their vectors. "
    "Requests wait in a bounded queue while another embedding batch is running. "
    "Database reads and cache writes release model capacity for other searches. "
) * 6


def measure(models: Queue, requests: int, workers: int, documents: bool) -> dict:
    started = monotonic()

    def execute(index: int) -> float:
        model = models.get()
        try:
            if documents and index % 4 == 0:
                list(model.embed([DOCUMENT] * 16))
            else:
                list(model.query_embed([QUERY]))
        finally:
            models.put(model)
        return monotonic() - started

    with ThreadPoolExecutor(max_workers=workers) as executor:
        elapsed = sorted(executor.map(execute, range(requests)))
    duration = monotonic() - started
    return {
        "workload": "mixed_batches_and_queries" if documents else "queries",
        "requests": requests,
        "elapsed_ms": round(duration * 1000, 1),
        "requests_per_second": round(requests / duration, 2),
        "burst_median_ms": round(median(elapsed) * 1000, 1),
        "burst_p95_ms": round(elapsed[min(len(elapsed) - 1, int(len(elapsed) * .95))] * 1000, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--cache-dir", default="/app/.embedding-cache")
    args = parser.parse_args()
    models = Queue()
    for _ in range(args.workers):
        model = TextEmbedding(MODEL, threads=args.threads, cache_dir=args.cache_dir)
        list(model.query_embed([QUERY]))
        models.put(model)
    results = [measure(models, args.requests, args.workers, documents)
               for documents in (False, True) for _ in range(3)]
    print(json.dumps({"workers": args.workers, "threads_per_worker": args.threads,
                      "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                            / 1024, 1), "results": results}))


if __name__ == "__main__":
    main()
