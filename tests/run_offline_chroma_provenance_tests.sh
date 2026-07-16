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
  TMPDIR
  TMP
  TEMP
  XDG_CACHE_HOME
  XDG_CONFIG_HOME
  XDG_DATA_HOME
  XDG_RUNTIME_DIR
  XDG_STATE_HOME
  LD_LIBRARY_PATH
  DYLD_LIBRARY_PATH
  DYLD_FALLBACK_LIBRARY_PATH
  DOCKER_HOST
  DOCKER_CONTEXT
  DOCKER_CONFIG
  COMPOSE_FILE
  COMPOSE_PROJECT_NAME
  CONTAINER_HOST
  HTTP_PROXY
  HTTPS_PROXY
  ALL_PROXY
  NO_PROXY
  http_proxy
  https_proxy
  all_proxy
  no_proxy
  REQUESTS_CA_BUNDLE
  CURL_CA_BUNDLE
  SSL_CERT_FILE
  SSL_CERT_DIR
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_SESSION_TOKEN
  AWS_PROFILE
  AWS_CONFIG_FILE
  AWS_SHARED_CREDENTIALS_FILE
  GOOGLE_APPLICATION_CREDENTIALS
  GOOGLE_CLOUD_PROJECT
  CLOUDSDK_CONFIG
  AZURE_CLIENT_ID
  AZURE_CLIENT_SECRET
  AZURE_TENANT_ID
  AZURE_SUBSCRIPTION_ID
  KUBECONFIG
  AUTH_ENABLED
  LOCALHOST_BYPASS
  ODYSSEUS_ADMIN_USER
  ODYSSEUS_ADMIN_PASSWORD
  PYTHONPATH
  PYTHONHOME
  VIRTUAL_ENV
  MARKETMATCH_CHROMA_PROVENANCE_TEST_ROOT
  MARKETMATCH_CHROMA_PROVENANCE_SENTINEL
  MARKETMATCH_CHROMA_PROVENANCE_SENTINEL_FD
  MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_FD
  MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_PID
  MARKETMATCH_CHROMA_PROVENANCE_TEMP_BOUNDARY
)

for variable in "${blocked_variables[@]}"; do
  if [[ -n "${!variable+x}" ]]; then
    echo "refusing inherited runtime variable: ${variable}" >&2
    exit 64
  fi
done

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
repo_root="$(CDPATH= cd -- "${script_dir}/.." && pwd -P)"
exec 8< "${script_dir}/run_offline_chroma_provenance_tests.sh"
temp_base="/tmp"
temp_base="$(CDPATH= cd -- "${temp_base}" && pwd -P)"
test_root="$(mktemp -d "${temp_base%/}/marketmatch-chroma-provenance.XXXXXX")"
sentinel_token="$(python3 -I -B -c 'import secrets; print(secrets.token_hex(32))')"
launcher_sentinel="${test_root}/.launcher-chroma-provenance-sentinel"
fixtures_root="${test_root}/fixtures"
mkdir -p "${fixtures_root}"
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

export MARKETMATCH_CHROMA_PROVENANCE_TEST_ROOT="${test_root}"
export MARKETMATCH_CHROMA_PROVENANCE_SENTINEL="${sentinel_token}"
export MARKETMATCH_CHROMA_PROVENANCE_SENTINEL_FD=9
export MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_FD=8
export MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_PID="$$"
export MARKETMATCH_CHROMA_PROVENANCE_TEMP_BOUNDARY="${temp_base}"
export PYTHONDONTWRITEBYTECODE=1

cd "${repo_root}"
python3 -I -B -m unittest discover \
  -s tests/offline_chroma_provenance \
  -p 'test_*.py' \
  -v

after_sentinel_hash="$(sha256_file "${launcher_sentinel}")"
after_root_listing="$(find "${test_root}" -mindepth 1 -maxdepth 2 -print | LC_ALL=C sort)"

if [[ "${before_sentinel_hash}" != "${after_sentinel_hash}" ]]; then
  echo "launcher Chroma provenance sentinel changed" >&2
  exit 70
fi
if [[ "${before_root_listing}" != "${after_root_listing}" ]]; then
  echo "launcher test root retained unexpected artifacts" >&2
  diff -u <(printf '%s\n' "${before_root_listing}") \
    <(printf '%s\n' "${after_root_listing}") >&2 || true
  exit 71
fi
