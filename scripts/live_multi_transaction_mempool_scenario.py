#!/usr/bin/env python3
"""Isolated three-transaction mempool propagation and miner-selection test.

Each invocation creates new miner and observer data directories, mines a fresh
genesis chain, submits three non-conflicting wallet payments, proves that all
three reach both mempools, and requires one canonical block to include them.
"""

import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_BIN = ROOT / "target" / "release" / "kairos"
RUN_PARENT = ROOT / "target" / "live-tests"
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
BASE = Path(
    os.environ.get(
        "NOID_LIVE_MULTI_TX_DIR", str(RUN_PARENT / f"multi-tx-mempool-clean-{STAMP}")
    )
)
BASE_PORT = int(os.environ.get("NOID_LIVE_MULTI_TX_BASE_PORT", "20500"))
AMOUNTS = (1_000_000, 2_000_000, 3_000_000)


class LiveMultiTxError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise LiveMultiTxError(message)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def rpc(port, method, params=None, timeout=15):
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method if method.startswith("paranoid_") else f"paranoid_{method}",
            "params": params or [],
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=payload,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise LiveMultiTxError(f"RPC {method}@{port} transport failed: {error}") from error
    if result.get("error") is not None:
        raise LiveMultiTxError(f"RPC {method}@{port} failed: {result['error']}")
    return result.get("result")


