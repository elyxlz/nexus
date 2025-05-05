#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "libtorrent>=2.0.11,<3",
#     "requests>=2,<3"
# ]
# ///
"""
Key-only BitTorrent-DHT discovery helper

Usage (no arguments needed – default Raft port 4002):
    uv run test_find.py             # announces and looks up peers

Optional custom port:
    uv run test_find.py 5000        # announce on port 5000

All nodes hard-code the same discovery key ("banana"). They publish their
public-IP:port tuple as a mutable DHT record and look for peers under the very
same info-hash.  Run this on as many machines as you like and they should list
one another once the DHT has converged.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
import urllib.request
from typing import List, Tuple

import libtorrent as lt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NETWORK_KEY = "banana"  # shared secret → SHA-1 → info-hash
DEFAULT_PORT = 4002  # Raft port we advertise
BOOTSTRAP_ROUTERS: List[Tuple[str, int]] = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]
ANNOUNCE_INTERVAL = 15  # seconds between repeated announces
QUERY_INTERVAL = 5  # seconds between get_peers lookups
TIMEOUT = 300  # total seconds to wait before giving up
MIN_ROUTING_NODES = 25  # wait until we know at least N DHT nodes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_public_ip() -> str | None:
    """Return the outward-facing IPv4 address seen by ipify (or None)."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            return r.read().decode().strip()
    except Exception:
        return None


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# ---------------------------------------------------------------------------
# Main discovery logic
# ---------------------------------------------------------------------------


def main() -> None:
    # ---------------------------------------------------------------------
    # Parse CLI
    # ---------------------------------------------------------------------
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    # ---------------------------------------------------------------------
    # Derive info-hash from NETWORK_KEY (hard-coded "banana")
    # ---------------------------------------------------------------------
    key_bytes = NETWORK_KEY.encode()
    info_hash_hex = sha1_hex(key_bytes)
    info_hash = lt.sha1_hash(bytes.fromhex(info_hash_hex))
    print(f"[*] Info-hash (SHA-1 of '{NETWORK_KEY}'): {info_hash_hex}")

    # ---------------------------------------------------------------------
    # Figure out our public IP to publish
    # ---------------------------------------------------------------------
    public_ip = get_public_ip()
    if public_ip:
        print(f"[*] Public IP detected: {public_ip}")
    else:
        print("[!] Could not determine public IP – announcing behind NAT; peers must connect via DHT rendezvous only.")
    advertised_value = json.dumps({"ip": public_ip, "port": port}).encode()

    # ---------------------------------------------------------------------
    # libtorrent session + DHT bootstrap
    # ---------------------------------------------------------------------
    ses = lt.session()
    ses.listen_on(port, port)
    for host, p in BOOTSTRAP_ROUTERS:
        ses.add_dht_router(host, p)
    ses.start_dht()
    ses.set_alert_mask(
        lt.alert.category_t.dht_operation_notification
        | lt.alert.category_t.dht_notification
        | lt.alert.category_t.dht_stats_notification
    )

    # Wait until routing table has a minimum number of nodes
    print("[*] Bootstrapping DHT …")
    while True:
        ses.post_dht_stats()
        alert = ses.wait_for_alert(1000)
        for a in ses.pop_alerts():
            if isinstance(a, lt.dht_stats_alert):
                if a.num_nodes >= MIN_ROUTING_NODES:
                    print(f"[*] DHT routing table ready – {a.num_nodes} nodes")
                    break
        else:
            continue  # inner loop did not break – keep waiting
        break  # inner loop did break – routing table OK

    # ---------------------------------------------------------------------
    # Repeatedly announce and look for peers
    # ---------------------------------------------------------------------
    next_announce = 0.0
    next_query = 0.0
    deadline = time.time() + TIMEOUT
    print("[*] Entering announce / query loop …")

    while time.time() < deadline:
        now = time.time()
        if now >= next_announce:
            print("[*] Announcing our presence …")
            ses.dht_put_item({"v": advertised_value})  # mutable put requires libtorrent 2.x
            ses.dht_announce(info_hash, port, 0)
            next_announce = now + ANNOUNCE_INTERVAL
        if now >= next_query:
            print("[*] Querying DHT for peers …")
            ses.dht_get_peers(info_hash)
            next_query = now + QUERY_INTERVAL

        alert = ses.wait_for_alert(1000)
        if not alert:
            continue

        for a in ses.pop_alerts():
            # Peer list comes back via dht_get_peers_reply_alert
            if isinstance(a, lt.dht_get_peers_reply_alert):
                peers = a.peers()
                if peers:
                    print("[+] Discovered peers:")
                    for ip, p in peers:
                        print(f"    {ip}:{p}")
                    return  # finished successfully
            elif isinstance(a, lt.dht_error_alert):
                print(f"[!] DHT error: {a}")
            elif isinstance(a, lt.dht_put_alert):
                print("[*] DHT put acknowledged")
            elif isinstance(a, lt.dht_announce_alert):
                print("[*] Announce accepted by remote node")

    print(f"[!] Gave up after {TIMEOUT}s – no peers found. Try again or check NAT/firewall.")


if __name__ == "__main__":
    main()
