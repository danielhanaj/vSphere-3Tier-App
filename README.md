# 3-Tier-Apps — single flat-network fork

## About this fork

This repository is a **fork of [vBrit/3-Tier-Apps](https://github.com/vBrit/3-Tier-Apps)**.

It has been modified so the 3-tier application deploys onto a **single flat L2 network**
(one portgroup, one `/24` subnet, one gateway) instead of the original per-tier NSX
segments/subnets. All container assets from the upstream project (`containers/`, `k8s/`)
have been **removed** — this is a VM-only deployment.

Key changes from upstream:

- **Single flat network** — all tiers share one portgroup/subnet (see "What gets deployed").
- **Container/Kubernetes path removed** — VM deployment only.
- **`playbooks/configure_vms.yml` fixed** — the upstream bootstrap ran the first-boot
  configuration dialog without waiting for the guest to finish, which caused a **race
  condition**: the VM could be rebooted before its network/hostname setup completed,
  leaving it **non-functional after the reboot**. This repo fixes that: the sendkey
  sequence now ends by setting a `guestinfo.configure-complete` flag via `vmware-rpctool`,
  and the playbook waits (and re-checks guest tools + IP) for that flag before proceeding
  or rebooting — so a VM is only treated as "configured" once the guest actually says so.

## What gets deployed (flat network)

Five Photon OS VMs on **one** portgroup (for example `VM_Network`) in a single `/24`:

| Tier    | VMs                                               | IPs (with `IPv4=192.168`, `SiteCode=0`) |
| ------- | ------------------------------------------------- | --------------------------------------- |
| Web     | `vpc-web-01<b>`, `vpc-web-02<b>`, `vpc-web-03<b>` | `.17`, `.18`, `.19`                     |
| App     | `vpc-app-01<b>`                                   | `.20`                                   |
| DB      | `vpc-db-01<b>`                                    | `.30`                                   |
| Gateway | —                                                 | `.1`                                    |

IPs are derived as `Common.BaseNetwork.IPv4`.`Common.SiteCode`.`<last octet>`, so the whole
deployment shifts with two values. The `<b>` is `NameSuffix`.

## 1. Prepare the Ansible server

The controller is any Ubuntu host with network access to vCenter and to the target portgroup.
(This repo was developed against Ubuntu with Python 3.)

### 1.1 Base packages

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y git python3 python3-venv python3-pip
```

### 1.2 Create the Python virtualenv

`deploy.sh` auto-activates `~/venvs/ansible` if `ansible-playbook` is not already on `PATH`,
so create the venv there (or activate your own before running):

```bash
python3 -m venv ~/venvs/ansible
source ~/venvs/ansible/bin/activate
pip install --upgrade pip
```

### 1.3 Clone the repo and install dependencies

The wrapper scripts use their own location as the repo root, so the repo can live anywhere.
This guide uses `~/3-Tier-Apps`:

```bash
git clone https://github.com/<your-fork>/3-Tier-Apps.git ~/3-Tier-Apps
cd ~/3-Tier-Apps

pip install -r requirements.txt                          # ansible, pyvmomi, requests
ansible-galaxy collection install -r collections/requirements.yml   # community.vmware
```

### 1.4 Place the Photon OS OVA

The OVA is not stored in this repo. Put it in an `ovas/` directory at the **repo root**:

```bash
mkdir -p ~/3-Tier-Apps/ovas
# copy photon-hw15-5.0-dde71ec57.x86_64.ova here
```

`playbooks/deploy_ova_vms.yml` expects `<repo>/ovas/photon-hw15-5.0-dde71ec57.x86_64.ova`.
If you use a different file/path, edit the `ova:` value in that playbook (see 2.2).

## 2. Configure the deployment

### 2.1 Main config: `inventories/production/group_vars/all.yml`

Create your local copy from the committed template (the real file is gitignored so secrets
are never committed):

```bash
cp inventories/production/group_vars/all.yml.template \
   inventories/production/group_vars/all.yml
```

Edit **`inventories/production/group_vars/all.yml`** and set:

| Location             | Variable             | Example                            | Purpose                                                                                       |
| -------------------- | -------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------- |
| top level            | `NameSuffix`         | `"b"`                              | VM/hostname suffix and default vCenter folder `3-Tier-b`. Keep top-level, not under `Common`. |
| `Common`             | `SiteCode`           | `0`                                | Third IP octet (`.SiteCode.`), e.g. `0` → `192.168.0.x`.                                      |
| `Common.Password`    | `Physical`           | vCenter password                   | vCenter/SSO account password.                                                                 |
| `Common.Password`    | `VMs`                | guest root password                | `root` password configured inside the Photon VMs.                                             |
| `Common`             | `DNS`                | `8.8.8.8`                          | Resolver written into the guest network config.                                               |
| `Common`             | `Domain`             | `corp.local`                       | DNS suffix used in `/etc/hosts` and generated cert SANs.                                      |
| `Common.BaseNetwork` | `IPv4`               | `'192.168'`                        | First two octets of the flat `/24`.                                                           |
| `Common.BaseNetwork` | `SubnetMask`         | `24`                               | Flat-network mask.                                                                            |
| `Common.PortGroups`  | `app` / `db` / `web` | `"VM_Network"`                     | **Set all three to the SAME portgroup** — this is what makes it a flat network.               |
| `Common.hostnames`   | `app` / `db` / `web` | `vpc-app-01b`, ...                 | VM names and hostnames (web is a 3-item list).                                                |
| `Target.vCenter`     | `FQDN`               | `vcsa01.lab.local`                 | vCenter hostname.                                                                             |
| `Target.vCenter`     | `User`               | `svc-ansible@vsphere.local`        | vCenter username.                                                                             |
| `Target.vCenter`     | `Password`           | `"{{ Common.Password.Physical }}"` | Usually left referencing `Physical`.                                                          |
| `Target.vCenter`     | `DataCenter`         | `DC01`                             | vCenter datacenter name.                                                                      |
| `Target.vCenter`     | `Cluster`            | `CL01`                             | Compute cluster.                                                                              |
| `Target.vCenter`     | `Datastore`          | `SATA-01`                          | Datastore for the VMs.                                                                        |
| `Target.vCenter`     | `Folder`             | `"3-Tier-{{ NameSuffix }}"`        | Optional; defaults to `3-Tier-<NameSuffix>`.                                                  |

Set the three `PortGroups` entries to the same value to run everything on one network. The
portgroup must already exist in vCenter.

### 2.2 OVA path: `playbooks/deploy_ova_vms.yml`

Only change this if your OVA filename/location differs from the default
`<repo>/ovas/photon-hw15-5.0-dde71ec57.x86_64.ova`:

```yaml
ova: "{{ playbook_dir | dirname }}/ovas/photon-hw15-5.0-dde71ec57.x86_64.ova"
```

### 2.3 Inventory: `inventories/production/inventory.yml` (auto-generated)

You normally **do not edit** this file. It is regenerated from `Common.hostnames` and the
IP math by `playbooks/render_inventory.yml`, which `deploy.sh`/`undeploy.sh` run
automatically. Re-run it manually after changing `Common.hostnames`, `SiteCode`, or
`BaseNetwork.IPv4`:

```bash
ansible-playbook -i localhost, playbooks/render_inventory.yml
```

Per-host overrides (if ever needed) live in `inventories/production/host_vars/`.

## 3. Deploy

```bash
cd ~/3-Tier-Apps
./deploy.sh
```

`deploy.sh` activates `~/venvs/ansible` if needed, renders the inventory, then runs
`deploy.yml`, which:

1. Renders inventory aliases from `Common.hostnames`.
2. Cleans up local SSH keys (`playbooks/ssh_cleanup.yml`).
3. Deploys the Photon OVAs into vCenter (`playbooks/deploy_ova_vms.yml`).
4. Bootstraps the guests — network config, `/etc/hosts`, `open-vm-tools`
   (`playbooks/configure_vms.yml`).
5. Configures DB → App → Web via **VMware Guest Operations (no SSH)**
   (`playbooks/install_config_db.yml`, `install_config_app.yml`, `install_config_web.yml`).

Access the app at `https://<web-ip>/cgi-bin/app.py` (the web tier also serves a stock page
on port 80).

