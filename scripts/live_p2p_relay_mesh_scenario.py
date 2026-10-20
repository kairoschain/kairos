#!/usr/bin/env python3
"""Isolated public-relay/NAT-client topology and retry-storm scenario.

Run inside a fresh user/network namespace. The scenario assigns four
globally-routable-looking loopback aliases to public nodes, then rejects every
direct TCP dial to private client listeners. Clients can therefore expand
their mesh only through explicit Circuit Relay v2 reservations; mDNS or a
localhost shortcut cannot make the test pass accidentally.

Example:
  unshare -Urn python3 scripts/live_p2p_relay_mesh_scenario.py
"""

import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path

import live_two_miner_fork_reorg_scenario as live


ROOT = Path(__file__).resolve().parents[1]
RUN_PARENT = ROOT / "target" / "live-tests"
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
BASE = Path(
    os.environ.get(
        "NOID_LIVE_RELAY_MESH_DIR",
        str(RUN_PARENT / f"relay-mesh-clean-{STAMP}"),
    )
)
CLIENT_COUNT = int(os.environ.get("NOID_LIVE_RELAY_MESH_CLIENTS", "16"))
SETTLE_SECONDS = int(os.environ.get("NOID_LIVE_RELAY_MESH_SETTLE_SECONDS", "45"))
TARGET_HEIGHT = int(os.environ.get("NOID_LIVE_RELAY_MESH_TARGET_HEIGHT", "2"))
SEED_IPS = ("11.1.0.1", "12.1.0.1", "13.1.0.1", "14.1.0.1")
SEED_P2P_BASE = 9700
SEED_RPC_BASE = 27100
CLIENT_P2P_PORT = 9700
CLIENT_RPC_PORT = 9701

live.BASE = BASE
Node = live.Node
rpc = live.rpc
require = live.require


