# BUILD PLAN — 3-Tier-Apps (VM-only, flat network, local OVA)

This file is the working document for agents/operators. Part 1 explains what this
repo does. Part 2 is the approved, minimal-change build plan for the target
deployment described below.

---

## Part 1 — What this repo does

Ansible automation that deploys a classic 3-tier web app as Photon OS VMs in
vCenter, then configures the guests **through VMware Guest Operations only**
(no SSH role execution; `connection: local` everywhere).

### Tiers

| Tier | Software | Ports |
|------|----------|-------|
| web  | NGINX, TLS cert, reverse proxy | 80/443, proxies to app `:8443` |
| app  | Apache httpd + CGI `app.py` (planet UI), talks to db by hostname | 8443 (TLS), CGI on 80 |
| db   | Apache httpd + CGI `data.py` + SQLite `clients.db` | 80 |

### Deploy flow

```
./deploy.sh
  1. ansible-playbook -i localhost, playbooks/render_inventory.yml
       renders inventories/production/inventory.yml from inventories/production/group_vars/all.yml
       (Common.hostnames.* → inventory host aliases)
  2. ansible-playbook -i inventories/production/inventory.yml deploy.yml
       deploy.yml imports, in order:
         playbooks/ssh_cleanup.yml       (purge known_hosts entries)
         playbooks/deploy_ova_vms.yml    (vcenter_folder + vmware_deploy_ovf per host)
         playbooks/configure_vms.yml     (console sendkey walkthrough: root pw,
                                          hostname, static network file, /etc/hosts,
                                          iptables, Photon repo fix, open-vm-tools, reboot)
         playbooks/install_config_db.yml (role db)
         playbooks/install_config_app.yml(role app)
         playbooks/install_config_web.yml(role web)
./undeploy.sh
  re-renders inventory, removes VMs from vCenter, deletes rendered
  playbooks/templates/*-hosts and *-10-static-eth0.network files
```

### Key mechanics

- **Naming model**: `NameSuffix` (top-level var, e.g. `b`) drives hostnames
  (`vpc-web-01b`…), and default vCenter folder `3-Tier-<suffix>`.
  `playbooks/tasks/resolve_target_vm_name.yml` maps inventory aliases
  (`web-01`/`vpc-web-01b`) to the VM name used by VMware modules.
- **IP model**: `ansible_host` = `{{ Common.BaseNetwork.IPv4 }}.{{ Common.SiteCode }}.<host-octet>`
  (see `playbooks/templates/inventory.yml.j2`). Web gateways/IPs live in
  `group_vars/web.yml`, app in `group_vars/app.yml`, db in `group_vars/db.yml`.
- **PortGroups**: `group_vars/{web,app,db}.yml` set `PortGroup: "{{ Common.PortGroups.<tier> }}"`;
  `deploy_ova_vms.yml` passes it to `vmware_deploy_ovf` (`networks: {"None": ...}`).
