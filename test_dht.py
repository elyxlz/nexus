#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "libtorrent>=2.0.11,<3",
#   "requests>=2,<3",
# ]
# ///
"""
BitTorrent-DHT discovery helper
• Shared key:  "banana"
• Publishes     <public-ip>:<port>  under SHA-1(key)
"""

from __future__ import annotations

import hashlib
import sys
import time
from typing import List, Tuple, Union

import libtorrent as lt
import requests

# --------------------------- config ----------------------------------------

NETWORK_KEY = "banana"
DEFAULT_PORT = 4002
BOOTSTRAP_ROUTERS: List[Tuple[str, int]] = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]
ANNOUNCE_INTERVAL = 20  # seconds
QUERY_INTERVAL = 7
TIMEOUT = 300  # seconds
MIN_ROUTING_NODES = 25

# --------------------------- helpers ---------------------------------------


def public_ip() -> str | None:
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return None


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def bucket_node_count(bucket: Union[dict, object]) -> int:
    """Return #nodes in a bucket, handling struct OR dict representation."""
    if isinstance(bucket, dict):
        return int(bucket.get("num_nodes", 0))
    return int(getattr(bucket, "num_nodes", 0))


# --------------------------- main ------------------------------------------


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    info_hash_hex = sha1_hex(NETWORK_KEY.encode())
    info_hash = lt.sha1_hash(bytes.fromhex(info_hash_hex))
    print(f"[*] Info-hash for key '{NETWORK_KEY}': {info_hash_hex}")

    ip = public_ip()
    if ip:
        print(f"[*] Public IP: {ip}")
    else:
        print("[!] Could not determine public IP (might be behind CG-NAT)")

    # ---- libtorrent session ------------------------------------------------
    ses = lt.session()
    ses.listen_on(port, port)
    for host, p in BOOTSTRAP_ROUTERS:
        ses.add_dht_router(host, p)
    ses.start_dht()

    ses.set_alert_mask(
        lt.alert.category_t.dht_operation_notification
        | lt.alert.category_t.dht_notification
        | lt.alert.category_t.stats_notification
    )

    # ---- wait for routing table to warm up ---------------------------------
    print("[*] Bootstrapping DHT …")
    warm_deadline = time.time() + 60
    while time.time() < warm_deadline:
        ses.post_dht_stats()
        ses.wait_for_alert(1000)
        total = 0
        for a in ses.pop_alerts():
            if isinstance(a, lt.dht_stats_alert):
                total = sum(bucket_node_count(b) for b in a.routing_table)
        if total >= MIN_ROUTING_NODES:
            print(f"[*] Routing table ready – {total} nodes")
            break
    else:
        print("[!] Routing table still sparse; continuing anyway")

    # ---- main announce / lookup loop --------------------------------------
    deadline = time.time() + TIMEOUT
    next_announce = next_query = 0.0

    while time.time() < deadline:
        now = time.time()
        if now >= next_announce:
            print("[*] Announcing …")
            ses.dht_announce(info_hash, port, 0)
            next_announce = now + ANNOUNCE_INTERVAL

        if now >= next_query:
            ses.dht_get_peers(info_hash)
            next_query = now + QUERY_INTERVAL

        ses.wait_for_alert(1000)
        for a in ses.pop_alerts():
            if isinstance(a, lt.dht_get_peers_reply_alert):
                peers = a.peers()
                if peers:
                    print("[+] Peers discovered:")
                    for ip_addr, ip_port in peers:
                        print(f"    {ip_addr}:{ip_port}")
                    return
            elif isinstance(a, lt.dht_error_alert):
                print(f"[!] DHT error: {a}")

    print(f"[!] No peers after {TIMEOUT}s – check NAT/firewall or try again.")


if __name__ == "__main__":
    main()
