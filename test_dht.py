#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "libtorrent>=2.0.11,<3",
#   "requests>=2,<3",
# ]
# ///
"""
BitTorrent-DHT peer-discovery helper
-----------------------------------

* Shared discovery key is **hard-coded** to ``"banana"``.
* Each instance publishes its own ``public-ip:port`` on the DHT and looks up
  others published under the same key (SHA-1 hashed).
* Start on two publicly reachable machines, e.g.

    uv run test_dht.py          # advertises port 4002
    uv run test_dht.py 5000     # advertises port 5000

  Wait a minute or two; both processes should eventually print the other’s
  address.
"""

from __future__ import annotations

import hashlib
import sys
import time
from typing import List, Tuple

import libtorrent as lt
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NETWORK_KEY = "banana"
DEFAULT_PORT = 4002

BOOTSTRAP_ROUTERS: List[Tuple[str, int]] = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]

ANNOUNCE_INTERVAL = 20  # seconds between dht_announce calls
QUERY_INTERVAL = 7  # seconds between dht_get_peers calls
TIMEOUT = 300  # total seconds before we give up
MIN_ROUTING_NODES = 25  # wait until the routing table has at least this many nodes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def public_ip() -> str | None:
    """Return outward-facing IPv4 as a string, or None on failure."""
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return None


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    info_hash_hex = sha1_hex(NETWORK_KEY.encode())
    info_hash = lt.sha1_hash(bytes.fromhex(info_hash_hex))
    print(f"[*] Info-hash (SHA-1 of '{NETWORK_KEY}'): {info_hash_hex}")

    ip = public_ip()
    if ip:
        print(f"[*] Public IP detected: {ip}")
    else:
        print("[!] Could not determine public IP; replies may fail behind strict NAT.")

    # --- libtorrent session ------------------------------------------------
    ses = lt.session()
    ses.listen_on(port, port)

    for host, p in BOOTSTRAP_ROUTERS:
        ses.add_dht_router(host, p)
    ses.start_dht()

    mask = (
        lt.alert.category_t.dht_operation_notification
        | lt.alert.category_t.dht_notification
        | lt.alert.category_t.stats_notification  # covers dht_stats_alert
    )
    ses.set_alert_mask(mask)

    # --- Wait for routing table to warm up ---------------------------------
    print("[*] Bootstrapping DHT – waiting for routing nodes …")
    bootstrap_deadline = time.time() + 60
    routing_ready = False

    while time.time() < bootstrap_deadline and not routing_ready:
        ses.post_dht_stats()
        ses.wait_for_alert(1000)
        for alert in ses.pop_alerts():
            if isinstance(alert, lt.dht_stats_alert):
                total_nodes = sum(bucket.num_nodes for bucket in alert.routing_table)
                if total_nodes >= MIN_ROUTING_NODES:
                    routing_ready = True
                    print(f"[*] Routing table ready – {total_nodes} nodes across buckets")
                    break

    if not routing_ready:
        print("[!] Routing table still sparse; continuing anyway…")

    # --- Main announce / lookup loop ---------------------------------------
    deadline = time.time() + TIMEOUT
    next_announce = 0.0
    next_query = 0.0

    while time.time() < deadline:
        now = time.time()

        if now >= next_announce:
            print("[*] Announcing presence …")
            ses.dht_announce(info_hash, port, 0)
            next_announce = now + ANNOUNCE_INTERVAL

        if now >= next_query:
            print("[*] Querying DHT for peers …")
            ses.dht_get_peers(info_hash)
            next_query = now + QUERY_INTERVAL

        ses.wait_for_alert(1000)
        for alert in ses.pop_alerts():
            if isinstance(alert, lt.dht_get_peers_reply_alert):
                peers = alert.peers()
                if peers:
                    print("[+] Discovered peers:")
                    for ip_addr, ip_port in peers:
                        print(f"    {ip_addr}:{ip_port}")
                    return
            elif isinstance(alert, lt.dht_error_alert):
                print(f"[!] DHT error: {alert}")

    print(
        f"[!] Timed out after {TIMEOUT}s – no peers found. " f"Ensure both machines have reachable UDP and try again."
    )


if __name__ == "__main__":
    main()
