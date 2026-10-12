# OCI Always Free A1 Flex Instance Auto-Retry Provisioner

An automated, robust Python tool to continuously retry provisioning an **Oracle Cloud Infrastructure (OCI) Always Free Ampere A1 (ARM)** instance until capacity is available.

---

## 📌 Why This Script Exists

Oracle Cloud provides an **Always Free** tier offering Ampere A1 Compute resources with up to **2 ARM OCPUs, 12 GB of RAM, and 200 GB of boot volume storage** at no charge.

However, because these resources are in extremely high demand, manually clicking **"Create"** in the OCI web console almost always fails with:

```text
Out of host capacity.
```

Capacity opens up randomly as other users terminate instances or when Oracle provisions more physical servers. This script:
- Continuously retries instance creation at regular intervals.
- Rotates automatically through all Availability Domains (ADs) in your region (e.g., AD-1, AD-2, AD-3).
- Runs automated **pre-flight diagnostics** before starting to ensure your API keys, subnet, image, and quotas are valid.
- Handles rate-limiting (HTTP 429) gracefully with exponential backoff.
- Stops automatically once the instance is created or if an instance with the same name already exists.

---

## ⚙️ Always Free Resource Allowances & Sizing

Under the Oracle Cloud "Always Free" tier, your total **VM.Standard.A1.Flex** resource pool is:
- **Total OCPUs**: 2 OCPUs (Ampere Altra ARM)
- **Total Memory**: 12 GB RAM
- **Boot Volume Storage**: Up to 200 GB total free storage across all boot and block volumes

### Default Sizing (Half of Total Limit)
By default, this script is configured to use **half of your total allowance**:
- **1.0 OCPU**
- **6.0 GB RAM**
- **100 GB Boot Volume**

