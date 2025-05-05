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
──────────────────────────────
• Shared key hard-coded to “banana”.
• Publishes   <public-ip>:<port> under SHA-1(key) and looks for peers.
Run on two public hosts:
    uv run test_dht.py          # advertises port 4002
    uv run test_dht.py 5000     # advertises port 5000
"""

from __future__ import annotations

import hashlib
import sys
import time
from typing import List, Tuple, Union

import libtorrent as lt
import requests

# ── config ────────────────────────────────────────────────────────────────
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

# ── helpers ───────────────────────────────────────────────────────────────


def public_ip() -> str | None:
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return None


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def bucket_node_count(bucket: Union[dict, object]) -> int:
    """Handle struct *or* dict representation of routing buckets."""
    if isinstance(bucket, dict):
        return int(bucket.get("num_nodes", 0))
    return int(getattr(bucket, "num_nodes", 0))


# ── main ──────────────────────────────────────────────────────────────────
def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    info_hash_hex = sha1_hex(NETWORK_KEY.encode())
    info_hash = lt.sha1_hash(bytes.fromhex(info_hash_hex))
    print(f"[*] Info-hash for key '{NETWORK_KEY}': {info_hash_hex}")

    ip = public_ip()
    if ip:
        print(f"[*] Public IP: {ip}")
    else:
        print("[!] Could not determine public IP (maybe CG-NAT)")

    # session
    ses = lt.session()
    ses.listen_on(port, port)
    for h, p in BOOTSTRAP_ROUTERS:
        ses.add_dht_router(h, p)
    ses.start_dht()
    ses.set_alert_mask(
        lt.alert.category_t.dht_operation_notification
        | lt.alert.category_t.dht_notification
        | lt.alert.category_t.stats_notification
    )

    # wait until routing table has some nodes
    print("[*] Bootstrapping DHT …")
    warm_until = time.time() + 60
    while time.time() < warm_until:
        ses.post_dht_stats()
        ses.wait_for_alert(1000)
        total = 0
        for al in ses.pop_alerts():
            if isinstance(al, lt.dht_stats_alert):
                total = sum(bucket_node_count(b) for b in al.routing_table)
        if total >= MIN_ROUTING_NODES:
            print(f"[*] Routing table ready – {total} nodes")
            break
    else:
        print("[!] Routing table still sparse; continuing anyway")

    # announce / lookup loop
    deadline = time.time() + TIMEOUT
    next_ann = next_q = 0.0

    while time.time() < deadline:
        now = time.time()
        if now >= next_ann:
            print("[*] Announcing …")
            ses.dht_announce(info_hash, port, 0)
            next_ann = now + ANNOUNCE_INTERVAL

        if now >= next_q:
            ses.dht_get_peers(info_hash)
            next_q = now + QUERY_INTERVAL

        ses.wait_for_alert(1000)
        for al in ses.pop_alerts():
            if isinstance(al, lt.dht_get_peers_reply_alert):
                peers = al.peers()
                if peers:
                    print("[+] Peers discovered:")
                    for ip_addr, ip_port in peers:
                        print(f"    {ip_addr}:{ip_port}")
                    return
            # catch-all for any DHT-related error alert variant
            elif "dht_" in al.what() and "error" in al.what():
                print(f"[!] {al.what()}: {al.message()}")

    print(f"[!] No peers after {TIMEOUT}s – check NAT/firewall or try again.")


if __name__ == "__main__":
    main()
