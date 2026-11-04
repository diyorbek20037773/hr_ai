#!/usr/bin/env bash
# hr_ai one-command start for Linux and macOS: detects the hardware, builds the matching image, downloads the model
# once, starts the API and the dashboards. The Windows twin is start.ps1 (keep the two in sync).
#
#   ./start.sh                   start (first run: image build + ~8 GB model download)
#   ./start.sh --cpu             force CPU even when a GPU exists
#   ./start.sh --download-model  ignore a local ./model and use the Hugging Face copy in a Docker volume
#   ./start.sh stop | status | logs
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ENV_FILE=.env.docker
PORT="${HR_AI_PORT:-8001}"
GRAFANA_PORT="${HR_AI_GRAFANA_PORT:-3001}"
MIN_RAM_CPU_MIB=9500  # bf16 weights ~8 GB; a Docker VM set to 10 GB reports about 9.7 GiB
MIN_VRAM_MB=4500      # 4-bit model ~2.8 GB + ~2 GB transient while generating
READY_TIMEOUT=900

ACTION=up FORCE_CPU=0 FORCE_DOWNLOAD=0
for arg in "$@"; do
  case "$arg" in
    up|stop|status|logs) ACTION="$arg" ;;
    --cpu) FORCE_CPU=1 ;;
    --download-model) FORCE_DOWNLOAD=1 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Noma'lum parametr: $arg (./start.sh --help)" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mDIQQAT:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mXATO:\033[0m %s\n' "$*" >&2; exit 1; }

compose() {
  docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f "docker/compose.${OVERLAY}.yml" "$@"
}

load_state() {
  [ -f "$ENV_FILE" ] || die "$ENV_FILE topilmadi. Avval ./start.sh ni ishga tushiring."
  OVERLAY="$(sed -n 's/^HR_AI_OVERLAY=//p' "$ENV_FILE")"
  PORT="$(sed -n 's/^HR_AI_PORT=//p' "$ENV_FILE")"
}

check_compose_version() {
  local version major minor
  version="$(docker compose version --short 2>/dev/null || true)"
  version="${version#v}"
  major="${version%%.*}"; minor="${version#*.}"; minor="${minor%%.*}"
  case "$major$minor" in ''|*[!0-9]*) die "Docker Compose v2.24+ kerak: https://docs.docker.com/compose/install/" ;; esac
  if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 24 ]; }; then
    die "Docker Compose $version eski, v2.24+ kerak: https://docs.docker.com/compose/install/"
  fi
}

ensure_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker o'rnatilmagan: https://docs.docker.com/get-docker/"
  command -v curl >/dev/null 2>&1 || die "curl o'rnatilmagan. Ubuntu/Debian: sudo apt install curl"
  check_compose_version
  if ! docker info >/dev/null 2>&1; then
    if [ "$(uname -s)" = Darwin ]; then
      info "Docker Desktop ishga tushirilmoqda..."
      open -a Docker || true
      for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && return 0; sleep 3; done
    fi
    die "Docker daemon ishlamayapti. Linux: sudo systemctl start docker (va foydalanuvchi docker guruhida bo'lsin)."
  fi
}

