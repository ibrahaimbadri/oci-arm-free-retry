#!/usr/bin/env python3
"""
OCI A1 Flex Instance Auto-Retry Script
=======================================
Continuously attempts to provision an Always Free VM.Standard.A1.Flex (ARM)
instance in your Oracle Cloud Infrastructure (OCI) tenancy, rotating across
Availability Domains until capacity becomes available.

Requirements:
  pip install -r requirements.txt
  (or: pip install oci)

Usage:
  python3 oci_a1_retry.py

Background run (recommended):
  tmux new -s oci
  python3 oci_a1_retry.py
  # Detach: Ctrl+B then D   |   Re-attach: tmux attach -t oci
"""

import os
import sys
import time
from datetime import datetime
import oci

# =============================================================================
# ENVIRONMENT / CONFIG LOADER
# =============================================================================

def _load_env_file(filepath: str = ".env"):
    """Optionally load key=value pairs from a local .env file if present."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val

_load_env_file()

# =============================================================================
# CONFIGURATION
# Set these variables here or define them in your environment / .env file.
# =============================================================================

# Path and profile for your OCI CLI configuration file (~/.oci/config)
OCI_CONFIG_PATH    = os.path.expanduser(os.getenv("OCI_CONFIG_PATH", "~/.oci/config"))
OCI_CONFIG_PROFILE = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")

# Compartment OCID (Leave empty or set to tenancy OCID to use root compartment)
COMPARTMENT_ID = os.getenv(
    "OCI_COMPARTMENT_ID",
    "YOUR_COMPARTMENT_OR_TENANCY_OCID"
)

# Subnet OCID (Public subnet in your VCN where the instance will be attached)
SUBNET_ID = os.getenv(
    "OCI_SUBNET_ID",
    "YOUR_SUBNET_OCID"
)

# OS Image OCID (Region-specific image for ARM / aarch64, e.g. Ubuntu or Oracle Linux)
IMAGE_ID = os.getenv(
    "OCI_IMAGE_ID",
    "YOUR_IMAGE_OCID"
)

# SSH Public Key: Specify a path (e.g. "~/.ssh/id_rsa.pub") OR paste the raw public key string
SSH_PUBLIC_KEY_OR_PATH = os.getenv(
    "OCI_SSH_PUBLIC_KEY",
    "~/.ssh/id_rsa.pub"
)

# Instance Shape & Sizing:
# Total Always Free A1 Flex limit: 2 OCPUs, 12 GB RAM, 200 GB storage.
# Defaults are set to half of the total limit (1 OCPU, 6 GB RAM, 100 GB storage)
# so you can run two instances or use remaining resources as needed:
SHAPE          = os.getenv("OCI_SHAPE", "VM.Standard.A1.Flex")
OCPUS          = float(os.getenv("OCI_OCPUS", "1.0"))        # 1.0 (half of 2.0 total limit)
MEMORY_IN_GBS  = float(os.getenv("OCI_MEMORY_IN_GBS", "6.0")) # 6.0 (half of 12.0 total limit)
BOOT_VOLUME_GB = int(os.getenv("OCI_BOOT_VOLUME_GB", "100"))  # 100 (half of 200 total limit)
DISPLAY_NAME   = os.getenv("OCI_DISPLAY_NAME", "free-a1-flex-instance")

# Retry delay in seconds between provisioning attempts
RETRY_INTERVAL_SECONDS = int(os.getenv("OCI_RETRY_INTERVAL", "30"))

# Availability Domains:
# Leave empty [] to automatically discover and rotate through all ADs in your region.
# Or provide a list if you want to pin specific ADs: ["<prefix>:<region-key>-AD-1", ...]
AVAILABILITY_DOMAINS = []

# =============================================================================
# HELPERS
# =============================================================================

def log(msg: str):
    """Print timestamped console log."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def resolve_ssh_key(key_input: str) -> str:
    """Read SSH public key from file if path exists; otherwise return raw string."""
    key_input = key_input.strip()
    expanded_path = os.path.expanduser(key_input)
    if os.path.isfile(expanded_path):
        with open(expanded_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return key_input


def build_launch_details(ad: str, ssh_public_key: str, compartment_id: str) -> oci.core.models.LaunchInstanceDetails:
    """Construct OCI LaunchInstanceDetails object."""
    return oci.core.models.LaunchInstanceDetails(
        display_name=DISPLAY_NAME,
        compartment_id=compartment_id,
        availability_domain=ad,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_IN_GBS,
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=IMAGE_ID,
            boot_volume_size_in_gbs=BOOT_VOLUME_GB,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID,
            assign_public_ip=True,
            assign_private_dns_record=True,
        ),
        metadata={"ssh_authorized_keys": ssh_public_key},
        agent_config=oci.core.models.LaunchInstanceAgentConfigDetails(
            is_management_disabled=False,
            is_monitoring_disabled=False,
        ),
        availability_config=oci.core.models.LaunchInstanceAvailabilityConfigDetails(
            recovery_action="RESTORE_INSTANCE",
        ),
        instance_options=oci.core.models.InstanceOptions(
            are_legacy_imds_endpoints_disabled=True,
        ),
        is_pv_encryption_in_transit_enabled=True,
    )