class Node:
    def __init__(self, name, p2p_port, rpc_port):
        self.name = name
        self.p2p_port = p2p_port
        self.rpc_port = rpc_port
        self.root = BASE / name
        self.data_dir = self.root / "data"
        self.config = self.root / "kairos.toml"
        self.proc = None
        self.log_handle = None
        self.log_path = None

    @property
    def seed(self):
        return f"127.0.0.1:{self.p2p_port}"

    def start(self, label, mode="node", genesis=False, seeds=None):
        require(self.proc is None or self.proc.poll() is not None, f"{self.name} already runs")
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = BASE / "logs" / f"{label}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            str(NODE_BIN),
            "--mode",
            mode,
            "--config",
            str(self.config),
            "--data-dir",
            str(self.data_dir),
            "--p2p-listen",
            f"127.0.0.1:{self.p2p_port}",
            "--rpc-listen",
            f"127.0.0.1:{self.rpc_port}",
            "--disable-dns-seeds",
            "--log",
            "debug",
        ]
        if genesis:
            args.append("--genesis")
        for seed in seeds or []:
            args.extend(["--seed", seed])
        self.log_handle = open(self.log_path, "wb", buffering=0)
        started = time.monotonic()
        self.proc = subprocess.Popen(
            args, cwd=ROOT, stdout=self.log_handle, stderr=subprocess.STDOUT
        )
        print(f"[start] {label} pid={self.proc.pid} mode={mode} genesis={genesis}", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise LiveMultiTxError(f"{label} exited during startup: {self.proc.returncode}")
            try:
                info = self.info(timeout=3)
                elapsed = time.monotonic() - started
                print(f"[ready] {label} height={info['height']} startup={elapsed:.3f}s", flush=True)
                return info, elapsed
            except LiveMultiTxError:
                time.sleep(0.5)
        raise LiveMultiTxError(f"{label} RPC startup timeout")

    def stop(self):
        if self.proc is None or self.proc.poll() is not None:
            self._close_log()
            return
        try:
            rpc(self.rpc_port, "stop", timeout=5)
        except LiveMultiTxError as error:
            print(f"[stop] {self.name}: {error}", flush=True)
        try:
            self.proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        print(f"[stopped] {self.name} code={self.proc.returncode}", flush=True)
        self._close_log()

    def _close_log(self):
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def info(self, timeout=15):
        return rpc(self.rpc_port, "getChainInfo", timeout=timeout)

    def height(self):
        return int(self.info()["height"])

    def mempool(self):
        return rpc(self.rpc_port, "getMempoolInfo")

    def log_text(self):
        require(self.log_path is not None, f"{self.name} has no log")
        assert self.log_path is not None
        return self.log_path.read_text(errors="replace")


def wait_value(label, probe, timeout, interval=0.25):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value:
                print(f"[ok] {label}: {value}", flush=True)
                return value
            last = value
        except (LiveMultiTxError, KeyError, TypeError) as error:
            last = str(error)
        time.sleep(interval)
    raise LiveMultiTxError(f"timeout waiting for {label}; last={last}")


def wait_mined(node, target, timeout=360):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        height = node.height()
        if height != last:
            print(f"[mine] {node.name} height={height}/{target}", flush=True)
            last = height
        if height >= target:
            return height
        time.sleep(0.25)
    raise LiveMultiTxError(f"{node.name} did not mine h{target}; last={last}")


def exact_tip(left, right):
    a = left.info()
    b = right.info()
    if int(a["height"]) == int(b["height"]) and a["best_hash"] == b["best_hash"]:
        return {left.name: a["height"], right.name: b["height"]}
    return False


def mempool_has_exact(node, txids):
    info = node.mempool()
    found = {item["tx_hash"] for item in info["txs"]}
    if info["size"] == len(txids) and found == set(txids):
        return {"size": info["size"], "txids": sorted(found)}
    return False


def confirmed_set(node, txids):
    found = [rpc(node.rpc_port, "getTx", [txid]) for txid in txids]
    return found if all(item is not None for item in found) else False


FAILURE_MARKERS = (
    " ERROR ",
    "panicked",
    "P2P block rejected",
    "wallet proof task",
    "wallet builder diverged",
    "tx rejected",
    "mempool relay: lagged",
)


def assert_clean_log(label, text):
    failures = [
        line for line in text.splitlines() if any(marker in line for marker in FAILURE_MARKERS)
    ]
    require(not failures, f"{label} contains failures: {failures[-8:]}")


def accepted_block_for_height(text, height):
    for line in text.splitlines():
        if "block accepted" not in line or f"height={height}" not in line:
            continue
        fields = {}
        for key in ("txs", "history_step_ms", "pow_ms", "nonce_to_commit_ms"):
            match = re.search(rf"(?:^|\s){key}=(\d+)(?:\s|$)", line)
            fields[key] = int(match.group(1)) if match else None
        return fields
    return None


def main():
    require(NODE_BIN.is_file(), f"release node is missing: {NODE_BIN}")
    require(not BASE.exists(), f"run directory already exists: {BASE}")
    ports = (BASE_PORT, BASE_PORT + 1, BASE_PORT + 10, BASE_PORT + 11)
    for port in ports:
        require(port_is_free(port), f"port occupied: {port}")
    (BASE / "logs").mkdir(parents=True)
    (RUN_PARENT / "LAST_MULTI_TX_RUN").write_text(str(BASE) + "\n")

    binary_hash = sha256(NODE_BIN)
    print(f"[run] {BASE}", flush=True)
    print(f"[binary] sha256={binary_hash} size={NODE_BIN.stat().st_size}", flush=True)
    miner = Node("miner", BASE_PORT, BASE_PORT + 1)
    observer = Node("observer", BASE_PORT + 10, BASE_PORT + 11)
    summary = {
        "run_dir": str(BASE),
        "binary_sha256": binary_hash,
        "binary_size": NODE_BIN.stat().st_size,
        "amounts_micronoid": list(AMOUNTS),
        "status": "running",
    }
    error = None
    try:
        # Establish the recipient wallet at h0 before it ever sees a block.
        observer.start("01-observer-wallet-h0")
        recipient = rpc(observer.rpc_port, "walletActiveAddress")
        require(recipient["key_index"] == 0, f"observer address is not index zero: {recipient}")
        observer.stop()
        assert_clean_log("observer wallet setup", observer.log_text())

        _, miner_startup = miner.start("02-fresh-genesis-miner", mode="miner", genesis=True)
        wait_mined(miner, 5)
        initial_scan = rpc(miner.rpc_port, "walletScan", timeout=120)
        sender_before = rpc(miner.rpc_port, "walletGetBalance")
        require(sender_before["utxo_count"] >= 5, f"miner wallet lacks distinct inputs: {sender_before}")

        _, observer_startup = observer.start(
            "03-observer-live", seeds=[miner.seed]
        )
        wait_value(
            "observer connected",
            lambda: int(rpc(observer.rpc_port, "getPeerCount")) >= 1,
            timeout=120,
        )
        wait_value(
            "observer exactly synced before submissions",
            lambda: exact_tip(miner, observer),
            timeout=300,
        )
        require(rpc(observer.rpc_port, "walletGetBalance")["balance_micronoid"] == 0, "recipient pre-funded")

        sends = []
        submit_started = time.monotonic()
        for amount in AMOUNTS:
            sent = rpc(
                miner.rpc_port,
                "walletSend",
                [recipient["address"], amount, 0],
                timeout=300,
            )
            require(sent["input_count"] == 1, f"payment did not reserve a distinct one-input coin: {sent}")
            require(sent["output_count"] == 2, f"payment lost pay/change shape: {sent}")
            sends.append(sent)
            print(f"[submitted] amount={amount} tx={sent['txid']}", flush=True)
        submission_elapsed = time.monotonic() - submit_started
        txids = [item["txid"] for item in sends]
        require(len(set(txids)) == len(txids), f"duplicate txids: {txids}")

        local_mempool = wait_value(
            "all three transactions coexist in miner mempool",
            lambda: mempool_has_exact(miner, txids),
            timeout=30,
        )
        remote_mempool_started = time.monotonic()
        remote_mempool = wait_value(
            "all three transactions propagated to observer mempool",
            lambda: mempool_has_exact(observer, txids),
            timeout=60,
        )
        propagation_elapsed = time.monotonic() - remote_mempool_started

        for node in (miner, observer):
            for txid in txids:
                entry = rpc(node.rpc_port, "getMempoolEntry", [txid])
                require(entry is not None, f"{node.name} lost pending {txid}")
                require(
                    entry["n_inputs"] == 1
                    and entry["n_outputs"] == 2
                    and entry["page_count"] == 1
                    and entry["minimum_proof_class"] == "B25"
                    and entry["has_authorization"],
                    f"{node.name} pending metadata wrong: {entry}",
                )

        confirmation_started = time.monotonic()
        miner_confirmed = wait_value(
            "all three transactions confirmed by miner",
            lambda: confirmed_set(miner, txids),
            timeout=600,
            interval=0.5,
        )
        confirmation_elapsed = time.monotonic() - confirmation_started
        heights = {int(item["height"]) for item in miner_confirmed}
        hashes = {item["block_hash"] for item in miner_confirmed}
        positions = {int(item["tx_position"]) for item in miner_confirmed}
        require(len(heights) == 1 and len(hashes) == 1, f"transactions split across blocks: {miner_confirmed}")
        require(positions == {1, 2, 3}, f"logical tx positions are not complete: {miner_confirmed}")
        confirmation_height = heights.pop()

        observer_confirmed = wait_value(
            "observer applies the same confirming block",
            lambda: confirmed_set(observer, txids),
            timeout=180,
            interval=0.5,
        )
        require(observer_confirmed == miner_confirmed, "observer transaction index differs")
        wait_value(
            "both mempools drain",
            lambda: miner.mempool()["size"] == 0 and observer.mempool()["size"] == 0,
            timeout=120,
        )
        wait_value(
            "nodes converge exactly after confirmation",
            lambda: exact_tip(miner, observer),
            timeout=180,
        )

        recipient_balance = rpc(observer.rpc_port, "walletGetBalance")
        require(
            recipient_balance["balance_micronoid"] == sum(AMOUNTS),
            f"observer wallet did not update incrementally: {recipient_balance}",
        )
        require(recipient_balance["utxo_count"] == len(AMOUNTS), f"recipient UTXO count wrong: {recipient_balance}")

        parent_header = rpc(miner.rpc_port, "getBlockHeader", [confirmation_height - 1])
        confirming_header = rpc(miner.rpc_port, "getBlockHeader", [confirmation_height])
        active_slot_delta = (
            int(confirming_header["active_slot_count"])
            - int(parent_header["active_slot_count"])
        )
        # Each payment is +1 net live slot; the confirming coinbase is +1.
        require(active_slot_delta == 4, f"confirming state delta is not +4: {active_slot_delta}")

        miner_text = miner.log_text()
        observer_text = observer.log_text()
        accepted = accepted_block_for_height(miner_text, confirmation_height)
        require(accepted is not None and accepted["txs"] == 4, f"miner block shape wrong: {accepted}")
        require(miner_text.count("tx admitted to mempool") >= 3, "miner did not log three admissions")
        require(observer_text.count("P2P tx admitted") >= 3, "observer did not log three P2P admissions")
        assert_clean_log("miner multi-transaction", miner_text)
        assert_clean_log("observer multi-transaction", observer_text)

        summary.update(
            {
                "status": "passed",
                "recipient": recipient,
                "miner_startup_s": round(miner_startup, 3),
                "observer_startup_s": round(observer_startup, 3),
                "submission_s": round(submission_elapsed, 3),
                "propagation_s": round(propagation_elapsed, 3),
                "confirmation_wait_s": round(confirmation_elapsed, 3),
                "sends": sends,
                "txids": txids,
                "local_mempool": local_mempool,
                "remote_mempool": remote_mempool,
                "confirmed": miner_confirmed,
                "confirmation_height": confirmation_height,
                "confirming_block_log": accepted,
                "active_slot_delta": active_slot_delta,
                "sender_before": sender_before,
                "recipient_balance": recipient_balance,
                "initial_scan": initial_scan,
            }
        )
        print("[PASS] isolated three-transaction mempool propagation and mining", flush=True)
    except Exception as caught:
        error = caught
        summary["status"] = "failed"
        summary["error"] = str(caught)
        print(f"[FAIL] {caught}", flush=True)
    finally:
        observer.stop()
        miner.stop()
        (BASE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"[summary] {BASE / 'summary.json'}", flush=True)
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
