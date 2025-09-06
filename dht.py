#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["libtorrent>=2.0.11,<3", "requests>=2,<3"]
# ///
"""
Minimal BitTorrent-DHT discovery helper
• Hard-coded key: "banana"
• Advertises: <public-ip>:<port>
• Prints new peers until you abort with Ctrl-C
"""

from __future__ import annotations
import hashlib
import sys
import time
import requests
import libtorrent as lt

KEY, DEFAULT_PORT = "bananaaslkdjalkjdalwkdjalkcjsdknsdmnvkwejnkwejfhwkejfhksjehf", 4002
BOOTSTRAP: list[tuple[str, int]] = [
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
]
ANNOUNCE_EVERY, QUERY_EVERY = 20, 7  # seconds
MIN_RT_NODES, WARMUP_TIMEOUT = 25, 60  # routing-table warm-up target, seconds
ROUTING_PORTS = {6881, 6889, 9118}  # Common BitTorrent DHT routing ports

sha1 = lambda b: hashlib.sha1(b).hexdigest()
pubip = lambda: requests.get("https://api.ipify.org", timeout=5).text.strip()
bucket_nodes = lambda b: int(b.get("num_nodes", 0) if isinstance(b, dict) else getattr(b, "num_nodes", 0))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    info_hex = sha1(KEY.encode())
    info_hash = lt.sha1_hash(bytes.fromhex(info_hex))
    print(f"[*] info-hash for key '{KEY}': {info_hex}")

    try:
        my_ip = pubip()
        print(f"[*] public IP: {my_ip}")
    except Exception:
        my_ip = None
        print("[!] could not determine public IP (may be CG-NAT)")

    ses = lt.session()
    ses.listen_on(port, port)
    for h, p in BOOTSTRAP:
        ses.add_dht_router(h, p)
    ses.start_dht()
    ses.set_alert_mask(
        lt.alert.category_t.dht_operation_notification
        | lt.alert.category_t.dht_notification
        | lt.alert.category_t.stats_notification
    )

    print("[*] bootstrapping DHT …")
    end = time.time() + WARMUP_TIMEOUT
    while time.time() < end:
        ses.post_dht_stats()
        ses.wait_for_alert(1000)
        for a in ses.pop_alerts():
            if isinstance(a, lt.dht_stats_alert) and sum(bucket_nodes(b) for b in a.routing_table) >= MIN_RT_NODES:
                print("[*] routing table ready")
                end = 0
                break

    known = set()
    next_ann = next_q = 0.0
    print("[*] running (Ctrl-C to stop) …")
    try:
        while True:
            now = time.time()
            if now >= next_ann:
                ses.dht_announce(info_hash, port, 0)
                next_ann = now + ANNOUNCE_EVERY
            if now >= next_q:
                ses.dht_get_peers(info_hash)
                next_q = now + QUERY_EVERY

            ses.wait_for_alert(1000)
            for a in ses.pop_alerts():
                if isinstance(a, lt.dht_get_peers_reply_alert):
                    for ip_addr, ip_port in a.peers():
                        if (my_ip and (ip_addr, ip_port) == (my_ip, port)) or ip_port in ROUTING_PORTS:
                            continue
                        peer = f"{ip_addr}:{ip_port}"
                        if peer not in known:
                            known.add(peer)
                            print(f"[+] new peer: {peer}")
                elif "dht_" in a.what() and "error" in a.what():
                    print(f"[!] {a.message()}")
    except KeyboardInterrupt:
        print("\n[✓] stopped by user")


if __name__ == "__main__":
    main()