# =============================================================================
# PRE-FLIGHT DIAGNOSTICS
# =============================================================================

def run_diagnostics(config: dict) -> tuple[bool, str, list[str]]:
    """Validate credentials, OCIDs, network, image, and AD shape listings."""
    all_ok = True
    resolved_compartment_id = COMPARTMENT_ID
    discovered_ads = []

    log("=" * 65)
    log("PRE-FLIGHT DIAGNOSTICS")
    log("=" * 65)

    # 1) Config fields validation
    log("[1/6] Checking OCI config fields...")
    required_keys = ["user", "fingerprint", "tenancy", "region", "key_file"]
    missing = [k for k in required_keys if not config.get(k)]
    if missing:
        log(f"      FAIL — Missing fields in OCI config: {missing}")
        all_ok = False
    else:
        log("      OK   — All required config fields present")
        log(f"             User       : {config['user']}")
        log(f"             Tenancy    : {config['tenancy']}")
        log(f"             Region     : {config['region']}")
        log(f"             Fingerprint: {config['fingerprint']}")

    # Check compartment placeholder
    if not resolved_compartment_id or "YOUR_COMPARTMENT" in resolved_compartment_id:
        log("      INFO — No custom compartment specified. Defaulting to root tenancy OCID.")
        resolved_compartment_id = config.get("tenancy", "")

    # 2) Private key file check
    log("[2/6] Checking API private key file...")
    key_path = os.path.expanduser(config.get("key_file", ""))
    if not key_path or not os.path.exists(key_path):
        log(f"      FAIL — Key file not found: {key_path}")
        all_ok = False
    else:
        perms = oct(os.stat(key_path).st_mode)[-3:]
        log(f"      OK   — Key file exists: {key_path}")
        if perms not in ("600", "400"):
            log(f"      WARN — Permissions are {perms}. Recommended: chmod 600 {key_path}")
        else:
            log(f"             Permissions: {perms} (secure)")

    # 3) Identity / Auth test
    log("[3/6] Connecting to OCI Identity API (auth test)...")
    try:
        identity = oci.identity.IdentityClient(config)
        tenancy  = identity.get_tenancy(config["tenancy"]).data
        log("      OK   — Authenticated successfully!")
        log(f"             Tenancy name : {tenancy.name}")
        log(f"             Home region  : {tenancy.home_region_key}")

        # Resolve availability domains
        if AVAILABILITY_DOMAINS:
            discovered_ads = list(AVAILABILITY_DOMAINS)
            log(f"             Using user-specified ADs: {discovered_ads}")
        else:
            ad_list = identity.list_availability_domains(resolved_compartment_id).data
            discovered_ads = [ad.name for ad in ad_list]
            log(f"             Discovered ADs in region: {discovered_ads}")

        if not discovered_ads:
            log("      FAIL — No Availability Domains found in this region.")
            all_ok = False

    except oci.exceptions.ServiceError as e:
        log(f"      FAIL — [{e.code}] {e.message}")
        if e.status == 401:
            log("             API key fingerprint or private key mismatch.")
            log("             Re-check the key in OCI Console -> Profile -> API Keys.")
        elif e.status == 404:
            log("             Tenancy OCID not found. Check 'tenancy' in ~/.oci/config.")
        all_ok = False
    except Exception as e:
        log(f"      FAIL — Cannot reach OCI API: {e}")
        all_ok = False

    # 4) Subnet check
    log("[4/6] Verifying Subnet OCID...")
    if not SUBNET_ID or "YOUR_SUBNET" in SUBNET_ID:
        log("      FAIL — SUBNET_ID is not configured. Please supply a valid subnet OCID.")
        all_ok = False
    else:
        try:
            net = oci.core.VirtualNetworkClient(config)
            sn  = net.get_subnet(SUBNET_ID).data
            log(f"      OK   — Subnet: {sn.display_name}")
            log(f"             CIDR  : {sn.cidr_block} | State: {sn.lifecycle_state}")
            if sn.lifecycle_state != "AVAILABLE":
                log("      WARN — Subnet is not in AVAILABLE state!")
        except oci.exceptions.ServiceError as e:
            log(f"      FAIL — Subnet [{e.code}]: {e.message}")
            all_ok = False
        except Exception as e:
            log(f"      FAIL — {e}")
            all_ok = False

    # 5) Image check
    log("[5/6] Verifying OS Image OCID...")
    if not IMAGE_ID or "YOUR_IMAGE" in IMAGE_ID:
        log("      FAIL — IMAGE_ID is not configured. Please supply a valid image OCID.")
        all_ok = False
    else:
        try:
            compute = oci.core.ComputeClient(config)
            img = compute.get_image(IMAGE_ID).data
            log(f"      OK   — Image: {img.display_name}")
            log(f"             OS    : {img.operating_system} {img.operating_system_version} | State: {img.lifecycle_state}")
            if img.lifecycle_state != "AVAILABLE":
                log("      WARN — Image is not in AVAILABLE state!")
        except oci.exceptions.ServiceError as e:
            log(f"      FAIL — Image [{e.code}]: {e.message}")
            all_ok = False
        except Exception as e:
            log(f"      FAIL — {e}")
            all_ok = False

    # 6) SSH Key validation
    resolved_key = resolve_ssh_key(SSH_PUBLIC_KEY_OR_PATH)
    if not resolved_key or ("ssh-rsa" not in resolved_key and "ssh-ed25519" not in resolved_key and "ecdsa-" not in resolved_key):
        log("      WARN — SSH public key does not appear to be a standard public key format (ssh-rsa/ssh-ed25519).")
        log("             Make sure SSH_PUBLIC_KEY_OR_PATH points to a valid .pub file or key string.")

    # 7) Shape availability per AD
    log("[6/6] Checking A1.Flex shape listing per Availability Domain...")
    try:
        compute = oci.core.ComputeClient(config)
        for ad in discovered_ads:
            shapes = oci.pagination.list_call_get_all_results(
                compute.list_shapes, resolved_compartment_id, availability_domain=ad
            ).data
            found = any(s.shape == SHAPE for s in shapes)
            status = "OK   — Shape supported" if found else "WARN — Shape NOT listed"
            log(f"      {status} in {ad}")
    except Exception as e:
        log(f"      WARN — Could not query shapes for ADs: {e}")

    log("=" * 65)
    if all_ok:
        log("DIAGNOSTICS PASSED — Starting retry loop...")
    else:
        log("DIAGNOSTICS FAILED — Please resolve the errors above.")
    log("=" * 65)
    log("")
    return all_ok, resolved_compartment_id, discovered_ads

