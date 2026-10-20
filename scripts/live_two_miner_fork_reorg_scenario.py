#!/usr/bin/env python3
"""Isolated fresh-chain two-miner fork and shallow-reorg live test.

The scenario mines every block itself.  It first gives both miners one exact
common prefix, lets them mine competing children while disconnected, extends
one branch by one block, and reconnects the nodes.  The shorter branch must
reorganize to the heavier branch without a snapshot.
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
NODE_BIN = Path(
    os.environ.get("NOID_LIVE_NODE_BIN", str(ROOT / "target" / "release" / "kairos"))
).resolve()
RUN_PARENT = ROOT / "target" / "live-tests"
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
BASE = Path(
    os.environ.get(
        "NOID_LIVE_FORK_REORG_DIR",
        str(RUN_PARENT / f"two-miner-fork-reorg-clean-{STAMP}"),
    )
)
BASE_PORT = int(os.environ.get("NOID_LIVE_FORK_REORG_BASE_PORT", "20600"))
COMMON_HEIGHT = 2
FORK_HEIGHT = COMMON_HEIGHT + 1
WINNER_HEIGHT = FORK_HEIGHT + 1


class LiveForkReorgError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise LiveForkReorgError(message)


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


def rpc(port, method, params=None, timeout=15, host="127.0.0.1"):
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method if method.startswith("paranoid_") else f"paranoid_{method}",
            "params": params or [],
        }
    ).encode()
    request = urllib.request.Request(
        f"http://{host}:{port}",
        data=payload,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise LiveForkReorgError(f"RPC {method}@{port} transport failed: {error}") from error
    if result.get("error") is not None:
        raise LiveForkReorgError(f"RPC {method}@{port} failed: {result['error']}")
    return result.get("result")


class Node:
    def __init__(
        self,
        name,
        p2p_port,
        rpc_port,
        p2p_host="127.0.0.1",
        rpc_host="127.0.0.1",
        command_prefix=(),
    ):
        self.name = name
        self.p2p_port = p2p_port
        self.rpc_port = rpc_port
        self.p2p_host = p2p_host
        self.rpc_host = rpc_host
        self.command_prefix = tuple(command_prefix)
        self.root = BASE / name
        self.data_dir = self.root / "data"
        self.config = self.root / "kairos.toml"
        self.proc = None
        self.log_handle = None
        self.log_path = None
        self.stopping = False

    @property
    def seed(self):
        return f"127.0.0.1:{self.p2p_port}"

    def start(self, label, mode="node", genesis=False, seeds=None):
        started = self.spawn(label, mode=mode, genesis=genesis, seeds=seeds)
        return self.wait_ready(label, started)

    def spawn(self, label, mode="node", genesis=False, seeds=None):
        require(self.proc is None or self.proc.poll() is not None, f"{self.name} already runs")
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = BASE / "logs" / f"{label}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            *self.command_prefix,
            str(NODE_BIN),
            "--mode",
            mode,
            "--config",
            str(self.config),
            "--data-dir",
            str(self.data_dir),
            "--p2p-listen",
            f"{self.p2p_host}:{self.p2p_port}",
            "--rpc-listen",
            f"{self.rpc_host}:{self.rpc_port}",
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
        self.stopping = False
        print(f"[start] {label} pid={self.proc.pid} mode={mode} genesis={genesis}", flush=True)
        return started

    def wait_ready(self, label, started, timeout=300):
        require(self.proc is not None, f"{label} has no process")
        assert self.proc is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise LiveForkReorgError(f"{label} exited during startup: {self.proc.returncode}")
            try:
                info = self.info(timeout=3)
                elapsed = time.monotonic() - started
                print(f"[ready] {label} height={info['height']} startup={elapsed:.3f}s", flush=True)
                return info, elapsed
            except LiveForkReorgError:
                time.sleep(0.5)
        raise LiveForkReorgError(f"{label} RPC startup timeout")

    def info(self, timeout=15):
        return rpc(self.rpc_port, "getChainInfo", timeout=timeout, host=self.rpc_host)

    def height(self):
        return int(self.info()["height"])

    def request_stop(self):
        if self.proc is None or self.proc.poll() is not None or self.stopping:
            return
        try:
            rpc(self.rpc_port, "stop", timeout=5, host=self.rpc_host)
        except LiveForkReorgError as error:
            print(f"[stop-request] {self.name}: {error}", flush=True)
        self.stopping = True
        print(f"[stop-request] {self.name}", flush=True)

    def finish_stop(self):
        if self.proc is None:
            self._close_log()
            return
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=75)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=10)
        print(f"[stopped] {self.name} code={self.proc.returncode}", flush=True)
        self._close_log()
        require(self.proc.returncode == 0, f"{self.name} exited with {self.proc.returncode}")

    def stop(self):
        self.request_stop()
        self.finish_stop()

    def _close_log(self):
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

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
        except (LiveForkReorgError, KeyError, TypeError) as error:
            last = str(error)
        time.sleep(interval)
    raise LiveForkReorgError(f"timeout waiting for {label}; last={last}")


def wait_mined(node, target, timeout=600):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        info = node.info()
        height = int(info["height"])
        if height != last:
            print(f"[mine] {node.name} height={height}/{target}", flush=True)
            last = height
        if height >= target:
            return info
        time.sleep(0.25)
    raise LiveForkReorgError(f"{node.name} did not mine h{target}; last={last}")


def mine_both_one_block(left, right, target, timeout=900):
    deadline = time.monotonic() + timeout
    results = {}
    last = {}
    while time.monotonic() < deadline and len(results) < 2:
        for node in (left, right):
            if node.name in results:
                continue
            info = node.info()
            height = int(info["height"])
            if last.get(node.name) != height:
                print(f"[fork-mine] {node.name} height={height}/{target}", flush=True)
                last[node.name] = height
            if height >= target:
                require(height == target, f"{node.name} overshot isolated target: {info}")
                results[node.name] = info
                node.request_stop()
        time.sleep(0.1)
    require(len(results) == 2, f"isolated miners did not both reach h{target}: {last}")
    left.finish_stop()
    right.finish_stop()
    return results


def exact_tip(left, right):
    a = left.info()
    b = right.info()
    if int(a["height"]) == int(b["height"]) and a["best_hash"] == b["best_hash"]:
        return {left.name: a["height"], right.name: b["height"], "hash": a["best_hash"]}
    return False


def exact_headers(left, right, through_height):
    matched = []
    for height in range(through_height + 1):
        a = rpc(left.rpc_port, "getBlockHeader", [height])
        b = rpc(right.rpc_port, "getBlockHeader", [height])
        if a is None or b is None or a["hash"] != b["hash"]:
            return False
        matched.append(a["hash"])
    return matched


def log_metrics(text):
    accepted = []
    for line in text.splitlines():
        if "block accepted" not in line:
            continue
        fields = {}
        for key in ("height", "txs", "history_step_ms", "pow_ms", "nonce_to_commit_ms"):
            match = re.search(rf"(?:^|\s){key}=(\d+)(?:\s|$)", line)
            fields[key] = int(match.group(1)) if match else None
        hash_match = re.search(r"(?:^|\s)hash=([0-9a-f]{64})(?:\s|$)", line)
        fields["hash"] = hash_match.group(1) if hash_match else None
        accepted.append(fields)
    return accepted


FAILURE_MARKERS = (
    " ERROR ",
    "panicked",
    "reorg failed",
    "P2P block rejected",
    "chained orphan apply failed",
)


def assert_clean_log(label, text):
    failures = [
        line for line in text.splitlines() if any(marker in line for marker in FAILURE_MARKERS)
    ]
    require(not failures, f"{label} contains failures: {failures[-8:]}")


def main():
    require(NODE_BIN.is_file(), f"release node is missing: {NODE_BIN}")
    require(not BASE.exists(), f"run directory already exists: {BASE}")
    ports = (BASE_PORT, BASE_PORT + 1, BASE_PORT + 10, BASE_PORT + 11)
    for port in ports:
        require(port_is_free(port), f"port occupied: {port}")
    (BASE / "logs").mkdir(parents=True)
    (RUN_PARENT / "LAST_FORK_REORG_RUN").write_text(str(BASE) + "\n")

    binary_hash = sha256(NODE_BIN)
    print(f"[run] {BASE}", flush=True)
    print(f"[binary] sha256={binary_hash} size={NODE_BIN.stat().st_size}", flush=True)
    left = Node("miner-a", BASE_PORT, BASE_PORT + 1)
    right = Node("miner-b", BASE_PORT + 10, BASE_PORT + 11)
    summary = {
        "run_dir": str(BASE),
        "binary_sha256": binary_hash,
        "binary_size": NODE_BIN.stat().st_size,
        "common_height": COMMON_HEIGHT,
        "fork_height": FORK_HEIGHT,
        "winner_height": WINNER_HEIGHT,
        "status": "running",
    }
    error = None
    try:
        # Build the common prefix only by mining it in this invocation.
        left.start("01-a-fresh-genesis-miner", mode="miner", genesis=True)
        common_info = wait_mined(left, COMMON_HEIGHT)
        left.stop()

        # Transfer that just-mined prefix over the production P2P protocol.
        left.start("02-a-common-prefix-server")
        right.start("03-b-fresh-common-prefix-sync", seeds=[left.seed])
        wait_value(
            "fresh miner B receives the exact common prefix",
            lambda: exact_tip(left, right),
            timeout=300,
        )
        common_headers = wait_value(
            "common-prefix hashes match",
            lambda: exact_headers(left, right, COMMON_HEIGHT),
            timeout=60,
        )
        right.stop()
        left.stop()

        # Mine one competing child on each disconnected miner.
        left.start("04-a-isolated-fork-miner", mode="miner", genesis=True)
        right.start("05-b-isolated-fork-miner", mode="miner", genesis=True)
        fork_infos = mine_both_one_block(left, right, FORK_HEIGHT)
        left_fork_hash = fork_infos[left.name]["best_hash"]
        right_fork_hash = fork_infos[right.name]["best_hash"]
        require(left_fork_hash != right_fork_hash, "isolated miners produced the same fork hash")
        print(
            f"[fork] height={FORK_HEIGHT} a={left_fork_hash} b={right_fork_hash}",
            flush=True,
        )

        # Give A strictly more cumulative work while B remains stopped.
        left.start("06-a-extends-winning-branch", mode="miner", genesis=True)
        winner_info = wait_mined(left, WINNER_HEIGHT)
        left.stop()

        # Reconnect as non-mining nodes so reorg behaviour is isolated from new work.
        left.start("07-a-winning-branch-server")
        right.start("08-b-reconnects-shorter-fork", seeds=[left.seed])
        wait_value(
            "fork peers connected",
            lambda: int(rpc(left.rpc_port, "getPeerCount")) >= 1
            and int(rpc(right.rpc_port, "getPeerCount")) >= 1,
            timeout=120,
        )
        reorg_started = time.monotonic()
        converged = wait_value(
            "shorter fork reorganizes to the heavier branch",
            lambda: exact_tip(left, right),
            timeout=180,
            interval=0.5,
        )
        reorg_elapsed = time.monotonic() - reorg_started
        require(int(converged[left.name]) == WINNER_HEIGHT, f"wrong converged height: {converged}")
        final_headers = wait_value(
            "every canonical hash matches after reorg",
            lambda: exact_headers(left, right, WINNER_HEIGHT),
            timeout=60,
        )

        right_text = right.log_text()
        left_text = left.log_text()
        require("reorg complete" in right_text, "shorter branch converged without reorg evidence")
        require("snapshot" not in "\n".join(
            line for line in right_text.splitlines() if "requesting snapshot" in line
        ), "shallow reorg unexpectedly requested a snapshot")
        assert_clean_log("winning branch node", left_text)
        assert_clean_log("shorter branch node", right_text)

        summary.update(
            {
                "status": "passed",
                "common_tip": common_info,
                "common_headers": common_headers,
                "fork_tips": fork_infos,
                "winner_tip_before_reconnect": winner_info,
                "converged_tip": converged,
                "canonical_headers": final_headers,
                "reorg_s": round(reorg_elapsed, 3),
                "a_genesis_mining": log_metrics(
                    (BASE / "logs" / "01-a-fresh-genesis-miner.log").read_text(errors="replace")
                ),
                "a_fork_mining": log_metrics(
                    (BASE / "logs" / "04-a-isolated-fork-miner.log").read_text(errors="replace")
                ),
                "b_fork_mining": log_metrics(
                    (BASE / "logs" / "05-b-isolated-fork-miner.log").read_text(errors="replace")
                ),
                "a_branch_extension": log_metrics(
                    (BASE / "logs" / "06-a-extends-winning-branch.log").read_text(errors="replace")
                ),
            }
        )
        print("[PASS] fresh-chain two-miner fork and shallow reorg", flush=True)
    except Exception as caught:
        error = caught
        summary["status"] = "failed"
        summary["error"] = str(caught)
        print(f"[FAIL] {caught}", flush=True)
    finally:
        right.stop()
        left.stop()
        (BASE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"[summary] {BASE / 'summary.json'}", flush=True)
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
