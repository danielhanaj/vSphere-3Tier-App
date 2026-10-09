# agent.md — session handoff (2026-10-08)

Read **BUILD_PLAN.md** first: repo explanation, plan, and status checklist.

STATUS: **full deploy is GREEN** — `configure_vms.yml` completion gate fixed, db/app/web all reached `failed=0`, and the app UI works at `https://<web-ip>/cgi-bin/app.py`. No open blocker.

REPO SHRUNK to VM-only (see BUILD_PLAN item 15 area): removed `containers/`, `k8s/`, `images/`, Infoblox bits, `deploy_pause.yml`, per-role Galaxy boilerplate, `__pycache__`, and stale rendered templates; `collections/requirements.yml` = `community.vmware` only.

## Repo & sync model

- Agent edits here: `/mnt/c/Trash/3tier1/3-Tier-Apps` (Windows drive via WSL).
- Deploy runs on the user's server at `/home/dan/1/3-Tier-Apps` — **user syncs files manually** (ask them how; assume every changed/deleted file must be re-synced).
- Nothing is committed to git. Do not commit unless the user asks.

## Environment

- This machine is ON the lab network `192.168.0.0/24` (verified: `vcsa01.lab.local` = 192.168.0.11 ping/443 OK; VM `ansible` = 192.168.0.114).
- vCenter: `vcsa01.lab.local`, `packer@vsphere.local` / `VMware1!`, DC01 / CL01 / SATA-01 / VM_Network.
- Deployment vars/credentials live in `inventories/production/group_vars/all.yml` (gitignored, present). VM root password = `Common.Password.VMs`.
- Local tooling: venv at `/tmp/opencode/aenv` (ansible-core 2.17.14 + pyvmomi) — `/tmp` may vanish; recreate with `python3 -m venv /tmp/opencode/aenv && pip install ansible-core==2.17.14 pyvmomi`. Collections resolve from `/home/dan/.ansible`.
- Server tooling: venv `/home/dan/venvs/ansible`; `deploy.sh` now auto-activates it.

## Target state

- 5 VMs from `ovas/photon-hw15-5.0-dde71ec57.x86_64.ova` (user places file in `ovas/`): web `192.168.0.17-.19`, app `.20`, db `.30`, gateway `.1` (never user-confirmed), DNS 8.8.8.8, domain corp.local, `NameSuffix: "b"` → hostnames `vpc-*-01b`, folder `3-Tier-b`.
- **All VMs are currently undeployed** (torn down twice). Do NOT probe for them.

## Done (BUILD_PLAN checklist 1–13, all verified locally except where noted)

- IP/gateway/inventory/template alignment; `check_requirements.yml` fixed (namespace bug, OVA grep, pyvmomi check) → runs `failed=0`.
- OVA path: `{{ playbook_dir | dirname }}/ovas/...` — `playbook_dir` resolves to the *imported* file's dir (`playbooks/`), not repo root.
- `deploy.sh`: auto-activates `$HOME/venvs/ansible`.
- `configure_vms.yml`: 60s post-reboot pause; post-reboot `vmware_guest_info` wait on `toolsRunningStatus == guestToolsRunning` AND non-empty `guest.ipAddress` with explicit `fail` (console diagnostics); tdnf/enable no longer typed as separate sendkeys.

## RESOLVED/APPLIED — completion gate (BUILD_PLAN item 14)

Two concrete bugs found and fixed (code changed, **not yet verified on a deploy**):