# =============================================================================
# MAIN RETRY LOOP
# =============================================================================

def main():
    log("=" * 65)
    log("OCI A1 Flex Always Free Instance Auto-Retry Provisioner")
    log("=" * 65)

    # 1. Load and validate OCI CLI configuration
    try:
        config = oci.config.from_file(OCI_CONFIG_PATH, OCI_CONFIG_PROFILE)
        oci.config.validate_config(config)
    except Exception as e:
        log(f"ERROR loading OCI config from '{OCI_CONFIG_PATH}': {e}")
        log("Make sure ~/.oci/config exists and is populated with your API key and user OCID.")
        sys.exit(1)

    # 2. Run pre-flight diagnostics
    all_ok, compartment_id, availability_domains = run_diagnostics(config)
    if not all_ok:
        sys.exit(1)

    # 3. Resolve SSH key
    ssh_key = resolve_ssh_key(SSH_PUBLIC_KEY_OR_PATH)

    compute = oci.core.ComputeClient(config)

    # 4. Check if an active instance with DISPLAY_NAME already exists
    existing = oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment_id
    ).data
    for inst in existing:
        if inst.display_name == DISPLAY_NAME and inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
            log(f"An instance named '{DISPLAY_NAME}' already exists: {inst.id} ({inst.lifecycle_state}).")
            log("Stopping to prevent creating unwanted duplicates.")
            return

    attempt = 0
    ad_index = 0
    throttle_wait = RETRY_INTERVAL_SECONDS

    # 5. Provisioning retry loop
    while True:
        attempt += 1
        ad = availability_domains[ad_index % len(availability_domains)]
        ad_index += 1

        log(f"Attempt #{attempt} — Trying {ad}...")

        try:
            launch_details = build_launch_details(ad, ssh_key, compartment_id)
            response = compute.launch_instance(launch_details)
            inst = response.data

            log("")
            log("=" * 65)
            log("SUCCESS! Instance provisioning initiated successfully.")
            log(f"  Instance ID       : {inst.id}")
            log(f"  Display Name      : {inst.display_name}")
            log(f"  AvailabilityDomain: {inst.availability_domain}")
            log(f"  Shape             : {inst.shape} ({OCPUS} OCPU, {MEMORY_IN_GBS} GB RAM)")
            log(f"  State             : {inst.lifecycle_state}")
            log("=" * 65)
            log("Wait ~2-3 minutes for the instance to reach 'RUNNING' state.")
            log("Check your public IP in the OCI Console or via OCI CLI.")
            log(f"Connect via SSH: ssh -i <path_to_private_key> ubuntu@<public_ip>")
            log("=" * 65)
            break

        except oci.exceptions.ServiceError as e:
            msg = e.message or ""
            code = e.code or ""

            if "Out of host capacity" in msg or "out of capacity" in msg.lower():
                log(f"  Out of capacity in {ad}. Retrying next AD in {RETRY_INTERVAL_SECONDS}s...")
            elif "LimitExceeded" in code:
                log(f"  LimitExceeded: {msg}")
                log("  You may have reached your Always Free resource limit or already have an instance running.")
                sys.exit(0)
            elif "NotAuthorizedOrNotFound" in code:
                log(f"  Auth / Resource Not Found error: {msg}")
                log("  Please verify your SUBNET_ID, IMAGE_ID, and COMPARTMENT_ID permissions.")
                sys.exit(1)
            elif e.status == 429 or "TooManyRequests" in code:
                log(f"  Rate-limited by OCI API — waiting {throttle_wait}s before next request...")
                time.sleep(throttle_wait)
                throttle_wait = min(throttle_wait * 2, 60)
                continue
            else:
                log(f"  OCI Service Error [{code}]: {msg}")
                log(f"  Retrying next AD in {RETRY_INTERVAL_SECONDS}s...")
        except Exception as e:
            log(f"  Unexpected error: {e}")
            log(f"  Retrying next AD in {RETRY_INTERVAL_SECONDS}s...")

        throttle_wait = RETRY_INTERVAL_SECONDS
        time.sleep(RETRY_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nStopped by user (Ctrl+C). Exiting cleanly.")
        sys.exit(0)
