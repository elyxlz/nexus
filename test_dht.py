#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "libtorrent>=2.0.11,<3",
#     "requests>=2,<3"
# ]
# ///
"""
Key-only BitTorrent-DHT discovery helper (hard-coded key: "banana")

Run on two (publicly reachable) machines:
    uv run test_dht.py          # advertises port 4002
or
    uv run test_dht.py 5000     # advertises custom port 5000

It will print other peers' public-IP:port once discovered.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from typing import List, Tuple

import libtorrent as lt

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
NETWORK_KEY = "banana"
DEFAULT_PORT = 4002
BOOTSTRAP_ROUTERS: List[Tuple[str, int]] = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]
ANNOUNCE_INTERVAL = 20  # seconds
QUERY_INTERVAL = 7  # seconds
TIMEOUT = 300  # give up after 5 min
MIN_ROUTING_NODES = 25  # wait until routing table reasonably filled

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_public_ip() -> str | None:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            return r.read().decode().strip()
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

    public_ip = get_public_ip()
    if public_ip:
        print(f"[*] Public IP detected: {public_ip}")
    else:
        print("[!] Could not determine public IP; replies may not reach us under symmetric NAT.")

    ses = lt.session()
    ses.listen_on(port, port)
    for host, p in BOOTSTRAP_ROUTERS:
        ses.add_dht_router(host, p)
    ses.start_dht()

    # Build alert mask – handle libtorrent variants gracefully
    mask = (
        lt.alert.category_t.dht_operation_notification
        | lt.alert.category_t.dht_notification
        | lt.alert.category_t.stats_notification  # broader stats category covers dht_stats_alert
    )
    ses.set_alert_mask(mask)

    # ------------------------------------------------------------------
    # Wait until routing table is populated
    # ------------------------------------------------------------------
    print("[*] Bootstrapping DHT … (looking for routing nodes)")
    while True:
        ses.post_dht_stats()
        ses.wait_for_alert(1000)
        ready = False
        for a in ses.pop_alerts():
            if isinstance(a, lt.dht_stats_alert):
                if a.num_nodes >= MIN_ROUTING_NODES:
                    ready = True
                    print(f"[*] Routing table ready – {a.num_nodes} nodes known")
                    break
        if ready:
            break

    # ------------------------------------------------------------------
    # Main announce / lookup loop
    # ------------------------------------------------------------------
    deadline = time.time() + TIMEOUT
    next_announce = 0.0
    next_query = 0.0

    while time.time() < deadline:
        now = time.time()
        if now >= next_announce:
            print("[*] Announcing our presence …")
            ses.dht_announce(info_hash, port, 0)
            next_announce = now + ANNOUNCE_INTERVAL
        if now >= next_query:
            print("[*] Querying DHT for peers …")
            ses.dht_get_peers(info_hash)
            next_query = now + QUERY_INTERVAL

        ses.wait_for_alert(1000)
        for a in ses.pop_alerts():
            if isinstance(a, lt.dht_get_peers_reply_alert):
                peers = a.peers()
                if peers:
                    print("[+] Discovered peers:")
                    for ip, p in peers:
                        print(f"    {ip}:{p}")
                    return
            elif isinstance(a, lt.dht_error_alert):
                print(f"[!] DHT error: {a}")

    print(f"[!] Timed out after {TIMEOUT}s – no peers found. Check NAT/firewall or run on public VMs.")


if __name__ == "__main__":
    main()
