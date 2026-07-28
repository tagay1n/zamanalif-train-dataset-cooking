#!/usr/bin/env bash
set -euo pipefail

readonly REVISION="18fe9e45d5672d6f6113291197449e7522df1b3e"
readonly REPOSITORY="https://github.com/apertium/apertium-tat.git"
readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly TARGET="${ROOT}/.tools/apertium-tat"

required=(git autoconf automake make pkg-config hfst-lexc lt-comp lt-proc)
missing=()
for command in "${required[@]}"; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    missing+=("${command}")
  fi
done
if ((${#missing[@]})); then
  printf 'Missing Apertium build tools: %s\n' "${missing[*]}" >&2
  printf 'On Ubuntu, install them with: sudo apt install apertium-all-dev\n' >&2
  exit 1
fi

if [[ -d "${TARGET}/.git" ]]; then
  git -C "${TARGET}" fetch --depth 1 origin "${REVISION}"
else
  mkdir -p "$(dirname "${TARGET}")"
  git clone --no-checkout "${REPOSITORY}" "${TARGET}"
  git -C "${TARGET}" fetch --depth 1 origin "${REVISION}"
fi
git -C "${TARGET}" checkout --detach --force "${REVISION}"

(
  cd "${TARGET}"
  ./autogen.sh
  make -j"$(getconf _NPROCESSORS_ONLN)"
)

test -s "${TARGET}/tat.automorf.bin"
printf 'Apertium-tat ready: %s\n' "${TARGET}/tat.automorf.bin"
printf 'Revision: %s\n' "${REVISION}"
