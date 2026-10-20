#!/usr/bin/env python3
# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Live multi-node slot/mempool/wallet UX test.

Covers:
- salted slot hints diverge across wallets/nodes and point at empty slots;
- many wallet sends across 3 nodes;
- mempool gossip/confirmation/drain;
- recipient balances update incrementally from block notifications (no rescan after each tx);
- final chain convergence and empty mempools.

Environment knobs:
  NOID_LIVE_TX_ROUNDS   default 2  (each round submits 6 txs: A->B,A->C,B->C,B->A,C->A,C->B)
  NOID_LIVE_START_BLOCKS default 20
  NOID_LIVE_BASE_PORT   default 19600
"""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_BIN = ROOT / "target" / "release" / "kairos"
BASE = ROOT / "target" / "live-tests" / "slot-mempool-wallet"
LOGS = BASE / "logs"

START_BLOCKS = int(os.environ.get("NOID_LIVE_START_BLOCKS", "20"))
TX_ROUNDS = int(os.environ.get("NOID_LIVE_TX_ROUNDS", "2"))
BASE_PORT = int(os.environ.get("NOID_LIVE_BASE_PORT", "19600"))
AMOUNT_BASE = 100_000  # 0.1 KAI in micronoid; small enough for many rounds


class LiveTestError(Exception):
    pass


class Node:
    def __init__(
        self,
        name,
        p2p_port,
        rpc_port,
        mode="node",
        genesis=False,
        seed=None,
        log="info",
    ):
        self.name = name
        self.p2p_port = p2p_port
        self.rpc_port = rpc_port
        self.mode = mode
        self.genesis = genesis
        self.seed = seed or []
        self.log = log
        self.data_dir = BASE / name
        self.log_path = LOGS / f"{name}.log"
        self.proc = None
        self.log_file = None

    @property
    def rpc_url(self):
        return f"http://127.0.0.1:{self.rpc_port}"

    @property
    def seed_addr(self):
        return f"127.0.0.1:{self.p2p_port}"

    def start(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        args = [
            str(NODE_BIN),
            "--mode",
            self.mode,
            "--data-dir",
            str(self.data_dir),
            "--p2p-listen",
            f"127.0.0.1:{self.p2p_port}",
            "--rpc-listen",
            f"127.0.0.1:{self.rpc_port}",
            "--disable-dns-seeds",
            "--log",
            self.log,
        ]
        if self.genesis:
            args.append("--genesis")
        for seed in self.seed:
            args.extend(["--seed", seed])
        self.log_file = open(self.log_path, "ab", buffering=0)
        self.log_file.write(
            (
                f"\n\n===== START {self.name} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            ).encode()
        )
        self.proc = subprocess.Popen(
            args, cwd=ROOT, stdout=self.log_file, stderr=subprocess.STDOUT
        )
        print(
            f"[start] {self.name}: pid={self.proc.pid} rpc={self.rpc_url} p2p={self.seed_addr} mode={self.mode}",
            flush=True,
        )
        self.wait_rpc()

    def wait_rpc(self, timeout=60):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise LiveTestError(
                    f"{self.name} exited early code={self.proc.returncode}"
                )
            try:
                rpc(self.rpc_url, "getChainInfo", timeout=2)
                return
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise LiveTestError(f"{self.name} RPC not ready: {last}")

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return
        print(f"[stop] {self.name}", flush=True)
        try:
            rpc(self.rpc_url, "stop", timeout=3)
        except Exception as e:
            print(f"[stop] {self.name}: rpc stop failed: {e}", flush=True)
        try:
            self.proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=6)
        if self.log_file:
            self.log_file.close()
            self.log_file = None

    def info(self):
        return rpc(self.rpc_url, "getChainInfo", timeout=5)

    def height(self):
        return int(self.info()["height"])

    def hash(self):
        return self.info()["best_hash"]

    def peers(self):
        return int(rpc(self.rpc_url, "getPeerCount", timeout=5))

    def mempool_size(self):
        return int(rpc(self.rpc_url, "getMempoolSize", timeout=5))

    def balance(self):
        return rpc(self.rpc_url, "walletGetBalance", timeout=10)

    def status(self):
        return rpc(self.rpc_url, "walletStatus", timeout=10)

    def address(self):
        addr = self.status()["address"]
        if not addr.startswith("o1"):
            raise LiveTestError(
                f"{self.name} wallet returned non-canonical address: {addr}"
            )
        info = rpc(self.rpc_url, "validateAddress", [addr], timeout=10)
        if not info.get("valid") or not info.get("hex"):
            raise LiveTestError(f"{self.name} invalid wallet address: {info}")
        if info.get("bech32") != addr:
            raise LiveTestError(
                f"{self.name} address did not round-trip: {addr} -> {info}"
            )
        return addr


def rpc(url, method, params=None, timeout=8):
    method_full = method if method.startswith("paranoid_") else f"paranoid_{method}"
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method_full, "params": params or []}
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if "error" in payload:
        raise LiveTestError(f"RPC {method} error: {payload['error']}")
    return payload.get("result")


def wait_until(desc, predicate, timeout=120, interval=1.0):
    print(f"[wait] {desc} timeout={timeout}s", flush=True)
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                print(f"[ok] {desc}: {value}", flush=True)
                return value
            last = value
        except Exception as e:
            last = e
        time.sleep(interval)
    raise LiveTestError(f"timeout waiting for {desc}; last={last}")


def assert_true(cond, msg):
    if not cond:
        raise LiveTestError(msg)


def same_tip(nodes, max_lag=0):
    infos = {n.name: n.info() for n in nodes}
    heights = [int(i["height"]) for i in infos.values()]
    hashes = [i["best_hash"] for i in infos.values()]
    if max(heights) - min(heights) > max_lag:
        return False
    if max_lag == 0 and len(set(hashes)) != 1:
        return False
    return {k: (v["height"], v["best_hash"][:12]) for k, v in infos.items()}


def tail(path, n=120):
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except Exception as e:
        return f"<cannot read {path}: {e}>"


def cleanup(nodes):
    for n in reversed(nodes):
        try:
            n.stop()
        except Exception as e:
            print(f"[cleanup] {n.name}: {e}", flush=True)


def assert_hints_empty_and_diverse(nodes):
    print(
        "\n=== Slot hint checks: salted diversity, emptiness, no mempool reservations ===",
        flush=True,
    )
    samples = {}
    for n in nodes:
        salt = n.name.encode().hex() + f"{time.time_ns():016x}"
        hints = rpc(n.rpc_url, "getSlotHintsSalted", [16, salt], timeout=10)
        assert_true(len(hints) >= 8, f"{n.name} returned too few salted hints: {hints}")
        assert_true(len(hints) == len(set(hints)), f"{n.name} duplicate hints: {hints}")
        for slot in hints[:8]:
            si = rpc(n.rpc_url, "getSlot", [slot], timeout=10)
            assert_true(si.get("empty"), f"{n.name} hinted occupied slot {slot}: {si}")
        samples[n.name] = hints
        print(f"[hints] {n.name}: {hints[:8]}", flush=True)

    unique_lists = {tuple(v[:8]) for v in samples.values()}
    assert_true(
        len(unique_lists) > 1, f"salted hints unexpectedly identical: {samples}"
    )


def wait_tx_confirmed(nodes, tx_hash, timeout=480):
    wait_until(
        f"tx {tx_hash[:12]} confirmed on at least one node",
        lambda: any(rpc(n.rpc_url, "getTx", [tx_hash], timeout=10) for n in nodes),
        timeout=timeout,
        interval=4,
    )
    wait_until(
        f"tx {tx_hash[:12]} visible/confirmed on all nodes or pruned index catches up",
        lambda: all(
            rpc(n.rpc_url, "getTx", [tx_hash], timeout=10) is not None for n in nodes
        ),
        timeout=180,
        interval=3,
    )


def main():
    if not NODE_BIN.exists():
        raise LiveTestError(
            f"release binary missing: {NODE_BIN}; run cargo build --release -p noid_node --bin kairos"
        )
    if BASE.exists():
        shutil.rmtree(BASE)
    LOGS.mkdir(parents=True, exist_ok=True)

    n1 = Node("node1-miner-A", BASE_PORT, BASE_PORT + 1, mode="miner", genesis=True)
    n2 = Node(
        "node2-relay-B",
        BASE_PORT + 10,
        BASE_PORT + 11,
        mode="node",
        seed=[n1.seed_addr],
    )
    n3 = Node(
        "node3-relay-C",
        BASE_PORT + 20,
        BASE_PORT + 21,
        mode="node",
        seed=[n1.seed_addr, n2.seed_addr],
    )
    nodes = [n1, n2, n3]
    started = []
    tx_hashes = []

    try:
        print("\n=== Boot miner and mine spendable funds ===", flush=True)
        n1.start()
        started.append(n1)
        wait_until(
            "node1 mines start blocks",
            lambda: n1.height() if n1.height() >= START_BLOCKS else False,
            timeout=600,
            interval=3,
        )
        # Miner wallet needs one full scan to discover historical coinbases.
        scan = rpc(n1.rpc_url, "walletScan", timeout=180)
        print(f"[scan] n1 initial: {scan}", flush=True)
        assert_true(
            n1.balance()["spendable_micronoid"] >= 100_000_000,
            f"n1 low spendable: {n1.balance()}",
        )

        print("\n=== Start relay wallets and sync ===", flush=True)
        n2.start()
        started.append(n2)
        n3.start()
        started.append(n3)
        wait_until(
            "all nodes have peers",
            lambda: (
                {n.name: n.peers() for n in nodes}
                if all(n.peers() >= 1 for n in [n2, n3])
                else False
            ),
            timeout=120,
            interval=2,
        )
        wait_until(
            "all nodes reach same chain within 2-block live PoW lag",
            lambda: same_tip(nodes, max_lag=2),
            timeout=420,
            interval=3,
        )

        # Register known primary addresses; no repeated scans after tx confirmations.
        addrs = {n.name: n.address() for n in nodes}
        print(f"[addresses] {addrs}", flush=True)
        assert_hints_empty_and_diverse(nodes)

        print("\n=== Funding B/C from A with multiple txs ===", flush=True)
        for i in range(4):
            dst = n2 if i % 2 == 0 else n3
            amount = AMOUNT_BASE + i * 10_000
            send = rpc(
                n1.rpc_url, "walletSend", [dst.address(), amount, 0], timeout=240
            )
            tx_hash = send.get("tx_hash") or send.get("txid")
            assert_true(tx_hash, f"walletSend omitted transaction id: {send}")
            tx_hashes.append(tx_hash)
            print(
                f"[send funding] A->{dst.name} amount={amount} tx={tx_hash[:12]} fee={send['fee_micronoid']}",
                flush=True,
            )
            wait_until(
                "funding tx gossiped",
                lambda: any(n.mempool_size() >= 1 for n in nodes),
                timeout=120,
                interval=2,
            )
            wait_tx_confirmed(nodes, tx_hash)
            wait_until(
                "mempools drain after funding",
                lambda: all(n.mempool_size() == 0 for n in nodes),
                timeout=180,
                interval=3,
            )
            wait_until(
                "chain tip within 1-block live PoW lag after funding",
                lambda: same_tip(nodes, max_lag=1),
                timeout=180,
                interval=3,
            )

        # No walletScan here: B/C must learn receipts via incremental block updates.
        wait_until(
            "B/C balances updated without explicit rescan",
            lambda: (
                {
                    "B": n2.balance(),
                    "C": n3.balance(),
                }
                if n2.balance()["balance_micronoid"] > 0
                and n3.balance()["balance_micronoid"] > 0
                else False
            ),
            timeout=120,
            interval=2,
        )

        print(f"\n=== Cross traffic rounds={TX_ROUNDS} (6 tx/round) ===", flush=True)
        pairs = [(n1, n2), (n1, n3), (n2, n3), (n2, n1), (n3, n1), (n3, n2)]
        for r in range(TX_ROUNDS):
            for j, (src, dst) in enumerate(pairs):
                bal = src.balance()
                if bal["spendable_micronoid"] < 50_000:
                    print(f"[skip] {src.name} spendable too low: {bal}", flush=True)
                    continue
                amount = 20_000 + r * 1_000 + j * 500
                pre_dst = dst.balance()["balance_micronoid"]
                hints_before = rpc(src.rpc_url, "getSlotHints", [8], timeout=10)
                assert_true(
                    len(hints_before) >= 2,
                    f"{src.name} no slot hints before send: {hints_before}",
                )
                send = rpc(
                    src.rpc_url,
                    "walletSend",
                    [dst.address(), amount, 0],
                    timeout=240,
                )
                tx_hash = send.get("tx_hash") or send.get("txid")
                assert_true(tx_hash, f"walletSend omitted transaction id: {send}")
                tx_hashes.append(tx_hash)
                print(
                    f"[send r{r}] {src.name}->{dst.name} amount={amount} tx={tx_hash[:12]} fee={send['fee_micronoid']} hints={hints_before[:3]}",
                    flush=True,
                )
                wait_until(
                    "tx enters some mempool",
                    lambda: any(n.mempool_size() >= 1 for n in nodes),
                    timeout=120,
                    interval=2,
                )
                # Check gossip reaches at least two nodes; exact all-node mempool can race with fast mining.
                wait_until(
                    "tx gossiped to >=2 mempools or already confirmed",
                    lambda: (
                        sum(1 for n in nodes if n.mempool_size() >= 1) >= 2
                        or any(
                            rpc(n.rpc_url, "getTx", [tx_hash], timeout=10)
                            for n in nodes
                        )
                    ),
                    timeout=120,
                    interval=2,
                )
                wait_tx_confirmed(nodes, tx_hash)
                wait_until(
                    "mempools drain",
                    lambda: all(n.mempool_size() == 0 for n in nodes),
                    timeout=180,
                    interval=3,
                )
                wait_until(
                    "chain tip within 1-block live PoW lag",
                    lambda: same_tip(nodes, max_lag=1),
                    timeout=180,
                    interval=3,
                )
                # Recipient should update incrementally; no walletScan.
                wait_until(
                    f"{dst.name} balance increases without rescan",
                    lambda: (
                        dst.balance()["balance_micronoid"]
                        if dst.balance()["balance_micronoid"] >= pre_dst + amount
                        else False
                    ),
                    timeout=120,
                    interval=2,
                )

        assert_hints_empty_and_diverse(nodes)
        wait_until(
            "final exact convergence",
            lambda: same_tip(nodes, max_lag=0),
            timeout=240,
            interval=4,
        )
        assert_true(
            all(n.mempool_size() == 0 for n in nodes), "final mempools not empty"
        )

        summary = {
            "tx_count": len(tx_hashes),
            "tx_hashes": tx_hashes,
            "final": {
                n.name: {
                    "info": n.info(),
                    "wallet": n.status(),
                    "balance": n.balance(),
                    "mempool": n.mempool_size(),
                }
                for n in nodes
            },
        }
        (BASE / "summary.json").write_text(json.dumps(summary, indent=2))
        print(f"[summary] wrote {BASE / 'summary.json'}", flush=True)
        print("SLOT/MEMPOOL/WALLET LIVE TESTS PASSED", flush=True)
    except Exception:
        print("\n=== LIVE TEST FAILURE ===", flush=True)
        for n in started:
            print(
                f"\n--- tail {n.name} {n.log_path} ---\n{tail(n.log_path)}", flush=True
            )
        raise
    finally:
        cleanup(started)


if __name__ == "__main__":
    main()
