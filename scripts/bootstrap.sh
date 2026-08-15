#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${project_dir}/.venv"

python3 -m venv "${venv_dir}"
"${venv_dir}/bin/python" -m pip install --upgrade pip
"${venv_dir}/bin/python" -m pip install -r "${project_dir}/requirements-dev.txt"

echo "Environment ready. Activate it with:"
echo "  source ${venv_dir}/bin/activate"
