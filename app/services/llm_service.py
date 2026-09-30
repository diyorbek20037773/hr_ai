"""Local LLM service. The Qwen model is loaded ONCE per process (FastAPI lifespan) — CLAUDE.md §3.4.

Only local files are used (`local_files_only=True`); nothing is ever downloaded (CLAUDE.md §3.1-3.2).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.logging import get_logger
from app.core.metrics import GPU_MEMORY_USED, LLM_DURATION, LLM_QUEUE_DEPTH, MODEL_LOADED

log = get_logger(__name__)

_TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json")


class ModelNotFoundError(RuntimeError):
    pass


class LLMTimeoutError(TimeoutError):
    pass


class LLMBusyError(RuntimeError):
    """Queue limit (LLM_MAX_QUEUE) reached - reject instead of piling up waiting threads."""


@runtime_checkable
class BaseLLMService(Protocol):
    @property
    def is_loaded(self) -> bool: ...

    @property
    def device(self) -> str: ...

    @property
    def dtype(self) -> str: ...

    @property
    def model_path(self) -> str: ...

    def generate(self, messages: list[dict[str, str]]) -> str: ...


# --------------------------------------------------------------------------- model discovery

def detect_model_dir(settings: Settings) -> Path:
    """MODEL_PATH if set, else ./model, else ./model/Qwen3-4B-Instruct-2507. Verifies completeness."""
    candidates: list[Path] = []
    if settings.MODEL_PATH:
        candidates.append(settings.resolve_path(settings.MODEL_PATH))
    else:
        candidates += [PROJECT_ROOT / "model", PROJECT_ROOT / "model" / "Qwen3-4B-Instruct-2507"]

    for path in candidates:
        if (path / "config.json").is_file() and all((path / f).is_file() for f in _TOKENIZER_FILES):
            verify_model_files(path)
            return path
    raise ModelNotFoundError(
        "No local Qwen model found. Checked: " + ", ".join(str(c) for c in candidates)
        + ". Each must contain config.json, tokenizer.json, tokenizer_config.json. Nothing will be downloaded."
    )


def verify_model_files(path: Path) -> None:
    index = path / "model.safetensors.index.json"
    if index.is_file():
        weight_map = json.loads(index.read_text(encoding="utf-8")).get("weight_map", {})
        missing = sorted({f for f in weight_map.values() if not (path / f).is_file()})
        if missing:
            raise ModelNotFoundError(
                f"Model at {path} is incomplete; missing weight files: {', '.join(missing)}. "
                "Restore them manually — this service never downloads models."
            )
    elif not (path / "model.safetensors").is_file():
        raise ModelNotFoundError(f"Model at {path} has no safetensors weights.")


# --------------------------------------------------------------------------- Qwen

class QwenLLMService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._model = None
        self._tokenizer = None
        self._model_path = ""
        self._device = "unloaded"
        self._dtype = "unloaded"
        self._lock = threading.Lock()
        self._waiting = 0
        self._waiting_lock = threading.Lock()
        self.load_seconds: float | None = None

    # -- properties
    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def device(self) -> str:
        return self._device

    @property
    def dtype(self) -> str:
        return self._dtype

    @property
    def model_path(self) -> str:
        return self._model_path

    # -- loading
    def _choose_device(self, torch) -> tuple[str, str, dict | None]:
        """Returns (device label, quantization, max_memory for offload or None).

        auto: full-precision GPU if free VRAM >= MODEL_MIN_FULL_GPU_VRAM_GB; otherwise, on CUDA, a bitsandbytes
        quantized model fully on the GPU (MODEL_QUANTIZATION=auto -> 4bit) when bitsandbytes is installed, else
        GPU+CPU offload (very slow: weights stream over PCIe every token). No CUDA -> CPU.
        float32 on CPU needs ~16 GB RAM for this 4B model.
        """
        s = self.settings
        want = s.MODEL_DEVICE.lower()
        quant = s.MODEL_QUANTIZATION.lower()
        cuda = torch.cuda.is_available()
        if want == "cpu" or not cuda:
            if want == "cuda":
                raise RuntimeError("MODEL_DEVICE=cuda but CUDA is not available")
            if want == "auto":
                log.warning("CUDA not available — running on CPU (slow)")
            return "cpu", "none", None
        free, _total = torch.cuda.mem_get_info()
        free_gb = free / 1024**3
        if quant in ("4bit", "8bit"):
            return "cuda", quant, None
        if free_gb >= s.MODEL_MIN_FULL_GPU_VRAM_GB or (want == "cuda" and quant == "none"):
            return "cuda", "none", None
        if quant == "auto" and _bitsandbytes_available():
            log.warning("Free VRAM below full-precision threshold — loading 4-bit quantized model on GPU",
                        extra={"freeVramGb": round(free_gb, 2), "thresholdGb": s.MODEL_MIN_FULL_GPU_VRAM_GB})
            return "cuda", "4bit", None
        budget = s.MODEL_GPU_MAX_MEMORY_GB or max(1.0, round(free_gb - 1.2, 1))
        log.warning(
            "Free VRAM below full-GPU threshold — using GPU+CPU offload (slow)",
            extra={"freeVramGb": round(free_gb, 2), "thresholdGb": s.MODEL_MIN_FULL_GPU_VRAM_GB,
                   "gpuBudgetGb": budget},
        )
        return "cuda+cpu-offload", "none", {0: f"{budget}GiB", "cpu": "24GiB"}

    def _choose_dtype(self, torch, device: str):
        name = self.settings.MODEL_DTYPE.lower()
        if name == "auto":
            if device.startswith("cuda"):
                return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            return torch.float32
        return {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[name]

    def load(self) -> None:
        if self.is_loaded:
            log.info("Model already loaded — skipping")
            return
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer

        log.info("Detecting local Qwen model…")
        path = detect_model_dir(self.settings)
        self._model_path = str(path)
        device, quant, max_memory = self._choose_device(torch)
        dtype = self._choose_dtype(torch, device)
        log.info("Model found", extra={"modelPath": str(path), "device": device, "dtype": str(dtype),
                                       "quantization": quant, "transformers": transformers.__version__,
                                       "torch": torch.__version__})
        if device == "cpu":
            torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))

        t0 = time.perf_counter()
        log.info("Loading tokenizer…")
        self._tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        log.info("Loading model…")
        kwargs: dict = {"local_files_only": True, "low_cpu_mem_usage": True}
        kwargs["dtype" if _accepts_dtype_kw(transformers) else "torch_dtype"] = dtype
        if quant in ("4bit", "8bit"):
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = (
                BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=dtype,
                                   bnb_4bit_use_double_quant=False)
                if quant == "4bit" else BitsAndBytesConfig(load_in_8bit=True)
            )
            kwargs["device_map"] = {"": 0}
        elif device == "cuda":
            kwargs["device_map"] = {"": 0}
        elif device == "cuda+cpu-offload":
            kwargs["device_map"] = "auto"
            kwargs["max_memory"] = max_memory
        model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
        model.eval()
        self._model = model
        self._device = device
        self._dtype = str(dtype).replace("torch.", "") + (f"+{quant}" if quant != "none" else "")
        self.load_seconds = round(time.perf_counter() - t0, 1)
        vram = torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
        GPU_MEMORY_USED.set(vram)
        MODEL_LOADED.set(1)
        log.info("Model loaded", extra={"loadSeconds": self.load_seconds, "device": device, "dtype": self._dtype,
                                        "vramUsedMb": round(vram / 1024**2)})

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        MODEL_LOADED.set(0)
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    # -- inference
    def _render(self, messages: list[dict[str, str]]) -> str:
        return self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    def render_prompt(self, messages: list[dict[str, str]]) -> str:
        """The exact text fed to the model (Qwen chat template applied)."""
        return self._render(messages)

    def count_tokens(self, messages: list[dict[str, str]]) -> int:
        return len(self._tokenizer(self._render(messages))["input_ids"])

    def generate(self, messages: list[dict[str, str]]) -> str:
        if not self.is_loaded:
            raise RuntimeError("model is not loaded")
        import torch

        with self._waiting_lock:
            if self._waiting >= self.settings.LLM_MAX_QUEUE:
                raise LLMBusyError(f"{self._waiting} requests already queued")
            self._waiting += 1
            LLM_QUEUE_DEPTH.set(self._waiting)
        try:
            if not self._lock.acquire(timeout=self.settings.LLM_TIMEOUT_SECONDS):
                raise LLMTimeoutError("timed out waiting for the model")
            try:
                deadline = time.monotonic() + self.settings.LLM_TIMEOUT_SECONDS
                t0 = time.perf_counter()
                text = self._render(messages)
                inputs = self._tokenizer([text], return_tensors="pt").to(self._model.device)
                eos = self._tokenizer.eos_token_id
                gen_kwargs = dict(
                    max_new_tokens=self.settings.MODEL_MAX_NEW_TOKENS,
                    do_sample=False,
                    temperature=None, top_p=None, top_k=None,
                    repetition_penalty=self.settings.MODEL_REPETITION_PENALTY,
                    pad_token_id=eos,
                    stopping_criteria=_deadline_criteria(deadline),
                )
                try:
                    with torch.inference_mode():
                        out = self._model.generate(**inputs, **gen_kwargs)
                finally:
                    LLM_DURATION.observe(time.perf_counter() - t0)
                new_tokens = out[0][inputs["input_ids"].shape[1]:]
                result = self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
                if time.monotonic() > deadline:
                    raise LLMTimeoutError(f"generation exceeded {self.settings.LLM_TIMEOUT_SECONDS}s")
                return result
            finally:
                # Return cached-but-unused CUDA blocks after every call. Without this the allocator cache grows with
                # varying prompt lengths past the physical VRAM of small GPUs, and Windows (WDDM) silently spills to
                # shared system memory: measured 6 GB RTX 3050 -> 7.4 GB reserved, single calls jumping 6 s -> 40 s.
                try:
                    if self._device.startswith("cuda"):
                        torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001 — never let cache cleanup keep the lock held
                    log.warning("cuda_empty_cache_failed")
                finally:
                    self._lock.release()
        finally:
            with self._waiting_lock:
                self._waiting -= 1
                LLM_QUEUE_DEPTH.set(self._waiting)


def _bitsandbytes_available() -> bool:
    try:
        import bitsandbytes  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure means "not usable"
        return False
    return True


def _accepts_dtype_kw(transformers) -> bool:
    parts = transformers.__version__.split(".")
    return (int(parts[0]), int(parts[1])) >= (4, 56)


def _deadline_criteria(deadline: float):
    from transformers import StoppingCriteria, StoppingCriteriaList

    class _Deadline(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            return time.monotonic() > deadline

    return StoppingCriteriaList([_Deadline()])


# --------------------------------------------------------------------------- fake (tests)

class FakeLLMService:
    """Deterministic stand-in for tests: returns canned outputs keyed by the last user message."""

    def __init__(self, responses: dict[str, str | list[str]] | None = None, default: str = "{}"):
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[list[dict[str, str]]] = []
        self._served: dict[str, int] = {}

    is_loaded = True
    device = "fake"
    dtype = "none"
    model_path = "fake://model"
    load_seconds = 0.0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        key = user_msgs[-1] if user_msgs else ""
        # corrective retry: look up by the original query (the user message before the retry instruction)
        if key not in self.responses and len(user_msgs) >= 2 and user_msgs[-2] in self.responses:
            key = user_msgs[-2]
        value = self.responses.get(key, self.default)
        if isinstance(value, list):
            n = self._served.get(key, 0)
            self._served[key] = n + 1
            return value[min(n, len(value) - 1)]
        return value
