#!/usr/bin/env python3
# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportMissingTypeArgument=false, reportUnusedCallResult=false, reportUnusedVariable=false
import json
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_BIN = ROOT / "target" / "release" / "kairos"
CLI_BIN = ROOT / "target" / "release" / "kairos-cli"
BASE = ROOT / "target" / "live-tests" / "cli-wallet"
LOGS = BASE / "logs"


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
                "\n\n===== START %s %s =====\n"
                % (self.name, time.strftime("%Y-%m-%d %H:%M:%S"))
            ).encode()
        )
        self.proc = subprocess.Popen(
            args, cwd=ROOT, stdout=self.log_file, stderr=subprocess.STDOUT
        )
        print(
            f"[start] {self.name}: pid={self.proc.pid} rpc={self.rpc_url} p2p={self.seed_addr}",
            flush=True,
        )
        wait_until(
            f"{self.name} RPC ready",
            lambda: rpc(self.rpc_url, "getChainInfo"),
            timeout=45,
            interval=0.5,
        )

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return
        print(f"[stop] {self.name}", flush=True)
        try:
            rpc(self.rpc_url, "stop", timeout=3)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.log_file:
            self.log_file.close()
            self.log_file = None

    def height(self):
        return int(rpc(self.rpc_url, "getChainInfo")["height"])

    def info(self):
        return rpc(self.rpc_url, "getChainInfo")


def rpc(url, method, params=None, timeout=8):
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": f"paranoid_{method}",
            "params": params or [],
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if "error" in payload:
        raise LiveTestError(f"RPC {method} error: {payload['error']}")
    return payload.get("result")