- **Static network**: `configure_vms.yml` renders `templates/10-static-eth0.network.j2`
  (`Address={{ansible_host}}/24`, `Gateway={{gateway}}`) and `templates/hosts.j2`
  (all tiers' IP↔hostname map) and copies them into each guest via
  `vmware_guest_file_operation`, then restarts systemd-networkd.
- **Cross-tier addressing**: app resolves db via `Common.hostnames.db` in
  `roles/app/templates/app.j2`; web proxies to `Common.hostnames.app` in
  `roles/web/templates/webapp.conf`. Both rely on `/etc/hosts` entries generated
  from `ansible_host`, so changing IPs in the inventory flows through everywhere.
- **Secrets**: `inventories/production/group_vars/all.yml` is gitignored;
  only `all.yml.template` (CHANGE_ME placeholders) is committed.
- **VM-only repo (shrunk)**: `containers/`, `k8s/`, `images/`, Infoblox
  (`test_infoblox_dns.yml`, `infoblox.yml.template`, `nios_modules`), `deploy_pause.yml`,
  per-role Galaxy boilerplate (`.travis.yml`, role `README.md`, `tests/`, `meta/`,
  empty `handlers/`/`defaults/`/`vars/`), `db/files/__pycache__/`, and the stale rendered
  templates (`app-01-*`, `db-01-*`, `web-0*-*`, `seg-*`, `app-01a.conf`) were REMOVED.
  `collections/requirements.yml` now lists only `community.vmware`; `playbooks/.gitignore`
  ignores rendered `*-hosts` / `*-10-static-eth0.network`.
- `check_requirements.yml` is a preflight playbook (includes a grep check that
  `deploy_ova_vms.yml` contains an `ova:` path).

---

## Part 2 — Approved build plan

### Goal

Deploy the 3 tiers **as VMs only** onto **one flat network** using a **locally
stored Photon OS OVA** in the `ovas/` directory on the Ansible server.

### Target end state

| VM name (default suffix `b`) | IP | Portgroup |
|---|---|---|
| vpc-web-01b | 192.168.0.17 | VM_Network |
| vpc-web-02b | 192.168.0.18 | VM_Network |
| vpc-web-03b | 192.168.0.19 | VM_Network |
| vpc-app-01b | 192.168.0.20 | VM_Network |
| vpc-db-01b  | 192.168.0.30 | VM_Network |

- Gateway for all tiers: `192.168.0.1` (assumption — only written into
  systemd-networkd config; L2 intra-subnet traffic does not depend on it)
- vCenter: `vcsa01.lab.local` / user `packer@vsphere.local` / password `VMware1!`
- Datacenter `DC01`, cluster `CL01`, datastore `SATA-01`, folder `3-Tier-b`
- VM root password (set via console sendkey flow): `VMware1!VMware1!`
- OVA: `ovas/photon-hw15-5.0-dde71ec57.x86_64.ova` (repo-root relative)
- DNS stays `8.8.8.8`, domain `corp.local`, hostnames stay default `vpc-*-01b`
- Container/K8s assets untouched; deploy/undeploy scripts and `deploy.yml` untouched

### Changes (minimal — 8 files, 1 new)

1. **NEW `inventories/production/group_vars/all.yml`** (gitignored local config;
   leave `all.yml.template` unchanged):
   - `NameSuffix: "b"`
   - `Common.SiteCode: 0`
   - `Common.Password.Physical: "VMware1!"`, `Common.Password.VMs: "VMware1!VMware1!"`
   - `Common.BaseNetwork.IPv4: '192.168'`, `SubnetMask: 24`
   - `Common.PortGroups: {app: "VM_Network", db: "VM_Network", web: "VM_Network"}`
   - `Common.DNS: "8.8.8.8"`, `Common.Domain: "corp.local"`, default `Common.hostnames`
     (`vpc-app-01b`, `vpc-db-01b`, `vpc-web-01b/02b/03b`)
   - `Target.vCenter: {FQDN: vcsa01.lab.local, User: packer@vsphere.local,
     Password: "{{ Common.Password.Physical }}", DataCenter: DC01, Cluster: CL01,
     Datastore: SATA-01, Folder: "3-Tier-{{ NameSuffix }}"}`
   - Keep the `Infoblox` block from the template (unused, harmless).

2. **`playbooks/templates/inventory.yml.j2`** — 3 formula tweaks so rendered
   IPs land in 192.168.0.0/24:
   - web: `.{{ 10 + loop.index }}` → `.{{ 16 + loop.index }}`  ⇒ .17/.18/.19
   - app: `Common.SiteCode + 1 }}.10` → `Common.SiteCode }}.20` ⇒ 192.168.0.20
   - db:  `Common.SiteCode + 2 }}.10` → `Common.SiteCode }}.30` ⇒ 192.168.0.30

3. **`inventories/production/inventory.yml`** — regenerate so the committed copy
   matches render output (render step would rewrite it anyway during deploy).

4. **`inventories/production/group_vars/app.yml` and `db.yml`** — one line each:
   - app: `gateway: "{{Common.BaseNetwork.IPv4}}.{{Common.SiteCode + 1}}.1"` →
     `gateway: "{{Common.BaseNetwork.IPv4}}.{{Common.SiteCode}}.1"`
   - db: same with `+ 2` removed
   - `web.yml` already renders `192.168.0.1` — **do not change**.
   - Do NOT touch the `PortGroup:` lines — they resolve to `VM_Network` via all.yml.

5. **`playbooks/ssh_cleanup.yml`** — update the 5 known_hosts purge expressions
   to the new addresses: `…{{Common.SiteCode}}.17/.18/.19` (web),
   `…{{Common.SiteCode}}.20` (app), `…{{Common.SiteCode}}.30` (db).
   (Replaces `SiteCode + 1`/`+ 2` formulas and old `.11/.12/.13`.)

6. **`inventories/production/host_vars/{web-01b,web-02b,web-03b,app-01b,db-01b}.yml`**
   — align `ansible_host` with the new scheme (`SiteCode` with no `+1/+2`,
   octets `.17/.18/.19/.20/.30`). These files are inactive under default
   `vpc-*` inventory naming, but must not be left holding stale IP formulas.

7. **`playbooks/deploy_ova_vms.yml`** — replace the `ova:` line (line ~40):
   ```yaml
   # Photon OS OVA is stored in the ovas/ directory on the Ansible server
   ova: "{{ playbook_dir }}/ovas/photon-hw15-5.0-dde71ec57.x86_64.ova"
   ```
   (`playbook_dir` = repo root because `deploy.sh` runs root-level `deploy.yml`).
   Keep the old commented `/Software/VMware/Photon/v*` lines or delete them — no
   functional impact.

8. **`check_requirements.yml` (line 149)** — update the grep pattern from
   `"ova:\s*/.*photon-hw"` to `"ova:.*ovas/.*photon-hw"` so the preflight OVA
   check still passes with the new path.
   **Plus a pre-existing bug fix discovered during execution** (see
   "Preflight bug fix" below): drop `name: all_vars` from the `include_vars`
   task and change `all_vars.X` → `X` in the assert/display tasks.

### Preflight bug fix (executed — required for `check_requirements.yml` to pass)

**Symptom:** `include_vars ... name: all_vars` reported `ok`, but the following
assert failed with `all_vars.Common.SiteCode is defined → false` and the
display task skipped (`all_vars is defined → false`). Reproduced byte-for-byte
with ansible-core 2.17.14.

**Root cause:** `all.yml` values contain nested Jinja referencing *top-level*
names (`vpc-app-01{{ NameSuffix }}`, `{{ Common.Password.Physical }}`,
`3-Tier-{{ NameSuffix }}`). `include_vars` with `name:` nests everything under
`all_vars`, so those top-level names do not exist. Ansible deep-templates the
whole dict on any access → `'NameSuffix' is undefined` → the variable *appears*
undefined everywhere (`-vvv` shows the register data with that exact error).
This bug predates this plan (the stock `all.yml.template` has the same nested
refs); it was masked by `ignore_errors`.

**Fix (minimal, matches `render_inventory.yml`'s existing `vars_files`
pattern):** load without the namespace so vars are top-level, exactly as real
inventory group_vars behave:
- `check_requirements.yml`: removed `name: all_vars` from `include_vars`;
  changed all 9 assert conditions and 7 display lines from `all_vars.X` → `X`;
  removed the now-invalid `when: all_vars is defined` guard.

**Verified:** assert prints `✓ All required variables are configured`, display
shows SiteCode/vCenter values, OVA grep ✓, `failed=0`.

### Recommended 1-line fixes (approved alongside plan)

- **`ansible.cfg`**: `log_path = /home/admin/git/3-Tier-Apps/ansible-playbook.log`
  → `log_path = ansible-playbook.log` (hardcoded foreign path may not exist on
  the target Ansible server; bare filename is already gitignored).

### Explicitly NOT changed

- `deploy.sh`, `undeploy.sh`, `deploy.yml`, `undeploy.yml`
- `playbooks/configure_vms.yml` (console sendkey flow, static network/hosts push)
- All `playbooks/roles/*`, `containers/*`, `k8s/*`
- `playbooks/deploy_ova_vms.yml` `networks: {"None": "{{ PortGroup }}"}` mapping
  (works for standard portgroups the same as NSX segments)
- Password/credential values in committed files (secrets only in gitignored all.yml)

### Assumptions (flag if wrong)

1. Gateway is `192.168.0.1` (not specified by user).
2. Stock Photon OS v5 OVA → first-boot console flow expects default `changeme`.
3. `VM_Network` is a portgroup reachable by vCenter; Ansible server can reach
   vCenter at `vcsa01.lab.local`.
4. Default hostnames/naming (`vpc-*-01b`, folder `3-Tier-b`) are acceptable.

### Verification steps (run on the Ansible server — ansible is not installed
in the working environment)

