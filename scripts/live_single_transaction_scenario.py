#!/usr/bin/env python3
"""One isolated production transaction from a newly mined genesis chain.

The scenario deliberately uses one node so P2P/mempool propagation cannot
hide the local wallet -> mempool -> miner -> canonical block path.  Network
propagation is covered by a separate live scenario. Environment parameters
select the input/page shape, but every invocation remains one fresh scenario.

The miner uses the default bounded CPU budget, reserving one or two visible
CPUs for networking and RPC. The wallet submission must enter that same fixed
pool through the local-only WalletProof and WalletVerify phases and finish
inside the GUI's 120-second RPC boundary; a second Rayon worker set would
violate this scenario.
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
SCENARIO = os.environ.get("NOID_LIVE_TX_SCENARIO", "single-input").strip()
BASE = Path(
    os.environ.get(
        "NOID_LIVE_SINGLE_TX_DIR",
        str(RUN_PARENT / f"transaction-{SCENARIO}-clean-{STAMP}"),
    )
)
P2P_PORT = int(os.environ.get("NOID_LIVE_SINGLE_TX_P2P_PORT", "20400"))
RPC_PORT = P2P_PORT + 1
MINE_TO_HEIGHT = int(os.environ.get("NOID_LIVE_TX_MINE_TO_HEIGHT", "3"))
PAYMENT_MICRONOID = int(os.environ.get("NOID_LIVE_TX_PAYMENT_MICRONOID", "1000000"))
EXPECTED_INPUTS = int(os.environ.get("NOID_LIVE_TX_EXPECTED_INPUTS", "1"))
EXPECTED_OUTPUTS = int(os.environ.get("NOID_LIVE_TX_EXPECTED_OUTPUTS", "2"))
EXPECTED_PAGES = int(
    os.environ.get("NOID_LIVE_TX_EXPECTED_PAGES", str((EXPECTED_INPUTS + 7) // 8))
)
EXPECTED_PROOF_CLASS = os.environ.get("NOID_LIVE_TX_EXPECTED_PROOF_CLASS", "B25")
EXPECT_CHANGE = os.environ.get("NOID_LIVE_TX_EXPECT_CHANGE", "1") != "0"


class LiveTxError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise LiveTxError(message)


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


def rpc(method, params=None, timeout=15):
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method if method.startswith("paranoid_") else f"paranoid_{method}",
            "params": params or [],
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{RPC_PORT}",
        data=payload,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise LiveTxError(f"RPC {method} transport failed: {error}") from error
    if result.get("error") is not None:
        raise LiveTxError(f"RPC {method} failed: {result['error']}")
    return result.get("result")


class Node:
    def __init__(self):
        self.root = BASE / "node"
        self.data_dir = self.root / "data"
        self.config = self.root / "kairos.toml"
        self.proc = None
        self.log_handle = None
        self.log_path = None

    def start(self, label, mode, genesis=False):
        require(self.proc is None or self.proc.poll() is not None, "node already runs")
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
            f"127.0.0.1:{P2P_PORT}",
            "--rpc-listen",
            f"127.0.0.1:{RPC_PORT}",
            "--disable-dns-seeds",
            "--log",
            "debug",
        ]
        if genesis:
            args.append("--genesis")
        self.log_handle = open(self.log_path, "wb", buffering=0)
        started = time.monotonic()
        self.proc = subprocess.Popen(
            args, cwd=ROOT, stdout=self.log_handle, stderr=subprocess.STDOUT
        )
        print(f"[start] {label} pid={self.proc.pid} mode={mode} genesis={genesis}", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise LiveTxError(f"{label} exited during startup: {self.proc.returncode}")
            try:
                info = rpc("getChainInfo", timeout=3)
                elapsed = time.monotonic() - started
                print(f"[ready] {label} height={info['height']} startup={elapsed:.3f}s", flush=True)
                return info, elapsed
            except LiveTxError:
                time.sleep(0.5)
        raise LiveTxError(f"{label} RPC startup timeout")

    def stop(self):
        if self.proc is None or self.proc.poll() is not None:
            self._close_log()
            return
        try:
            rpc("stop", timeout=5)
        except LiveTxError as error:
            print(f"[stop] RPC failed: {error}", flush=True)
        try:
            self.proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        print(f"[stopped] code={self.proc.returncode}", flush=True)
        self._close_log()

    def _close_log(self):
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def log_text(self):
        require(self.log_path is not None, "node has no log")
        assert self.log_path is not None
        return self.log_path.read_text(errors="replace")


def wait_height_at_least(target, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        height = int(rpc("getChainInfo")["height"])
        if height != last:
            print(f"[mine] height={height} target>={target}", flush=True)
            last = height
        if height >= target:
            return height
        time.sleep(0.25)
    raise LiveTxError(f"miner did not reach h{target}; last={last}")


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
        except (LiveTxError, KeyError, TypeError) as error:
            last = str(error)
        time.sleep(interval)
    raise LiveTxError(f"timeout waiting for {label}; last={last}")


FAILURE_MARKERS = (
    " ERROR ",
    "panicked",
    "P2P block rejected",
    "wallet proof task",
    "wallet builder diverged",
    "mempool admission failed",
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
    require(port_is_free(P2P_PORT), f"P2P port occupied: {P2P_PORT}")
    require(port_is_free(RPC_PORT), f"RPC port occupied: {RPC_PORT}")
    (BASE / "logs").mkdir(parents=True)
    (RUN_PARENT / "LAST_SINGLE_TX_RUN").write_text(str(BASE) + "\n")
    (RUN_PARENT / "LAST_TRANSACTION_RUN").write_text(str(BASE) + "\n")

    binary_hash = sha256(NODE_BIN)
    print(f"[run] {BASE}", flush=True)
    print(f"[binary] sha256={binary_hash} size={NODE_BIN.stat().st_size}", flush=True)
    node = Node()
    summary = {
        "run_dir": str(BASE),
        "binary_sha256": binary_hash,
        "binary_size": NODE_BIN.stat().st_size,
        "scenario": SCENARIO,
        "configuration": {
            "mine_to_height": MINE_TO_HEIGHT,
            "payment_micronoid": PAYMENT_MICRONOID,
            "expected_inputs": EXPECTED_INPUTS,
            "expected_outputs": EXPECTED_OUTPUTS,
            "expected_pages": EXPECTED_PAGES,
            "expected_proof_class": EXPECTED_PROOF_CLASS,
            "expect_change": EXPECT_CHANGE,
        },
        "status": "running",
    }
    error = None
    try:
        # Create the receiving key at h0, then restore payout/spend account 0
        # before mining starts so every pre-payment coinbase has one owner.
        node.start("01-wallet-setup-h0", mode="node")
        sender = rpc("walletActiveAddress")
        require(sender["key_index"] == 0, f"unexpected initial address: {sender}")
        recipient = rpc("walletNextAddress")
        require(recipient["key_index"] == 1, f"unexpected recipient address: {recipient}")
        restored = rpc("walletSetActiveAddress", [0])
        require(restored["address"] == sender["address"], "failed to restore sender account")
        require(rpc("getSlotsByOwner", [recipient["address"]]) == [], "recipient funded at h0")
        node.stop()
        assert_clean_log("wallet setup", node.log_text())

        _, miner_startup = node.start("02-genesis-miner-single-tx", mode="miner", genesis=True)
        reached = wait_height_at_least(
            MINE_TO_HEIGHT, timeout=max(300, MINE_TO_HEIGHT * 45)
        )
        node_status = rpc("getNodeStatus")
        available_threads = int(node_status["available_threads"])
        reserved_threads = 2 if available_threads >= 8 else 1 if available_threads >= 2 else 0
        expected_worker_threads = max(1, available_threads - reserved_threads)
        require(
            int(node_status["worker_threads"]) == expected_worker_threads,
            f"miner did not preserve the expected network CPU reserve: {node_status}",
        )
        scan = rpc("walletScan", timeout=120)
        before = rpc("walletGetBalance")
        require(before["spendable_micronoid"] > PAYMENT_MICRONOID, f"sender not funded: {before}")
        require(
            before["utxo_count"] >= EXPECTED_INPUTS,
            f"sender has too few UTXOs for requested shape: {before}",
        )

        plan = rpc("walletPlanSend", [recipient["address"], PAYMENT_MICRONOID, 0], timeout=60)
        require(
            plan["input_count"] == EXPECTED_INPUTS,
            f"payment selected the wrong input count: {plan}",
        )
        require(
            plan["output_count"] == EXPECTED_OUTPUTS,
            f"payment selected the wrong output count: {plan}",
        )
        require(
            (plan["change_micronoid"] > 0) == EXPECT_CHANGE,
            f"payment change shape is wrong: {plan}",
        )

        submit_height = int(rpc("getChainInfo")["height"])
        proof_started = time.monotonic()
        sent = rpc("walletSend", [recipient["address"], PAYMENT_MICRONOID, 0], timeout=300)
        proof_elapsed = time.monotonic() - proof_started
        require(
            proof_elapsed < 120,
            f"walletSend exceeded the GUI timeout boundary: {proof_elapsed:.3f}s",
        )
        txid = sent["txid"]
        require(
            sent["input_count"] == EXPECTED_INPUTS
            and sent["output_count"] == EXPECTED_OUTPUTS,
            f"send shape changed: {sent}",
        )
        require(sent["fee_micronoid"] == plan["fee_micronoid"], f"send fee changed: {sent} vs {plan}")
        print(f"[submitted] tx={txid} proof_and_admission={proof_elapsed:.3f}s", flush=True)

        mempool_started = time.monotonic()
        entry = wait_value(
            "transaction present in local mempool",
            lambda: rpc("getMempoolEntry", [txid]),
            timeout=30,
        )
        mempool_observed = time.monotonic() - mempool_started
        require(
            entry["n_inputs"] == EXPECTED_INPUTS
            and entry["n_outputs"] == EXPECTED_OUTPUTS,
            f"mempool shape wrong: {entry}",
        )
        require(entry["page_count"] == EXPECTED_PAGES, f"page count wrong: {entry}")
        require(
            entry["minimum_proof_class"] == EXPECTED_PROOF_CLASS,
            f"wrong proof class: {entry}",
        )
        require(
            entry["requires_b255_miner"] == (EXPECTED_PROOF_CLASS == "B255"),
            f"B255 producer flag is wrong: {entry}",
        )
        require(entry["has_authorization"], f"authorization not cached: {entry}")
        require(int(rpc("getMempoolSize")) == 1, "local mempool size is not one")

        confirmation_started = time.monotonic()
        confirmed = wait_value(
            "transaction confirmed",
            lambda: rpc("getTx", [txid]),
            timeout=600,
            interval=0.5,
        )
        confirmation_elapsed = time.monotonic() - confirmation_started
        require(confirmed["tx_hash"] == txid, f"confirmed txid mismatch: {confirmed}")
        require(confirmed["tx_position"] == 1, f"user transaction is not after coinbase: {confirmed}")
        wait_value(
            "mempool drained",
            lambda: int(rpc("getMempoolSize")) == 0,
            timeout=60,
        )
        recipient_slots = rpc("getSlotsByOwner", [recipient["address"]])
        require(len(recipient_slots) == 1, f"recipient should own one UTXO: {recipient_slots}")
        require(recipient_slots[0]["value"] == PAYMENT_MICRONOID, f"recipient amount wrong: {recipient_slots}")

        confirmation_height = int(confirmed["height"])
        parent_header = rpc("getBlockHeader", [confirmation_height - 1])
        confirming_header = rpc("getBlockHeader", [confirmation_height])
        active_slot_delta = (
            int(confirming_header["active_slot_count"])
            - int(parent_header["active_slot_count"])
        )
        expected_active_slot_delta = 1 + EXPECTED_OUTPUTS - EXPECTED_INPUTS
        require(
            active_slot_delta == expected_active_slot_delta,
            "confirming header does not account for exact coinbase+input/output shape: "
            f"delta={active_slot_delta}, expected={expected_active_slot_delta}",
        )
        miner_text = node.log_text()
        accepted = accepted_block_for_height(miner_text, confirmation_height)
        require(accepted is not None, f"no accepted-block log for h{confirmation_height}")
        assert accepted is not None
        expected_physical_txs = 1 + EXPECTED_PAGES
        require(
            accepted["txs"] == expected_physical_txs,
            "confirming block does not contain coinbase plus every PagedSpend page: "
            f"{accepted}, expected physical txs={expected_physical_txs}",
        )
        require("wallet_send deterministic plan ready" in miner_text, "wallet plan missing from log")
        wallet_phase = re.search(
            r'CPU phase entered shared bounded Rayon pool '
            r'phase="WalletProof" phase_threads=(\d+) shared_pool_threads=(\d+)',
            miner_text,
        )
        require(wallet_phase is not None, "wallet proof bypassed the shared process CPU pool")
        assert wallet_phase is not None
        require(
            int(wallet_phase.group(1)) == int(node_status["worker_threads"])
            and int(wallet_phase.group(2)) == int(node_status["worker_threads"]),
            f"wallet proof used the wrong CPU budget: {wallet_phase.group(0)}",
        )
        verification_phase = re.search(
            r'CPU phase entered shared bounded Rayon pool '
            r'phase="WalletVerify" phase_threads=(\d+) shared_pool_threads=(\d+) '
            r"pool_queue_ms=(\d+)",
            miner_text[wallet_phase.end() :],
        )
        require(
            verification_phase is not None,
            "wallet submission did not enter shared-pool admission verification",
        )
        assert verification_phase is not None
        require(
            int(verification_phase.group(1)) == int(node_status["worker_threads"])
            and int(verification_phase.group(2)) == int(node_status["worker_threads"]),
            f"wallet admission verification used the wrong CPU budget: {verification_phase.group(0)}",
        )
        require(
            int(verification_phase.group(3)) < 5_000,
            "wallet admission verification starved behind background PoW: "
            f"{verification_phase.group(0)}",
        )
        require(
            "mining template ready" in miner_text
            and f"n_txs={expected_physical_txs}" in miner_text,
            "miner never selected every page of the logical payment",
        )
        assert_clean_log("miner transaction", miner_text)
        node.stop()
        miner_text = node.log_text()
        assert_clean_log("miner shutdown", miner_text)

        info, verify_startup = node.start("03-post-confirmation-verification", mode="node")
        require(int(info["height"]) == confirmation_height, f"shutdown added a block: {info}")
        require(rpc("getTx", [txid]) == confirmed, "transaction index changed across restart")
        receipt = rpc("walletExportReceipt", [txid])
        receipt_check = rpc("verifyReceipt", [receipt])
        require(
            receipt_check["confirmed"]
            and receipt_check["canonical"]
            and receipt_check["merkle_valid"],
            f"receipt verification failed: {receipt_check}",
        )

        active_recipient = rpc("walletSetActiveAddress", [1])
        require(active_recipient["address"] == recipient["address"], "recipient activation failed")
        recipient_scan = rpc("walletScan", timeout=120)
        recipient_balance = rpc("walletGetBalance")
        require(recipient_balance["balance_micronoid"] == PAYMENT_MICRONOID, f"recipient balance wrong: {recipient_balance}")
        require(recipient_balance["utxo_count"] == 1, f"recipient UTXO count wrong: {recipient_balance}")

        rpc("walletSetActiveAddress", [0])
        sender_scan = rpc("walletScan", timeout=120)
        sender_after = rpc("walletGetBalance")
        require(sender_after["pending_outbound_micronoid"] == 0, f"sender reservation survived confirmation: {sender_after}")
        history = rpc("walletHistory")
        require(any(item["tx_hash"] == txid and item["direction"] == "sent" for item in history), f"sent history missing: {history}")

        summary.update(
            {
                "status": "passed",
                "sender": sender,
                "recipient": recipient,
                "initial_mining_height_observed": reached,
                "submit_height": submit_height,
                "confirmation_height": confirmation_height,
                "miner_startup_s": round(miner_startup, 3),
                "verification_startup_s": round(verify_startup, 3),
                "wallet_proof_and_admission_s": round(proof_elapsed, 3),
                "mempool_observation_s": round(mempool_observed, 3),
                "confirmation_wait_s": round(confirmation_elapsed, 3),
                "plan": plan,
                "send": sent,
                "mempool_entry": entry,
                "confirmed": confirmed,
                "confirming_block_log": accepted,
                "sender_before": before,
                "sender_after": sender_after,
                "recipient_balance": recipient_balance,
                "initial_scan": scan,
                "recipient_scan": recipient_scan,
                "sender_scan": sender_scan,
                "receipt": receipt_check,
            }
        )
        summary["active_slot_delta"] = active_slot_delta
        print(
            f"[PASS] isolated fresh-chain transaction scenario={SCENARIO} "
            f"inputs={EXPECTED_INPUTS} outputs={EXPECTED_OUTPUTS} pages={EXPECTED_PAGES}",
            flush=True,
        )
    except Exception as caught:
        error = caught
        summary["status"] = "failed"
        summary["error"] = str(caught)
        print(f"[FAIL] {caught}", flush=True)
    finally:
        node.stop()
        (BASE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"[summary] {BASE / 'summary.json'}", flush=True)
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
