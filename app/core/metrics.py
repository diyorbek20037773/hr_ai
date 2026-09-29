"""Prometheus metrics (all prefixed `hr_ai_`)."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

HTTP_REQUESTS = Counter("hr_ai_http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_DURATION = Histogram(
    "hr_ai_http_request_duration_seconds", "HTTP request duration", ["path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
LLM_DURATION = Histogram(
    "hr_ai_llm_inference_duration_seconds", "LLM generate() duration",
    buckets=(0.5, 1, 2, 3, 5, 8, 12, 20, 30, 45, 60, 90, 120),
)
LLM_REQUESTS = Counter("hr_ai_llm_requests_total", "LLM requests by outcome", ["status"])
LLM_JSON_RETRY = Counter("hr_ai_llm_json_retry_total", "Corrective retries after invalid JSON")
LLM_QUEUE_DEPTH = Gauge("hr_ai_llm_queue_depth", "Requests waiting for or holding the LLM lock")
MODEL_LOADED = Gauge("hr_ai_model_loaded", "1 when the local model is loaded")
GPU_MEMORY_USED = Gauge("hr_ai_gpu_memory_used_bytes", "CUDA memory allocated by this process")
RESOLVER_FIELDS = Counter("hr_ai_resolver_fields_total", "Resolved fields by result", ["result"])
FUZZY_SCORE = Histogram("hr_ai_fuzzy_score", "RapidFuzz best score for fuzzy lookups",
                        buckets=(50, 60, 70, 75, 80, 85, 88, 90, 95, 100))
HR_SEARCH_REQUESTS = Counter("hr_ai_hr_search_requests_total", "Calls to the HR Search API", ["status"])

for _status in ("ok", "json_error", "timeout", "error"):
    LLM_REQUESTS.labels(status=_status)
for _result in ("resolved", "unresolved", "candidate"):
    RESOLVER_FIELDS.labels(result=_result)


def update_gpu_memory() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            GPU_MEMORY_USED.set(torch.cuda.memory_allocated())
    except Exception:  # noqa: BLE001 — metrics must never break a request
        pass


def render_metrics() -> tuple[bytes, str]:
    update_gpu_memory()
    return generate_latest(), CONTENT_TYPE_LATEST