detect_hardware() {
  ACCEL=cpu OVERLAY=cpu TORCH_INDEX=cpu TORCH_VERSION=2.11.0 INSTALL_BNB=0
  MODEL_DEVICE=cpu MODEL_DTYPE=bfloat16 MODEL_QUANTIZATION=none LLM_TIMEOUT=600 HW="CPU"
  [ "$FORCE_CPU" = 1 ] && return 0

  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    local line cuda major minor num vram
    line="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | head -1)"
    vram="${line##*, }"
    cuda="$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9]*\.[0-9]*\).*/\1/p' | head -1)"
    major="${cuda%%.*}"; minor="${cuda##*.}"; num=$(( major * 100 + minor ))
    if   [ "$num" -ge 1208 ]; then TORCH_INDEX=cu128 TORCH_VERSION=2.11.0
    elif [ "$num" -ge 1206 ]; then TORCH_INDEX=cu126 TORCH_VERSION=2.11.0
    elif [ "$num" -ge 1108 ]; then TORCH_INDEX=cu118 TORCH_VERSION=2.7.1
    else die "NVIDIA drayveri juda eski (CUDA $cuda). Drayverni yangilang: https://www.nvidia.com/drivers"
    fi
    ACCEL="$TORCH_INDEX" OVERLAY=nvidia INSTALL_BNB=1
    MODEL_DEVICE=auto MODEL_DTYPE=auto MODEL_QUANTIZATION=auto LLM_TIMEOUT=60
    HW="NVIDIA ${line%%,*}, ${vram} MiB VRAM, CUDA $cuda"
    if [ "$vram" -lt "$MIN_VRAM_MB" ]; then
      # The 4-bit model would run out of memory: split it between GPU and RAM instead (slow, but it works).
      warn "VRAM ${vram} MiB kam (${MIN_VRAM_MB} MiB dan past): model GPU va RAM orasida bo'linadi, javoblar sekinroq bo'ladi."
      INSTALL_BNB=0 MODEL_QUANTIZATION=none LLM_TIMEOUT=180
    fi
    return 0
  fi

  if [ "$(uname -s)" = Linux ] && [ -e /dev/kfd ]; then
    TORCH_INDEX=rocm7.0 TORCH_VERSION=2.10.0 ACCEL=rocm70 OVERLAY=amd
    MODEL_DEVICE=auto MODEL_DTYPE=auto MODEL_QUANTIZATION=none LLM_TIMEOUT=120
    HW="AMD GPU (ROCm)"
    return 0
  fi

  if [ "$(uname -s)" = Darwin ]; then
    warn "macOS: Docker konteynerlari Apple GPU'siga kira olmaydi, model CPU'da ishlaydi (sekin)."
  else
    warn "Mos GPU topilmadi: model CPU'da ishlaydi (sekin, bir so'rov bir necha daqiqa)."
  fi
}

check_memory() {
  local mem_mib
  mem_mib=$(( $(docker info --format '{{.MemTotal}}') / 1024 / 1024 ))
  [ "$mem_mib" -lt "$MIN_RAM_CPU_MIB" ] || return 0
  if [ "$OVERLAY" = cpu ]; then
    die "Docker'ga ${mem_mib} MiB RAM berilgan, CPU rejimi uchun kamida 10 GB kerak. Docker Desktop > Settings > Resources."
  elif [ "$MODEL_QUANTIZATION" = none ]; then
    warn "Docker'ga ${mem_mib} MiB RAM berilgan: model GPU va RAM orasida bo'linadi, 10 GB tavsiya etiladi."
  fi
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

check_ports() {
  local own p
  own="$(docker ps --filter label=com.docker.compose.project=hr_ai --format '{{.Ports}}')"
  for p in "$PORT" "$GRAFANA_PORT"; do
    case "$own" in *":$p->"*) continue ;; esac
    if port_busy "$p"; then
      die "$p-port band. Boshqa port bilan: HR_AI_PORT=8011 HR_AI_GRAFANA_PORT=3011 ./start.sh"
    fi
  done
}

write_env() {
  local src="" mode=rw readonly=0
  if [ "$FORCE_DOWNLOAD" = 0 ] && [ -f model/config.json ]; then
    src=./model mode=ro readonly=1
    info "Mavjud ./model ishlatiladi (faqat o'qish uchun), qayta yuklanmaydi."
  fi
  cat > "$ENV_FILE" <<EOF
# Generated by start.sh for this machine. Do not commit.
HR_AI_OVERLAY=$OVERLAY
HR_AI_ACCEL=$ACCEL
TORCH_INDEX=$TORCH_INDEX
TORCH_VERSION=$TORCH_VERSION
INSTALL_BNB=$INSTALL_BNB
MODEL_DEVICE=$MODEL_DEVICE
MODEL_DTYPE=$MODEL_DTYPE
MODEL_QUANTIZATION=$MODEL_QUANTIZATION
LLM_TIMEOUT_SECONDS=$LLM_TIMEOUT
HR_AI_PORT=$PORT
HR_AI_GRAFANA_PORT=$GRAFANA_PORT
HR_AI_MODEL_SOURCE=${src:-hr_ai_models}
HR_AI_MODEL_MODE=$mode
HR_AI_MODEL_READONLY=$readonly
HR_AI_RENDER_GID=$(getent group render 2>/dev/null | cut -d: -f3 || true)
HR_AI_VIDEO_GID=$(getent group video 2>/dev/null | cut -d: -f3 || true)
EOF
}