## 4. Undeploy

```bash
./undeploy.sh
```

Removes the VMs matching the current suffix/hostnames from vCenter and cleans the
temporary rendered files in `playbooks/templates/`.

## 5. Re-apply a tier without a full redeploy

After editing role content (DB data, app UI, etc.), push just the affected tier:

```bash
ansible-playbook -i inventories/production/inventory.yml playbooks/install_config_db.yml
ansible-playbook -i inventories/production/inventory.yml playbooks/install_config_app.yml
ansible-playbook -i inventories/production/inventory.yml playbooks/install_config_web.yml
```

## 6. Connection model

Role playbooks run with `connection: local` and configure guests through VMware Guest
Operations modules (`community.vmware.vmware_vm_shell`,
`community.vmware.vmware_guest_file_operation`). Because of this, **no SSH is required**
into the VMs, but `open-vm-tools` must be installed and running — `playbooks/configure_vms.yml`
installs and enables it during bootstrap.

## 7. Secrets

`inventories/production/group_vars/all.yml` holds passwords and is **gitignored**. Only
`all.yml.template` (with `CHANGE_ME_*` placeholders) is committed. Do not commit the real file.

## Credits

- Forked from [vBrit/3-Tier-Apps](https://github.com/vBrit/3-Tier-Apps) (original author
  Karl Newick).
- Original also thanks [kwrobert](https://github.com/kwrobert) and
  [doug-baer/hol-3-tier-app](https://github.com/doug-baer/hol-3-tier-app).
