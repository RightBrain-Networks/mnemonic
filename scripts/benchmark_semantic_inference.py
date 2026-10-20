"""Measure isolated local-model throughput using public synthetic text only.

Run each worker/thread configuration in its own process to compare peak RSS.
Model files must already be cached; this benchmark never accesses the database.
"""

import argparse
import json
import os
import resource
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from statistics import median
from time import monotonic

from mnemonic_api.semantic import FastembedEmbedder

QUERY = "Represent this sentence for searching relevant passages: semantic inference capacity"
DOCUMENT = (
    "A local search service embeds work items and compares their vectors. "
    "Requests wait in a bounded queue while another embedding batch is running. "
    "Database reads and cache writes release model capacity for other searches. "
)


def current_rss_mib() -> float | None:
    """Linux current resident memory, separately from the lifetime peak."""
    status = Path("/proc/self/status")
    if not status.is_file():
        return None
    for line in status.read_text().splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    return None


def measure(models: Queue, requests: int, workers: int, documents: bool, document: str) -> dict:
    started = monotonic()

    def execute(index: int) -> float:
        model = models.get()
        try:
            if documents and index % 4 == 0:
                model.embed_documents([document] * 16)
            else:
                model.embed_query(QUERY)
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
        "rss_after_burst_mib": current_rss_mib(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--bursts", type=int, default=3)
    parser.add_argument("--document-repeats", type=int, default=6,
                        help="Use 16 to exercise the model's maximum token window")
    parser.add_argument("--cache-dir", default="/app/.embedding-cache")
    args = parser.parse_args()
    if min(args.threads, args.requests, args.bursts, args.document_repeats) < 1:
        parser.error("Thread, request, burst and document repetition counts must be positive")
    os.environ["MNEMONIC_EMBEDDING_CACHE"] = args.cache_dir
    os.environ["HF_HUB_OFFLINE"] = "1"
    models = Queue()
    for _ in range(args.workers):
        model = FastembedEmbedder(threads=args.threads)
        model.embed_query(QUERY)
        models.put(model)
    warm_rss = current_rss_mib()
    results = [measure(models, args.requests, args.workers, documents,
                       DOCUMENT * args.document_repeats)
               for documents in (False, True) for _ in range(args.bursts)]
    print(json.dumps({"workers": args.workers, "threads_per_worker": args.threads,
                      "document_repeats": args.document_repeats,
                      "warm_rss_mib": warm_rss, "idle_rss_mib": current_rss_mib(),
                      "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                            / 1024, 1), "results": results}))


if __name__ == "__main__":
    main()
