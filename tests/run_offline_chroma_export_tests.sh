#!/usr/bin/env bash
set -euo pipefail

blocked_variables=(
  DATABASE_URL
  ODYSSEUS_DATA_DIR
  APP_DATA_DIR
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
  CHROMA_HOST
  CHROMA_PORT
  CHROMADB_HOST
  CHROMADB_PORT
  CHROMADB_CONNECT_TIMEOUT
  CHROMA_TENANT
  CHROMA_DATABASE
  CHROMA_SERVER_AUTH_CREDENTIALS
  RAG_DIR
  RAG_DB_PATH
  RAG_ENDPOINT
  EMAIL_CACHE_DB
  SQLITE_TMPDIR
  MARKETMATCH_CHROMA_SNAPSHOT_TEST_ROOT
  MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL
  MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL_FD
  MARKETMATCH_CHROMA_SNAPSHOT_LAUNCHER_FD
  MARKETMATCH_CHROMA_SNAPSHOT_LAUNCHER_PID
)

for variable in "${blocked_variables[@]}"; do
  if [[ -n "${!variable+x}" ]]; then
    echo "refusing inherited runtime path variable: ${variable}" >&2
    exit 64
  fi
done

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
repo_root="$(CDPATH= cd -- "${script_dir}/.." && pwd -P)"
exec 8< "${script_dir}/run_offline_chroma_export_tests.sh"
temp_base="${TMPDIR:-/tmp}"
temp_base="$(CDPATH= cd -- "${temp_base}" && pwd -P)"
test_root="$(mktemp -d "${temp_base%/}/marketmatch-chroma-snapshot.XXXXXX")"
sentinel_token="$(python3 -I -B -c 'import secrets; print(secrets.token_hex(32))')"
launcher_sentinel="${test_root}/.launcher-chroma-snapshot-sentinel"
snapshots_root="${test_root}/snapshots"
mkdir -p "${snapshots_root}"
printf '%s\n' "${sentinel_token}" > "${launcher_sentinel}"
chmod 0400 "${launcher_sentinel}"
exec 9< "${launcher_sentinel}"

cleanup() {
  chmod -R u+rwX -- "${test_root}" 2>/dev/null || true
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

before_sentinel_hash="$(sha256_file "${launcher_sentinel}")"
before_root_listing="$(find "${test_root}" -mindepth 1 -maxdepth 2 -print | LC_ALL=C sort)"

export MARKETMATCH_CHROMA_SNAPSHOT_TEST_ROOT="${test_root}"
export MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL="${sentinel_token}"
export MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL_FD=9
export MARKETMATCH_CHROMA_SNAPSHOT_LAUNCHER_FD=8
export MARKETMATCH_CHROMA_SNAPSHOT_LAUNCHER_PID="$$"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0

cd "${repo_root}"
python3 -I -B -m unittest discover \
  -s tests/offline_chroma_export \
  -p 'test_*.py' \
  -v

after_sentinel_hash="$(sha256_file "${launcher_sentinel}")"
after_root_listing="$(find "${test_root}" -mindepth 1 -maxdepth 2 -print | LC_ALL=C sort)"

if [[ "${before_sentinel_hash}" != "${after_sentinel_hash}" ]]; then
  echo "launcher Chroma snapshot sentinel changed" >&2
  exit 70
fi
if [[ "${before_root_listing}" != "${after_root_listing}" ]]; then
  echo "launcher test root retained unexpected artifacts" >&2
  diff -u <(printf '%s\n' "${before_root_listing}") \
    <(printf '%s\n' "${after_root_listing}") >&2 || true
  exit 71
fi