def run_net(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def prepare_isolated_network():
    run_net("ip", "link", "set", "lo", "up")
    for address in SEED_IPS:
        run_net("ip", "addr", "add", f"{address}/32", "dev", "lo")
    # The parent namespace owns every veth gateway. Without these rules its
    # mDNS socket can hear all private clients and make the simulated public
    # VPSes look like LAN neighbours. Real Internet seeds never receive those
    # multicast packets; keep this scenario on the public Kademlia/relay path.
    for chain in ("INPUT", "OUTPUT", "FORWARD"):
        run_net(
            "iptables",
            "-A",
            chain,
            "-p",
            "udp",
            "--dport",
            "5353",
            "-j",
            "DROP",
        )
    # Client namespaces are routable only so the harness can query RPC. No
    # direct P2P listener in a private namespace may be used by another node.
    for chain in ("OUTPUT", "FORWARD"):
        run_net(
            "iptables",
            "-A",
            chain,
            "-d",
            "10.200.0.0/16",
            "-p",
            "tcp",
            "--dport",
            str(CLIENT_P2P_PORT),
            "-m",
            "conntrack",
            "--ctstate",
            "NEW",
            "-j",
            "REJECT",
            "--reject-with",
            "tcp-reset",
        )


class ClientNamespace:
    """One private host behind the parent namespace's public seed network."""

    def __init__(self, index):
        self.index = index
        self.gateway = f"10.200.{index}.1"
        self.address = f"10.200.{index}.2"
        self.parent_if = f"nvp{index}"
        self.child_if = f"nvc{index}"
        self.keeper = None

    def start(self):
        self.keeper = subprocess.Popen(["unshare", "-n", "--", "sleep", "infinity"])
        time.sleep(0.05)
        require(self.keeper.poll() is None, f"client namespace {self.index} did not start")
        pid = self.keeper.pid
        run_net(
            "ip", "link", "add", self.parent_if, "type", "veth", "peer", "name", self.child_if
        )
        run_net("ip", "link", "set", self.child_if, "netns", str(pid))
        run_net("ip", "addr", "add", f"{self.gateway}/30", "dev", self.parent_if)
        run_net("ip", "link", "set", self.parent_if, "up")
        run_net("nsenter", "-t", str(pid), "-n", "ip", "link", "set", "lo", "up")
        run_net(
            "nsenter", "-t", str(pid), "-n", "ip", "addr", "add",
            f"{self.address}/30", "dev", self.child_if,
        )
        run_net("nsenter", "-t", str(pid), "-n", "ip", "link", "set", self.child_if, "up")
        run_net(
            "nsenter", "-t", str(pid), "-n", "ip", "route", "add", "default",
            "via", self.gateway,
        )

    @property
    def command_prefix(self):
        require(self.keeper is not None, "namespace was not started")
        return ("nsenter", "-t", str(self.keeper.pid), "-n")

    def stop(self):
        if self.keeper is None:
            return
        if self.keeper.poll() is None:
            self.keeper.terminate()
            try:
                self.keeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.keeper.kill()
                self.keeper.wait(timeout=5)
        self.keeper = None


def set_public_address(node, address):
    text = node.config.read_text()
    marker = "public_addresses = []"
    require(marker in text, f"{node.name} config has no empty public-address field")
    node.config.write_text(text.replace(marker, f'public_addresses = ["{address}"]', 1))


def log_text(label):
    return (BASE / "logs" / f"{label}.log").read_text(errors="replace")


def count(text, marker):
    return text.count(marker)


def node_status(node):
    return rpc(node.rpc_port, "getNodeStatus", host=node.rpc_host)


def private_mesh_ready(clients, minimum_peers=4, minimum_reservations=1):
    statuses = [node_status(node) for node in clients]
    peers = [int(status["p2p_connected_peers"]) for status in statuses]
    reservations = [int(status["p2p_relay_reservations"]) for status in statuses]
    if all(value >= minimum_peers for value in peers) and all(
        value >= minimum_reservations for value in reservations
    ):
        return {
            "peer_min": min(peers),
            "peer_max": max(peers),
            "reservation_min": min(reservations),
            "reservation_max": max(reservations),
        }
    return False


def canonical_header(node, height):
    header = rpc(node.rpc_port, "getBlockHeader", [height], host=node.rpc_host)
    if header is None or int(header["height"]) != height:
        return False
    return header


def all_clients_have_header(clients, height, expected_hash):
    for node in clients:
        header = canonical_header(node, height)
        if not header or header["hash"] != expected_hash:
            return False
    return True


def assert_clean(label, text):
    forbidden = (
        " ERROR ",
        "panicked",
        "P2P network error",
        "required node-event lane overflowed",
        "network-profile correlation table is full",
    )
    failures = [
        line
        for line in text.splitlines()
        if any(item in line for item in forbidden)
        # This scenario deliberately drops mDNS multicast at the namespace
        # boundary. libp2p reports that injected firewall condition as an
        # ERROR even though the public Kademlia/relay transport is unaffected.
        and "error sending packet on iface address Operation not permitted" not in line
    ]
    require(not failures, f"{label} contains failures: {failures[-10:]}")


def stop_all(nodes):
    errors = []
    for node in reversed(nodes):
        try:
            node.request_stop()
        except Exception as error:
            errors.append(f"request {node.name}: {error}")
    for node in reversed(nodes):
        try:
            node.finish_stop()
        except Exception as error:
            errors.append(f"finish {node.name}: {error}")
    return errors


def main():
    require(live.NODE_BIN.is_file(), f"release node is missing: {live.NODE_BIN}")
    require(8 <= CLIENT_COUNT <= 48, "relay scenario requires 8..48 private clients")
    require(1 <= TARGET_HEIGHT <= 8, "relay scenario target requires 1..8 blocks")
    require(not BASE.exists(), f"run directory already exists: {BASE}")

    seed_endpoints = [
        f"{address}:{SEED_P2P_BASE + index}"
        for index, address in enumerate(SEED_IPS)
    ]
    seeds = [
        Node(
            f"seed-{index}",
            SEED_P2P_BASE + index,
            SEED_RPC_BASE + index,
            # Unique address/port pairs keep each simulated VPS from
            # advertising the other three hosts' interfaces through Identify.
            p2p_host=address,
        )
        for index, address in enumerate(SEED_IPS)
    ]
    (BASE / "logs").mkdir(parents=True)
    (RUN_PARENT / "LAST_P2P_RELAY_MESH_RUN").write_text(str(BASE) + "\n")
    prepare_isolated_network()

    namespaces = [ClientNamespace(index) for index in range(CLIENT_COUNT)]
    try:
        for namespace in namespaces:
            namespace.start()
    except Exception:
        for namespace in reversed(namespaces):
            namespace.stop()
        raise
    clients = [
        Node(
            f"client-{index:02d}",
            CLIENT_P2P_PORT,
            CLIENT_RPC_PORT,
            p2p_host=namespace.address,
            rpc_host=namespace.address,
            command_prefix=namespace.command_prefix,
        )
        for index, namespace in enumerate(namespaces)
    ]
    all_nodes = [*seeds, *clients]

    binary_hash = live.sha256(live.NODE_BIN)
    print(
        f"[run] {BASE} binary={binary_hash} clients={CLIENT_COUNT} settle={SETTLE_SECONDS}s",
        flush=True,
    )
    summary = {
        "run_dir": str(BASE),
        "binary_sha256": binary_hash,
        "client_count": CLIENT_COUNT,
        "target_height": TARGET_HEIGHT,
        "status": "running",
    }
    error = None
    cleanup_errors = []
    active_nodes = []
    seed_labels = []
    client_labels = [f"03-client-{index:02d}" for index in range(CLIENT_COUNT)]

    try:
        # First start creates the exact storage-epoch marker. The migration
        # deliberately clears old network settings, so public addresses are
        # installed only after this clean initialization and then tested on a
        # normal restart.
        for index, seed in enumerate(seeds):
            init_label = f"01-seed-{index}-initialize"
            # Initialize every storage directory offline. Starting an anchor
            # once as a NAT client would persist relay addresses that a real
            # public VPS never had and contaminate the following load test.
            seed.start(init_label, genesis=index == 0, seeds=[])
            seed.stop()
            set_public_address(seed, seed_endpoints[index])
            live_label = f"02-seed-{index}-public"
            seed.start(
                live_label,
                seeds=[endpoint for offset, endpoint in enumerate(seed_endpoints) if offset != index],
            )
            seed_labels.append(live_label)
            active_nodes.append(seed)

        starts = [
            client.spawn(label, seeds=seed_endpoints)
            for client, label in zip(clients, client_labels)
        ]
        for client, label, started in zip(clients, client_labels, starts):
            client.wait_ready(label, started, timeout=300)
            active_nodes.append(client)

        initial = live.wait_value(
            "all private clients form a relay-backed four-peer mesh",
            lambda: private_mesh_ready(clients),
            timeout=240,
            interval=0.5,
        )
        time.sleep(SETTLE_SECONDS)

        public_logs = {label: log_text(label) for label in seed_labels}
        private_logs = {label: log_text(label) for label in client_labels}
        accepted = sum(count(text, "relay: bounded circuit accepted") for text in public_logs.values())
        denied = sum(count(text, "relay: circuit request denied") for text in public_logs.values())
        accelerated = sum(
            count(text, "kad: accelerated lookup below outbound target")
            for text in {**public_logs, **private_logs}.values()
        )
        require(accepted > 0, "no relay circuit was established")
        # One joining wallet needs only a bounded handful of relay circuits to
        # reach the four-peer mesh. The previous implicit full Kademlia
        # bootstrap produced hundreds of accepted and denied circuits for 16
        # clients while the final peer count looked healthy, so peer-count
        # assertions alone did not catch the carrier storm.
        relay_accept_budget = CLIENT_COUNT * 8
        relay_deny_budget = CLIENT_COUNT * 10
        require(
            accepted <= relay_accept_budget,
            f"relay acceptance storm remains: accepted={accepted} budget={relay_accept_budget}",
        )
        require(
            denied <= relay_deny_budget,
            f"relay denial storm remains: denied={denied} budget={relay_deny_budget}",
        )
        require(
            accelerated <= (CLIENT_COUNT + len(seeds)) * 4,
            f"accelerated Kademlia discovery remained unbounded: {accelerated}",
        )

        # Connectivity alone is not sufficient: exercise the full header-first
        # path, terminal serving and verification while every private client is
        # reachable only through Circuit Relay. Reuse the first anchor's
        # identity and database so the restart also proves that reservations
        # recover without creating a second network.
        relay_miner = seeds[0]
        relay_miner.stop()
        active_nodes.remove(relay_miner)
        relay_miner_label = "04-seed-0-relay-miner"
        relay_miner.start(
            relay_miner_label,
            mode="miner",
            seeds=seed_endpoints[1:],
        )
        seed_labels.append(relay_miner_label)
        active_nodes.append(relay_miner)
        live.wait_value(
            "restarted relay miner rejoins the public mesh",
            lambda: int(rpc(relay_miner.rpc_port, "getPeerCount", host=relay_miner.rpc_host)) >= 2,
            timeout=180,
            interval=0.25,
        )

        propagation = []
        for height in range(1, TARGET_HEIGHT + 1):
            header = live.wait_value(
                f"relay miner commits h{height}",
                lambda height=height: canonical_header(relay_miner, height),
                timeout=900,
                interval=0.1,
            )
            observed_at = time.monotonic()
            expected_hash = header["hash"]
            live.wait_value(
                f"all private clients commit canonical h{height}",
                lambda height=height, expected_hash=expected_hash: all_clients_have_header(
                    clients, height, expected_hash
                ),
                timeout=300,
                interval=0.1,
            )
            elapsed = time.monotonic() - observed_at
            propagation.append({"height": height, "seconds": round(elapsed, 3)})
            print(f"[relay-fanout] h{height} all={elapsed:.3f}s", flush=True)

        # Remove one whole public failure domain. Existing exact peers and the
        # second reservation must keep every private node connected while the
        # topology heals without a redial storm.
        failed_seed = seeds[0]
        failed_seed.stop()
        active_nodes.remove(failed_seed)
        after_failover = live.wait_value(
            "private relay mesh survives one public relay outage",
            lambda: private_mesh_ready(clients, minimum_peers=3, minimum_reservations=1),
            timeout=180,
            interval=0.5,
        )
        time.sleep(10)

        final_public_logs = {
            label: log_text(label)
            for label in seed_labels
            if (BASE / "logs" / f"{label}.log").is_file()
        }
        final_private_logs = {label: log_text(label) for label in client_labels}
        for label, text in {**final_public_logs, **final_private_logs}.items():
            assert_clean(label, text)
        summary.update(
            {
                "status": "passed",
                "initial_mesh": initial,
                "post_outage_mesh": after_failover,
                "block_propagation": propagation,
                "max_block_propagation_s": max(item["seconds"] for item in propagation),
                "relay_circuits_accepted": accepted,
                "relay_circuits_denied": denied,
                "accelerated_discovery_rounds": accelerated,
            }
        )
        print(
            f"[PASS] relay mesh accepted={accepted} denied={denied} accelerated={accelerated}",
            flush=True,
        )
    except Exception as caught:
        error = caught
        summary["status"] = "failed"
        summary["error"] = str(caught)
        print(f"[FAIL] {caught}", flush=True)
    finally:
        cleanup_errors.extend(
            stop_all(
                [node for node in active_nodes if node.proc is not None and node.proc.poll() is None]
            )
        )
        for namespace in reversed(namespaces):
            namespace.stop()
        if cleanup_errors and error is None:
            error = live.LiveForkReorgError(f"cleanup failures: {cleanup_errors}")
            summary["status"] = "failed"
            summary["error"] = str(error)
        (BASE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"[summary] {BASE / 'summary.json'}", flush=True)
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