def cli(node, args, json_mode=False, timeout=120, check=True):
    cmd = [str(CLI_BIN), "--rpc", node.rpc_url]
    if json_mode:
        cmd.append("--json")
    cmd.extend(args)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    print(f"[cli] {node.name}$ {' '.join(args)} -> {proc.returncode}", flush=True)
    if check and proc.returncode != 0:
        raise LiveTestError(
            f"CLI failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def cli_json(node, args, timeout=120):
    out, err, _ = cli(node, args, json_mode=True, timeout=timeout)
    if not out:
        raise LiveTestError(
            f"CLI JSON command produced no stdout: {args}; stderr={err}"
        )
    return json.loads(out)


def assert_contains(text, needle, label):
    if needle not in text:
        raise LiveTestError(f"{label}: expected {needle!r} in output:\n{text}")


def assert_true(cond, msg):
    if not cond:
        raise LiveTestError(msg)


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


def same_tip(nodes):
    infos = {n.name: n.info() for n in nodes}
    hashes = {i["best_hash"] for i in infos.values()}
    heights = {int(i["height"]) for i in infos.values()}
    if len(hashes) == 1 and len(heights) == 1:
        return {k: (v["height"], v["best_hash"][:12]) for k, v in infos.items()}
    return False


def extract_tx_hash(send_output):
    m = re.search(r"[0-9a-f]{64}", send_output)
    if not m:
        raise LiveTestError(
            f"could not extract tx hash from send output:\n{send_output}"
        )
    return m.group(0)


def tamper_hex(hex_str):
    last = hex_str[-1]
    return hex_str[:-1] + ("0" if last != "0" else "1")


def cleanup(nodes):
    for n in reversed(nodes):
        try:
            n.stop()
        except Exception as e:
            print(f"[cleanup] {n.name}: {e}", flush=True)


def main():
    if not NODE_BIN.exists() or not CLI_BIN.exists():
        raise LiveTestError(
            "release binaries missing; run cargo build --release -p noid_node --bin kairos --bin kairos-cli"
        )
    if BASE.exists():
        shutil.rmtree(BASE)
    LOGS.mkdir(parents=True, exist_ok=True)

    n1 = Node("node1-cli-miner", 19500, 19501, mode="miner", genesis=True)
    n2 = Node("node2-cli-relay", 19510, 19511, mode="node", seed=[n1.seed_addr])
    n3 = Node("node3-cli-wallet", 19520, 19521, mode="node", seed=[n2.seed_addr])
    nodes = [n1, n2, n3]
    started = []
    tx_hashes = []

    try:
        print("\n=== CLI Scenario 1: start network and sync relays ===", flush=True)
        n1.start()
        started.append(n1)
        wait_until(
            "node1 mines 20 blocks",
            lambda: n1.height() if n1.height() >= 20 else False,
            timeout=480,
            interval=2,
        )
        n2.start()
        started.append(n2)
        wait_until("node2 syncs", lambda: same_tip([n1, n2]), timeout=240, interval=3)
        n3.start()
        started.append(n3)
        wait_until(
            "node3 syncs via node2", lambda: same_tip(nodes), timeout=240, interval=3
        )

        print("\n=== CLI Scenario 2: basic user command output ===", flush=True)
        out, _, _ = cli(n1, ["status"])
        assert_contains(out, "Paranoid node status", "status output")
        assert_contains(out, "Height", "status output")
        assert_contains(out, "Mempool", "status output")

        out, _, _ = cli(n1, ["mining"])
        assert_contains(out, "Mining info", "mining output")
        assert_contains(out, "Block reward", "mining output")

        out, _, _ = cli(n2, ["peers"])
        assert_contains(out, "Connected peers", "peers output")
        assert_contains(out, "Count", "peers output")

        out, _, _ = cli(n1, ["history-step-terminal"])
        assert_contains(out, "HistoryStep terminal", "HistoryStep terminal output")
        assert_contains(out, "Size", "HistoryStep terminal output")

        out, _, _ = cli(n1, ["state"])
        assert_contains(out, "UTXO state", "state output")
        assert_contains(out, "Active UTXOs", "state output")

        out, _, _ = cli(n1, ["estimate-fee", "2"])
        assert_contains(out, "Fee estimate", "estimate-fee output")
        assert_contains(out, "Min relay fee", "estimate-fee output")

        print(
            "\n=== CLI Scenario 3: address generation/list/validation ===", flush=True
        )
        out, _, _ = cli(n3, ["address"])
        assert_contains(out, "Active address [index=0]", "address output")
        assert_contains(out, "o1", "address output")

        new1 = cli_json(n3, ["address", "--new"])
        new2 = cli_json(n3, ["address", "--new"])
        addr1 = new1["address"]
        addr2 = new2["address"]
        assert_true(
            addr1.startswith("o1") and addr2.startswith("o1") and addr1 != addr2,
            "fresh addresses are invalid or not unique",
        )
        assert_true(
            new2["key_index"] == new1["key_index"] + 1,
            "fresh address key indexes are not consecutive",
        )

        out, _, _ = cli(n3, ["address", "--new"])
        assert_contains(out, "New address", "address --new output")
        assert_contains(out, "o1", "address --new output")

        out, _, _ = cli(n3, ["address", "--list"])
        assert_contains(out, "Wallet addresses", "address --list output")
        assert_contains(out, "locally generated address(es)", "address --list output")

        out, _, _ = cli(n3, ["validate", addr1])
        assert_contains(out, "Address validation", "validate output")
        assert_contains(out, "Valid address", "validate output")
        assert_contains(out, "hex", "validate output")

        print("\n=== CLI Scenario 4: balances/utxos before receiving ===", flush=True)
        cli(n1, ["scan"], timeout=180)
        bal1 = cli_json(n1, ["balance"])
        assert_true(
            bal1["spendable_micronoid"] >= 100_000_000,
            f"node1 spendable too low: {bal1}",
        )
        out, _, _ = cli(n1, ["balance"])
        assert_contains(out, "Wallet balance", "balance output")
        assert_contains(out, "Balance:", "balance output")
        out, _, _ = cli(n1, ["utxos"])
        assert_contains(out, "Wallet UTXOs", "utxos output")
        assert_contains(out, "TOTAL", "utxos output")

        out, _, _ = cli(n3, ["balance"])
        assert_contains(out, "No UTXOs found", "empty balance output")

        print(
            "\n=== CLI Scenario 5: send to two fresh addresses, mempool output, confirmation ===",
            flush=True,
        )
        send_out, _, _ = cli(n1, ["send", addr1, "1.250000"], timeout=240)
        assert_contains(send_out, "Transaction submitted", "send output")
        assert_contains(send_out, "Amount", "send output")
        assert_contains(send_out, "Fee", "send output")
        tx1 = extract_tx_hash(send_out)
        tx_hashes.append(tx1)

        wait_until(
            "tx1 visible in all mempools via CLI JSON",
            lambda: all(cli_json(n, ["mempool"])["size"] >= 1 for n in nodes),
            timeout=120,
            interval=2,
        )
        out, _, _ = cli(n2, ["mempool"])
        assert_contains(out, "Mempool", "mempool output")
        assert_contains(out, "Pending", "mempool output")
        assert_contains(out, "proof", "mempool output")
        out, _, _ = cli(n2, ["mempool-tx", tx1])
        assert_contains(out, "Mempool transaction", "mempool-tx output")
        assert_contains(out, "Minimum proof class", "mempool-tx output")
        assert_contains(out, "attached", "mempool-tx output")

        wait_until(
            "tx1 confirmed",
            lambda: (
                cli_json(n1, ["tx", tx1])
                if cli_json(n1, ["tx", tx1]) is not None
                else False
            ),
            timeout=480,
            interval=4,
        )
        wait_until(
            "all mempools empty after tx1",
            lambda: all(cli_json(n, ["mempool"])["size"] == 0 for n in nodes),
            timeout=180,
            interval=3,
        )
        wait_until(
            "nodes converge after tx1", lambda: same_tip(nodes), timeout=180, interval=3
        )

        send2_out, _, _ = cli(n1, ["send", addr2, "0.750000"], timeout=240)
        tx2 = extract_tx_hash(send2_out)
        tx_hashes.append(tx2)
        wait_until(
            "tx2 confirmed",
            lambda: (
                cli_json(n1, ["tx", tx2])
                if cli_json(n1, ["tx", tx2]) is not None
                else False
            ),
            timeout=480,
            interval=4,
        )
        wait_until(
            "nodes converge after tx2", lambda: same_tip(nodes), timeout=180, interval=3
        )

        print(
            "\n=== CLI Scenario 6: recipient scan, balance, per-address UTXOs, history ===",
            flush=True,
        )
        cli_json(n3, ["address", "--use", str(new1["key_index"])])
        scan3 = cli_json(n3, ["scan"], timeout=180)
        assert_true(
            scan3["balance_micronoid"] == 1_250_000
            and scan3["found_utxos"] == 1,
            f"first recipient scan did not find the exact funds: {scan3}",
        )
        bal3 = cli_json(n3, ["balance"])
        assert_true(
            bal3["balance_micronoid"] == 1_250_000 and bal3["utxo_count"] == 1,
            f"first recipient balance wrong: {bal3}",
        )
        out, _, _ = cli(n3, ["balance"])
        assert_contains(out, "1.250000", "first recipient balance output")

        cli_json(n3, ["address", "--use", str(new2["key_index"])])
        scan3 = cli_json(n3, ["scan"], timeout=180)
        assert_true(
            scan3["balance_micronoid"] == 750_000
            and scan3["found_utxos"] == 1,
            f"second recipient scan did not find the exact funds: {scan3}",
        )
        bal3 = cli_json(n3, ["balance"])
        assert_true(
            bal3["balance_micronoid"] == 750_000 and bal3["utxo_count"] == 1,
            f"second recipient balance wrong: {bal3}",
        )
        out, _, _ = cli(n3, ["balance"])
        assert_contains(out, "0.750000", "second recipient balance output")

        slots1 = cli_json(n3, ["utxos-of", addr1])
        slots2 = cli_json(n3, ["utxos-of", addr2])
        assert_true(
            len(slots1) >= 1 and len(slots2) >= 1,
            f"per-address UTXOs missing: {slots1} {slots2}",
        )
        out, _, _ = cli(n3, ["utxos-of", addr1])
        assert_contains(out, "UTXOs of", "utxos-of output")
        assert_contains(out, "TOTAL", "utxos-of output")

        hist1 = cli_json(n1, ["history"])
        sent_entries = [h for h in hist1 if h.get("direction") == "sent"]
        assert_true(
            len(sent_entries) >= 2, f"sender history missing sent entries: {hist1}"
        )
        out, _, _ = cli(n1, ["history", "--last", "5"])
        assert_contains(out, "sent", "sender history output")

        print(
            "\n=== CLI Scenario 7: receipts/export/verify/tamper failure ===",
            flush=True,
        )
        receipt1, _, _ = cli(n1, ["receipt", tx1])
        receipt1 = receipt1.strip()
        assert_true(
            len(receipt1) > 100
            and all(c in "0123456789abcdefABCDEF" for c in receipt1),
            "receipt output is not hex",
        )
        out, _, _ = cli(n1, ["verify", receipt1])
        assert_contains(out, "Receipt verification", "verify output")
        assert_contains(out, "Receipt is VALID and canonical", "verify output")
        verify_json = cli_json(n2, ["verify", receipt1])
        assert_true(
            verify_json["confirmed"]
            and verify_json["canonical"]
            and verify_json["merkle_valid"],
            f"verify JSON wrong: {verify_json}",
        )

        bad_receipt = tamper_hex(receipt1)
        bad_out, bad_err, bad_code = cli(n2, ["verify", bad_receipt], check=False)
        assert_true(bad_code != 0, "tampered receipt unexpectedly verified")
        assert_true(
            "Receipt INVALID" in bad_out
            or "Receipt verification failed" in bad_err
            or "Error:" in bad_err,
            f"tampered verify output unexpected\nstdout={bad_out}\nstderr={bad_err}",
        )

        print(
            "\n=== CLI Scenario 8: final exact convergence and command JSON sanity ===",
            flush=True,
        )
        wait_until(
            "final exact convergence", lambda: same_tip(nodes), timeout=180, interval=3
        )
        for n in nodes:
            status = cli_json(n, ["status"])
            mempool = cli_json(n, ["mempool"])
            assert_true(mempool["size"] == 0, f"{n.name} mempool not empty: {mempool}")
            print(
                f"[final] {n.name} height={status['height']} hash={status['best_hash'][:12]} mempool={mempool['size']}",
                flush=True,
            )

        summary = {
            "final": {
                n.name: {
                    "status": cli_json(n, ["status"]),
                    "balance": cli_json(n, ["balance"]),
                }
                for n in nodes
            },
            "tx_hashes": tx_hashes,
            "recipient_addresses": [addr1, addr2],
        }
        (BASE / "summary.json").write_text(json.dumps(summary, indent=2))
        print(f"[summary] wrote {BASE / 'summary.json'}", flush=True)
        print("CLI LIVE TESTS PASSED", flush=True)
    except Exception:
        print("\n=== CLI LIVE TEST FAILURE ===", flush=True)
        for n in started:
            if n.log_path.exists():
                print(f"\n--- tail {n.name} ---")
                print(
                    "\n".join(
                        n.log_path.read_text(errors="replace").splitlines()[-100:]
                    )
                )
        raise
    finally:
        cleanup(started)


if __name__ == "__main__":
    main()
