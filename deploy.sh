#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ansible lives in a venv on the server; activate it if not already on PATH
if ! command -v ansible-playbook >/dev/null 2>&1 && [ -x "$HOME/venvs/ansible/bin/ansible-playbook" ]; then
  source "$HOME/venvs/ansible/bin/activate"
fi
command -v ansible-playbook >/dev/null 2>&1 || {
  echo "error: ansible-playbook not found on PATH - activate your ansible venv first" >&2
  exit 1
}

ansible-playbook -i localhost, "$ROOT_DIR/playbooks/render_inventory.yml"
ansible-playbook -i "$ROOT_DIR/inventories/production/inventory.yml" "$ROOT_DIR/deploy.yml" "$@"