This gives you maximum flexibility:
- **Run two balanced instances** (1 OCPU / 6 GB RAM / 100 GB each), or
- Adjust the configuration to **one maximum instance** utilizing the full allowance (2 OCPUs / 12 GB RAM / 200 GB).

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- An active [Oracle Cloud Infrastructure](https://cloud.oracle.com/) account.
- Python 3.8 or newer.
- An SSH key pair on your machine.

---

### 2. Set Up Your OCI API Key & Config

OCI scripts authenticate using an API signing key pair and a configuration file located at `~/.oci/config`.

1. Log into your **[Oracle Cloud Console](https://cloud.oracle.com/)**.
2. Click on the **Profile** icon in the top-right corner → Select **My Profile** (or **User Settings**).
3. Scroll down on the left navigation and click **API Keys**.
4. Click **Add API Key**:
   - Select **Generate API Key Pair**.
   - Click **Download Private Key** and save it as `oci_api_key.pem`.
   - Click **Add**.
5. A dialog titled **Configuration File Preview** will appear. Copy the text block shown.
6. On your machine (or VPS), set up your `~/.oci` directory:

```bash
mkdir -p ~/.oci
# Move your downloaded private key into ~/.oci/
mv /path/to/oci_api_key.pem ~/.oci/oci_api_key.pem
chmod 600 ~/.oci/oci_api_key.pem
```

7. Create or edit `~/.oci/config`:

```ini
[DEFAULT]
user=ocid1.user.oc1..aaaaaaaaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
fingerprint=xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx
tenancy=ocid1.tenancy.oc1..aaaaaaaaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
region=us-ashburn-1
key_file=~/.oci/oci_api_key.pem
```

8. Restrict permissions on the config file:

```bash
chmod 600 ~/.oci/config
```

---

### 3. Obtain Required OCI Identifiers (OCIDs)

You will need three IDs from the OCI web console:

| Identifier | Where to Find in OCI Console | Example |
| :--- | :--- | :--- |
| **Compartment / Tenancy OCID** | **Identity & Security** → **Tenancy** (or **Compartments**) → Copy Tenancy OCID. | `ocid1.tenancy.oc1..aaaaaaaaxxxx` |
| **Subnet OCID** | **Networking** → **Virtual Cloud Networks** → Click your VCN → **Subnets** → Copy Public Subnet OCID. | `ocid1.subnet.oc1.<region>.aaaaaaaaxxxx` |
| **Image OCID** | **Compute** → **Custom Images** or Instance Creation page (see instructions below). | `ocid1.image.oc1.<region>.aaaaaaaaxxxx` |

#### How to find the OS Image OCID for ARM:
1. Go to **Compute** → **Instances** → **Create Instance**.
2. Under **Placement and Image**, click **Change Image**.
3. Select your desired OS (e.g., **Canonical Ubuntu 24.04** or **Oracle Linux 9**).
4. **Important**: Select the **aarch64 / ARM** architecture.
5. Click the image to view its details and copy the **Image OCID**.
*(Alternatively, consult the [Oracle Cloud Linux Images documentation](https://docs.oracle.com/en-us/iaas/images/) for your specific region).*

#### Don't have a VCN yet?
If you don't have a Virtual Cloud Network yet:
1. Go to **Networking** → **Virtual Cloud Networks**.
2. Click **Start VCN Wizard** → Select **Create VCN with Internet Connectivity**.
3. Follow the wizard. Once completed, copy the OCID of the **Public Subnet**.

---

### 4. Configure the Script

Clone this repository:

```bash
git clone https://github.com/ibrahaimbadri/oci-arm-free-retry.git
cd oci-arm-free-retry
```

You can configure the parameters using either a `.env` file or by editing `oci_a1_retry.py` directly:

#### Option A: Using `.env` (Recommended)

Copy the template and edit:

```bash
cp .env.example .env
nano .env
```

Fill in your OCIDs and desired sizing:

```ini
OCI_COMPARTMENT_ID=ocid1.tenancy.oc1..aaaaaaaaxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OCI_SUBNET_ID=ocid1.subnet.oc1.<region>.aaaaaaaaxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OCI_IMAGE_ID=ocid1.image.oc1.<region>.aaaaaaaaxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OCI_SSH_PUBLIC_KEY=~/.ssh/id_rsa.pub

OCI_OCPUS=1.0
OCI_MEMORY_IN_GBS=6.0
OCI_BOOT_VOLUME_GB=100
OCI_DISPLAY_NAME=free-a1-flex-instance
OCI_RETRY_INTERVAL=10
```

#### Option B: Editing `oci_a1_retry.py` directly

Open `oci_a1_retry.py` and modify the values in the `CONFIG` section near the top of the file:

```python
COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaaxxxxxxxxxxxxxxxxxxxx"
SUBNET_ID      = "ocid1.subnet.oc1.<region>.aaaaaaaaxxxxxxxxxxxxxxxxxxxx"
IMAGE_ID       = "ocid1.image.oc1.<region>.aaaaaaaaxxxxxxxxxxxxxxxxxxxx"
SSH_PUBLIC_KEY_OR_PATH = "~/.ssh/id_rsa.pub"  # or paste your ssh-rsa/ssh-ed25519 string
```

> **Note on Availability Domains**: Leaving `AVAILABILITY_DOMAINS = []` enables **automatic discovery**. The script will query OCI's Identity API and dynamically rotate through every Availability Domain present in your tenancy's region.

---

### 5. Install Dependencies & Run

Install the OCI Python SDK:

```bash
pip install -r requirements.txt
```

Run the script:

```bash
python3 oci_a1_retry.py
```

The script will first execute a **Pre-Flight Diagnostic**:
```text
=================================================================
PRE-FLIGHT DIAGNOSTICS
=================================================================
[1/6] Checking OCI config fields...
      OK   — All required config fields present
[2/6] Checking API private key file...
      OK   — Key file exists: /home/user/.oci/oci_api_key.pem
             Permissions: 600 (secure)
[3/6] Connecting to OCI Identity API (auth test)...
      OK   — Authenticated successfully!
             Discovered ADs in region: ['xxxx:REGION-AD-1', 'xxxx:REGION-AD-2', 'xxxx:REGION-AD-3']
[4/6] Verifying Subnet OCID...
      OK   — Subnet: Public Subnet-vcn | State: AVAILABLE
[5/6] Verifying OS Image OCID...
      OK   — Image: Canonical-Ubuntu-24.04-aarch64 | State: AVAILABLE
[6/6] Checking A1.Flex shape listing per Availability Domain...
      OK   — Shape supported in xxxx:REGION-AD-1
      OK   — Shape supported in xxxx:REGION-AD-2
      OK   — Shape supported in xxxx:REGION-AD-3
=================================================================
DIAGNOSTICS PASSED — Starting retry loop...
=================================================================
```

---

## 🏃 Running 24/7 in the Background

Since capacity may take hours or a few days to open up, you should run the script inside a persistent background session.

### Method 1: Using `tmux` (Recommended)

1. Start a new session:
   ```bash
   tmux new -s oci
   ```
2. Launch the script:
   ```bash
   python3 oci_a1_retry.py
   ```
3. Detach from the session: Press `Ctrl + B`, release, then press `D`.
4. To check back on progress later:
   ```bash
   tmux attach -t oci
   ```

### Method 2: Using `nohup`

Run in the background redirecting logs to `oci_retry.log`:

```bash
nohup python3 oci_a1_retry.py > oci_retry.log 2>&1 &
```

Check the live logs:
```bash
tail -f oci_retry.log
```

---

## 🔑 Post-Provisioning: Connecting to Your Server

Once capacity opens up, the script will output:

```text
=================================================================
SUCCESS! Instance provisioning initiated successfully.
  Instance ID       : ocid1.instance.oc1.<region>.xxxxx
  Display Name      : free-a1-flex-instance
  AvailabilityDomain: xxxx:REGION-AD-2
  Shape             : VM.Standard.A1.Flex (1.0 OCPU, 6.0 GB RAM)
  State             : PROVISIONING
=================================================================
```

1. Wait **2–3 minutes** for the instance to transition from `PROVISIONING` to `RUNNING`.
2. Locate your instance's **Public IP address** in the OCI Console (Compute → Instances).
3. Connect using SSH:
   - For **Ubuntu**:
     ```bash
     ssh -i ~/.ssh/id_rsa ubuntu@<YOUR_PUBLIC_IP>
     ```
   - For **Oracle Linux**:
     ```bash
     ssh -i ~/.ssh/id_rsa opc@<YOUR_PUBLIC_IP>
     ```

### Opening Firewall Ports
If you plan to run web servers (HTTP 80 / HTTPS 443):
1. **OCI Security List**: In your VCN's Public Subnet, go to the default Security List and add an **Ingress Rule** for ports 80/443 (Source CIDR: `0.0.0.0/0`).
2. **OS Firewall**:
   - Ubuntu:
     ```bash
     sudo ufw allow 80/tcp
     sudo ufw allow 443/tcp
     ```
   - Oracle Linux:
     ```bash
     sudo firewall-cmd --permanent --add-port=80/tcp
     sudo firewall-cmd --permanent --add-port=443/tcp
     sudo firewall-cmd --reload
     ```

---

## ❓ Troubleshooting & FAQ

| Problem | Cause & Solution |
| :--- | :--- |
| `Out of host capacity` | Normal. Oracle currently has no free A1 ARM nodes in that specific AD. Leave the script running; it will catch the next available node. |
| `TooManyRequests (429)` | You are making requests too quickly. The script automatically handles this with exponential backoff. Increase `OCI_RETRY_INTERVAL` (e.g. 30–60s) if this occurs frequently. |
| `NotAuthorizedOrNotFound` | One of your OCIDs (`SUBNET_ID`, `IMAGE_ID`, or `COMPARTMENT_ID`) is incorrect, or your API key lacks permissions for that compartment. Check that the region in your `~/.oci/config` matches your resource OCIDs. |
| `LimitExceeded` | You have reached your Always Free tenancy quota (e.g., you already have instances consuming the 4 OCPUs or 200 GB volume limit). Verify existing instances in OCI Console. |
| `Key file permissions warning` | Your private key permissions are too open. Run `chmod 600 ~/.oci/oci_api_key.pem`. |

---

## 🔒 Security Best Practices

- **Never commit `.env`, `~/.oci/config`, or your private key (`.pem`) to GitHub.**
- The `.gitignore` included in this repository blocks `.env`, `*.pem`, `*.key`, and OCI configuration files by default.

---

## 📄 License

MIT License. Free for personal and commercial use.