1. **`vmware_guest_info` needs `schema: vsphere` whenever `properties:` is set.** Default `schema` is `summary`; module hard-fails with "The option 'properties' is only valid when the schema is 'vsphere'" (`vmware_guest_info.py:282`). This broke BOTH the post-reboot tools/IP guard and any `properties`-based wait — an `until` loop just retries that fatal error as `FAILED - RETRYING`. Fix: added `schema: vsphere` to the tools guard (and the new wait).
2. **Old completion signal used `vmware_guest_file_operation.fetch`** (`InitiateFileTransferFromGuest`, the fragile path; PUT copy worked, GET fetch did not). REPLACED. The typed chain now ends with `vmware-rpctool "info-set guestinfo.configure-complete yes"`, and the gate polls `config.extraConfig` (`schema: vsphere` + `properties: [config.extraConfig]`) for that key (120×15s, `ignore_errors` + explicit `fail`). tdnf output still visible on the console.
3. **LATEST RUN: the guestinfo wait still looped `FAILED - RETRYING` for all 5 VMs** although tdnf finished. ROOT CAUSE: `vmware-rpctool` takes its payload as ONE quoted argument; the chain invoked it UNQUOTED (`vmware-rpctool info-set guestinfo.configure-complete yes`), so rpctool received only `info-set` and set nothing. FIX: quote it — `vmware-rpctool "info-set guestinfo.configure-complete yes"` (sendkey supports `"`; char map `0x34` + LEFTSHIFT). The fail message now lists the guestinfo keys actually present in `config.extraConfig`.

**Also noted:** `vmware_guest_sendkey` defaults `sleep_time: 0` — it fires each USB scan code with no delay and its own comment says the sleep prevents dropping/garbling. Long typed strings can drop chars. Left at 0 for now (user saw the chain run to completion); set `sleep_time: 1` on the chain task if the tail marker proves unreliable.

**CONFIRMED:** rpctool-set guestinfo DOES surface in vCenter `config.extraConfig`, and the full deploy is GREEN (all hosts `failed=0`). `sleep_time` still 0.

## RESOLVED/APPLIED — app tier HTTP 500 (BUILD_PLAN item 15)

**Symptom:** `https://<web-ip>/cgi-bin/app.py` → HTTP 500 `End of script output before headers`.
**ROOT CAUSE (confirmed from app `/var/log/httpd/error_log` + guest shell):** guest Python is **3.14.7**; the stdlib `cgi` module was REMOVED in Python 3.13. `app.py` and the db `data.py` did `import cgi` only to parse the query string → `ModuleNotFoundError: No module named 'cgi'`. `requests` was present.
**FIX:** replaced `cgi.FieldStorage()` / `form.getvalue("querystring")` with `urllib.parse.parse_qs(os.getenv("QUERY_STRING",""))` in `roles/app/templates/app.j2` and `roles/db/files/data.py`. Applied via `playbooks/install_config_app.yml` + `playbooks/install_config_db.yml` (no redeploy/reboot).
**Access model confirmed:** `http://<web-ip>/` = stock nginx welcome; app on HTTPS 443 (`webapp.corp.local`) → proxy to app:8443; app root `/` = stock Apache "It works!"; the real UI is `/cgi-bin/app.py`.

## Background root cause (confirmed, do not re-litigate)

Original failure (tools + ssh dead after configure reboot): sendkey-typed tdnf was interrupted/left half-done by the reboot → corrupted openssh + open-vm-tools on disk. User's manual `tdnf update` at console repaired both VMs. Any fix must guarantee the package transaction completes before the reboot sendkey.

## Rules learned

- Minimal changes: no container/k8s/roles logic unless required; Photon = `tdnf` (no apt), ssh unit = `sshd`.
- Sendkey is write-only (no console read); `vmware_vm_shell` output invisible on console; sendkey has no length cap but fails loudly on unsupported chars.
- Don't manually fix the user's existing VMs; stop when asked; keep BUILD_PLAN.md updated; no commits unless asked.

## Verification commands

```bash
ansible-playbook check_requirements.yml        # expect failed=0
bash -n deploy.sh
ansible-playbook --syntax-check playbooks/configure_vms.yml
./deploy.sh
```

## Resume checklist

1. Ask user to sync changed files to `/home/dan/1/3-Tier-Apps` (list: `playbooks/configure_vms.yml`, `playbooks/deploy_ova_vms.yml`, `deploy.sh`, `check_requirements.yml`, `ansible.cfg`, inventory/group_vars/host_vars, `playbooks/ssh_cleanup.yml`, `playbooks/templates/inventory.yml.j2`).
2. Redeploy → confirm the guestinfo gate passes (i.e. rpctool-set guestinfo surfaces in `config.extraConfig`); if it still times out, capture the module output and switch the gate to the `vmware_vm_shell` `test -f` probe.
3. Get past configure → verify db/app/web stages → full `failed=0` run.
4. Update BUILD_PLAN checklist; commit only if asked.
