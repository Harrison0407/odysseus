#!/usr/bin/env bash
set -euo pipefail

blocked_variables=(
  DATABASE_URL
  ODYSSEUS_DATA_DIR
  DATA_DIR
  ODYSSEUS_MAIL_ATTACHMENTS_DIR
  FASTEMBED_CACHE_PATH
  AUTH_FILE
  MEMORY_FILE
  USER_PREFS_FILE
  SETTINGS_FILE
  UPLOAD_DIR
  UPLOAD_FOLDER
  CHROMA_DIR
  CHROMA_PATH
  CHROMA_DB_PATH
  RAG_DIR
  RAG_DB_PATH
)

for variable in "${blocked_variables[@]}"; do
  if [[ -n "${!variable+x}" ]]; then
    echo "refusing inherited runtime path variable: ${variable}" >&2
    exit 64
  fi
done

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
repo_root="$(CDPATH= cd -- "${script_dir}/.." && pwd -P)"
temp_base="${TMPDIR:-/tmp}"
temp_base="$(CDPATH= cd -- "${temp_base}" && pwd -P)"
test_root="$(mktemp -d "${temp_base%/}/marketmatch-offline-inspector.XXXXXX")"
sentinel_database="${test_root}/launcher-sentinel.db"
runtime_data="${test_root}/runtime-data"
mkdir -p "${runtime_data}"
: > "${sentinel_database}"

cleanup() {
  rm -rf -- "${test_root}"
}
trap cleanup EXIT HUP INT TERM

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  else
    echo "no SHA-256 utility available" >&2
    return 69
  fi
}

before_hash="$(sha256_file "${sentinel_database}")"
before_listing="$(find "${test_root}" -mindepth 1 -maxdepth 2 -print | LC_ALL=C sort)"

export DATABASE_URL="sqlite:///${sentinel_database}"
export ODYSSEUS_DATA_DIR="${runtime_data}"
export OFFLINE_INSPECTOR_TEST_ROOT="${test_root}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0

cd "${repo_root}"
python3 -I -B -m unittest discover \
  -s tests/offline_inspector \
  -p 'test_*.py' \
  -v

after_hash="$(sha256_file "${sentinel_database}")"
after_listing="$(find "${test_root}" -mindepth 1 -maxdepth 2 -print | LC_ALL=C sort)"

if [[ "${before_hash}" != "${after_hash}" ]]; then
  echo "launcher sentinel database changed" >&2
  exit 70
fi
if [[ "${before_listing}" != "${after_listing}" ]]; then
  echo "launcher test root retained unexpected artifacts" >&2
  diff -u <(printf '%s\n' "${before_listing}") <(printf '%s\n' "${after_listing}") >&2 || true
  exit 71
fi