gpu_preflight() {
  # Fail before the 8 GB download when Docker cannot actually reach the GPU.
  case "$OVERLAY" in
    nvidia)
      docker run --rm --gpus all --entrypoint python "hr_ai:$ACCEL" -c \
        "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" || die \
        "GPU konteynerga ko'rinmayapti. Linux: nvidia-container-toolkit o'rnating (https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), Windows: Docker Desktop WSL2 rejimida bo'lsin. GPU'siz davom etish: ./start.sh --cpu" ;;
    amd)
      compose run --rm --no-deps --entrypoint python api -c \
        "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" || die \
        "AMD GPU konteynerga ko'rinmayapti: amdgpu drayverini va GPU'ning ROCm ro'yxatida borligini tekshiring. GPU'siz davom etish: ./start.sh --cpu" ;;
  esac
}

wait_ready() {
  local i health cid restarts
  info "Model yuklanmoqda, /ready kutilmoqda..."
  for i in $(seq 1 $(( READY_TIMEOUT / 3 ))); do
    if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/ready" 2>/dev/null; then return 0; fi
    health="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"
    case "$health" in *'"modelStatus":"failed"'*) break ;; esac
    # Crash loop, usually out of memory: do not wait the full timeout.
    cid="$(compose ps -q api 2>/dev/null || true)"
    restarts="$(docker inspect -f '{{.RestartCount}}' "$cid" 2>/dev/null || echo 0)"
    [ "${restarts:-0}" -lt 2 ] || break
    sleep 3
  done
  compose logs --tail 40 api || true
  die "API tayyor bo'lmadi. Yuqoridagi logga qarang."
}

report() {
  local health
  health="$(curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health")"
  case "$health" in
    *'"device":"cuda'*) ;;
    *) [ "$OVERLAY" = cpu ] || warn "GPU kutilgan edi, lekin model CPU'da ishlayapti: $health" ;;
  esac
  local running service
  running="$(compose ps --status running --services 2>/dev/null || true)"
  for service in prometheus grafana; do
    case "$running" in *"$service"*) ;; *) warn "$service ishlamayapti: ./start.sh logs yoki docker compose logs $service" ;; esac
  done
  echo
  info "Tayyor. Qurilma: $HW"
  echo "    Dashboard + prompt tahriri: http://127.0.0.1:$PORT/"
  echo "    Monitoring (Grafana):       http://127.0.0.1:$GRAFANA_PORT/"
  echo "    API hujjatlari (Swagger):   http://127.0.0.1:$PORT/docs"
  echo "    To'xtatish: ./start.sh stop | Holat: ./start.sh status | Log: ./start.sh logs"
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$PORT/" || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:$PORT/" >/dev/null 2>&1 || true
  fi
}

ensure_docker
case "$ACTION" in
  stop)   load_state; compose stop; exit 0 ;;
  status) load_state; compose ps; curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" || true; echo; exit 0 ;;
  logs)   load_state; compose logs -f --tail 100 api; exit 0 ;;
esac

detect_hardware
info "Aniqlangan qurilma: $HW (torch $TORCH_VERSION, $TORCH_INDEX)"
check_memory
check_ports
write_env
info "Image qurilmoqda (birinchi marta 10-40 daqiqa, internetga bog'liq)..."
if ! compose build api; then
  docker image inspect "hr_ai:$ACCEL" >/dev/null 2>&1 || die "Image qurilmadi: internetni tekshirib qayta urinib ko'ring."
  warn "Image yangilanmadi (internet?), mavjud image ishlatiladi."
fi
gpu_preflight
info "Model tekshirilmoqda (kerak bo'lsa Hugging Face'dan yuklanadi)..."
compose run --rm --no-deps model-init
info "Konteynerlar ko'tarilmoqda..."
compose up -d
wait_ready
report