```bash
ansible-playbook check_requirements.yml          # preflight incl. OVA path check
ansible-playbook -i localhost, playbooks/render_inventory.yml
#   → confirm inventories/production/inventory.yml shows 192.168.0.17/.18/.19/.20/.30
./deploy.sh
ansible-playbook -i inventories/production/inventory.yml \
  playbooks/install_config_web.yml               # optional web-only re-run
```

Post-deploy sanity: from a web VM, `curl -k https://vpc-app-01b.corp.local:8443/`
and from app VM `curl http://vpc-db-01b/cgi-bin/data.py` (hosts-file based).

### Implementation status

- [x] 1. create `group_vars/all.yml`
- [x] 2. edit `playbooks/templates/inventory.yml.j2`
- [x] 3. regenerate `inventories/production/inventory.yml` (byte-exact match of real template render, verified with Jinja2 + `trim_blocks=True` as Ansible uses)
- [x] 4. fix gateways in `group_vars/app.yml` + `group_vars/db.yml`
- [x] 5. fix IPs in `playbooks/ssh_cleanup.yml`
- [x] 6. align 5 `host_vars/*.yml`
- [x] 7. point `deploy_ova_vms.yml` at `ovas/` + comment
- [x] 8. update `check_requirements.yml` grep (verified it matches the new line)
- [x] 8b. fix pre-existing `include_vars name: all_vars` namespace bug in `check_requirements.yml` (see "Preflight bug fix"); full check now ends `failed=0` with `✓ All required variables are configured`
- [x] 11. add pyvmomi import check to `check_requirements.yml` (deploy failed on server with `ModuleNotFoundError: No module named 'pyVim'` — `community.vmware` modules need pyvmomi from `requirements.txt` installed into the controller's Python; check mirrors the existing sshpass/git/collection style)
- [x] 12. fix OVA path after first deploy attempt failed with `playbooks/ovas/... is not a valid path`: `playbook_dir` resolves to the directory of the play's **source file** (`<repo>/playbooks`), not the entry playbook, when `deploy.yml` uses `import_playbook` — corrected to `{{ playbook_dir | dirname }}/ovas/photon-hw15-5.0-dde71ec57.x86_64.ova`
- [x] 13. vmtoolsd + SSH dead after configure-stage reboot — ROOT CAUSE CONFIRMED: user manually ran `tdnf update` at the console of one VM and both tools and SSH recovered → the sendkey-typed tdnf commands were being interrupted/left half-done by the reboot (in-flight package transaction), breaking openssh + open-vm-tools on disk. FIX EVOLVED (final): first attempt moved tdnf/enable into `vmware_vm_shell` with `wait_for_process` — rejected: guest ops produce no console output and the task blocked blind for up to 1800s. Final design: sendkey types ONE chained command (`tdnf -y update; tdnf -y install lsof open-vm-tools; systemctl enable --now vmtoolsd vgauthd; touch /var/tmp/configure-complete`) so live output stays on the VM console, then a `vmware_guest_file_operation` `fetch` polls `/var/tmp/configure-complete` (120×15s = 30 min ceiling, pre-touched local dest) — the reboot sendkey and everything after only run once the chain verifiably completed; on timeout an explicit `fail` points at the console for the live tdnf output. All remaining sendkeys are ms-fast commands (sed/iptables/hostname). Backstops kept: 60s post-reboot pause + `vmware_guest_info` tools/IP wait with console-diagnostic fail.
- [x] 9. fix `ansible.cfg` log_path
- [x] 10. review diffs; hand off verification commands to user
 - [x] 14. marker-completion gate — RESOLVED, full deploy GREEN (all hosts `failed=0`). Three bugs found & fixed:
        (a) `vmware_guest_info` requires `schema: vsphere` whenever `properties:` is set (module hard-fails with "The option 'properties' is only valid when the schema is 'vsphere'"; default schema is `summary`) — this broke the post-reboot tools/IP guard, and any `properties`-based wait just retries the fatal error as "FAILED - RETRYING". Fixed by adding `schema: vsphere` to the guard and the wait.
        (b) The old completion signal used `vmware_guest_file_operation` `fetch` (`InitiateFileTransferFromGuest` — the fragile path; PUT copy works, GET fetch did not). REPLACED: the typed chain ends with `vmware-rpctool "info-set guestinfo.configure-complete yes"`, and the gate polls `config.extraConfig` (`schema: vsphere` + `properties: [config.extraConfig]`) for that key (120×15s). tdnf output still visible on the console.
        (c) The guestinfo wait looped `FAILED - RETRYING` for all 5 VMs although tdnf finished. ROOT CAUSE: `vmware-rpctool` takes its payload as ONE quoted argument; the chain ran it UNQUOTED, so rpctool received only `info-set` and set nothing. FIX: quote the payload (sendkey supports `"` = keycode 0x34+LEFTSHIFT). Confirmed working: rpctool-set guestinfo DOES surface in vCenter `config.extraConfig`.
        Note: the guard `fail` task banners print the (unrendered) `msg=` template and look alarming, but they show `skipping:` — they are NOT errors. `vmware_guest_sendkey` `sleep_time` left at default 0; typing proved reliable.
 - [x] 15. App tier HTTP 500 (`/cgi-bin/app.py`) — ROOT CAUSE CONFIRMED: guest runs Python **3.14.7**, and the stdlib `cgi` module was REMOVED in Python 3.13. Both `app.py` (`roles/app/templates/app.j2`) and the db's `data.py` (`roles/db/files/data.py`) did `import cgi` only to read the query string → `ModuleNotFoundError: No module named 'cgi'` → CGI died before headers → 500 (`End of script output before headers`). `requests` WAS present. FIX: replaced `cgi.FieldStorage()`/`form.getvalue("querystring")` with `urllib.parse.parse_qs(os.getenv("QUERY_STRING",""))` in both files (stdlib, py3.13+ safe). Both render/compile clean. Apply with `playbooks/install_config_app.yml` and `playbooks/install_config_db.yml` (no redeploy/reboot needed) — then browse `https://<web-ip>/cgi-bin/app.py`.
         Also confirmed expected surfacing: `http://<web-ip>/` = stock nginx welcome (port 80 server block the authors left in `roles/web/files/nginx.conf`); the app is published on HTTPS 443 (`server_name webapp.corp.local`) → proxies to app:8443; app root `/` = stock Apache "It works!" (`DirectoryIndex index.html`), the app itself is at `/cgi-bin/app.py`.

**Verified after implementation:**
- Rendered host→IP map: web `.17/.18/.19`, app `.20`, db `.30`; all-tier gateway `192.168.0.1`
- Static network template renders `Address=192.168.0.20/24`, `Gateway=192.168.0.1`
- Zero leftover `SiteCode +` formulas in `inventories/`, `playbooks/`, root `*.yml`
- All changed YAML files parse cleanly; `all.yml` confirmed gitignored
- `git status`: 13 modified files + untracked `BUILD_PLAN.md` only

**Remaining (on the Ansible server):** place the OVA at `<repo>/ovas/photon-hw15-5.0-dde71ec57.x86_64.ova`, then run the verification commands above.
