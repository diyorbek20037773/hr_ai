# Environment (Phase 0 findings + model load verification)

Recorded 2026-09-30 on the development machine.

## Machine

| Item | Value |
|---|---|
| OS | Windows 11 Pro 10.0.26200 |
| CPU | 13th Gen Intel Core i5-13420H — 8 cores / 12 threads |
| RAM | 15.7 GB total (~3.9 GB free at inspection time) |
| GPU | NVIDIA GeForce RTX 3050 Laptop, **6 GB VRAM** (~5.0 GB free), driver 576.52, CUDA 12.9 |
| Disk D: | 233 GB, 63 GB free |
| git | 2.53.0.windows.1 (repo initialised in this session, branch `main`, no remote) |
| Docker | 29.7.2 installed (optional monitoring stack only; not required) |

## Python / venv

`D:\myprojects\hr_ai\.venv` — Python 3.13.5 (existing venv, kept as is). At inspection it only had
`huggingface_hub` and its deps; no torch/transformers.

Installed in this session (pinned in `requirements.txt`):

| Package | Version | Note |
|---|---|---|
| torch | 2.11.0+cu128 | CUDA wheel from download.pytorch.org/whl/cu128 (driver supports CUDA 12.9) |
| transformers | 5.17.0 | ≥ 4.51 required for Qwen3 |
| accelerate | 1.15.0 | device maps |
| bitsandbytes | 0.50.2 | 4-bit / 8-bit quantization (6 GB GPU) |
| safetensors | 0.8.0 | |
| fastapi / uvicorn | 0.142.0 / 0.54.0 | |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | |
| rapidfuzz | 3.14.6 | |
| httpx | 0.28.1 | |
| prometheus-client | 0.26.0 | |
| pytest / pytest-asyncio / ruff | 9.1.1 / 1.4.0 / 0.16.9 | |

`torch.cuda.is_available()` → `True`.

## Model

| Item | Value |
|---|---|
| Detected path | `D:\myprojects\hr_ai\model` (config.json + tokenizer files at the root) |
| Architecture | `Qwen3ForCausalLM`, 36 layers, hidden 2560, bf16, `transformers_version` 4.51.0 |
| Weights | 3 safetensors shards, 8,045,591,552 bytes total |
| Completeness | all 3 files referenced by `model.safetensors.index.json` present — **complete** |
| Prompt size | 1,395 tokens (system prompt + 10 few-shots + query), under the 1,800 target |

## Model load verification (real hardware)

`python -m app.scripts.test_model …` — one load per process, `local_files_only=True`:

| Mode | How selected | Load time | Latency per query (3 test queries) | Answers |
|---|---|---|---|---|
| bf16, GPU + CPU offload | no quantization, VRAM < 9 GB | 10.0 s | ~40 s (first query), 4.3 s for an empty answer | correct |
| **bf16 compute + 4-bit NF4 (default)** | `MODEL_QUANTIZATION=auto` → 4bit | 12.0 s | 5.9 s / 5.8 s / 4.3 s | correct, identical |
| bf16 compute + 8-bit | `MODEL_QUANTIZATION=8bit` | 15.3 s | 12.8 s / 12.1 s / 8.7 s | correct, identical |
| float32 on CPU | — | not attempted | — | needs ~16 GB RAM; machine has 15.7 GB total |

Decision: on this machine `MODEL_DEVICE=auto` + `MODEL_QUANTIZATION=auto` loads the 4-bit model fully on the GPU.
On a server with ≥ 9 GB free VRAM the same settings load plain bf16 on the GPU. See `docs/architecture.md`
decision 6. VRAM used by the 4-bit model is reported by `/health` logs (`vramUsedMb`) and the
`hr_ai_gpu_memory_used_bytes` metric.
