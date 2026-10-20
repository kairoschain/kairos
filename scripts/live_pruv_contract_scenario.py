#!/usr/bin/env python3
"""Qualify Pruv output on two isolated v2 wallets and a fresh receipt verifier.

Use a stopped, passed v2 fixture with branch-a and branch-b directories. The
scenario copies those directories into a new output directory and uses real
wallet authorization, P2P, mining, contract execution and receipt verification.
Run under unshare --user --map-root-user --net. Required environment:
NOID_PRUV_LIVE_DIR, NOID_PRUV_SOURCE_FIXTURE, NOID_PRUV_NODE, NOID_PRUV_Z3.
Optional: NOID_PRUV_BIN, NOID_PRUV_CLI, NOID_PRUV_MINER.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ["NOID_PRUV_LIVE_DIR"]).resolve()
os.environ["NOID_V2_LIVE_DIR"] = str(BASE)

import live_v2_contract_scenario as contracts

live = contracts.live
contracts.NODE = Path(os.environ["NOID_PRUV_NODE"]).resolve()
contracts.CLI = Path(os.environ.get("NOID_PRUV_CLI", ROOT / "target/release/kairos-cli")).resolve()
contracts.MINER = Path(os.environ.get("NOID_PRUV_MINER", ROOT / "target/release/kairos-miner")).resolve()
PRUV = Path(os.environ.get("NOID_PRUV_BIN", ROOT / "target/debug/pruv")).resolve()
Z3 = Path(os.environ["NOID_PRUV_Z3"]).resolve()
rpc = contracts.rpc


def main():
    devices = [line.split(":")[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:]]
    live.require(devices == ["lo"], "use a loopback-only network namespace")
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    source = Path(os.environ["NOID_PRUV_SOURCE_FIXTURE"]).resolve()
    prior = json.loads((source / "report.json").read_text())
    live.require(prior["status"] == "passed" and not prior.get("shutdown_errors"), "source fixture did not pass and stop")
    live.require(not BASE.exists(), "use a fresh output directory")
    for binary in (contracts.NODE, contracts.CLI, contracts.MINER, PRUV, Z3):
        live.require(binary.is_file(), f"missing executable: {binary}")
    for name in ("branch-a", "branch-b"):
        path = source / name / "data" / "mdbx.dat"
        live.require(path.is_file(), "source fixture has no chain database")
        live.require(subprocess.run(["fuser", "-s", str(path)]).returncode == 1,
                     "source database is open by another process")
    BASE.mkdir(parents=True)
    (BASE / "logs").mkdir()
    artifacts = BASE / "artifacts"
    artifacts.mkdir()
    for old, new in (("branch-a", "producer"), ("branch-b", "receiver")):
        subprocess.run(["cp", "-a", "--reflink=auto", "--sparse=always", str(source / old), str(BASE / new)], check=True)
    key = BASE / "mining.key"
    with key.open("x") as handle:
        handle.write(secrets.token_hex(32) + "\n")
    key.chmod(0o600)
    live.BASE = BASE
    producer = contracts.Node("producer", 27300, 27301, command_prefix=("taskset", "-c", "0,1,2,3,4,5,6,7"))
    receiver = contracts.Node("receiver", 27310, 27311, command_prefix=("taskset", "-c", "8,9,10,11"))
    verifier = contracts.Node("verifier", 27320, 27321, command_prefix=("taskset", "-c", "8,9,10,11"))
    report = {"status": "running", "blocks": [], "calls": [], "negative_cases": {}, "receipts": [],
              "source_fixture": str(source), "source_tip": prior["final_tip"],
              "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "binary_sha256": {p.name: live.sha256(p) for p in (contracts.NODE, contracts.CLI, contracts.MINER, PRUV, Z3)},
              "compiler_source_sha256": {str(p.relative_to(ROOT)): live.sha256(p)
                  for p in [ROOT / "noid_pruv/Cargo.toml", *sorted((ROOT / "noid_pruv/src").glob("*.rs"))]},
              "script_sha256": live.sha256(__file__)}

    def checkpoint(stage):
        report["stage"] = stage
        (BASE / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print("[stage] " + stage, flush=True)

    def converge():
        live.wait_value("wallet nodes select the same tip", lambda: live.exact_tip(producer, receiver), 600)

    def mine(count=1, pending=()):
        for txid in pending:
            live.wait_value("producer received transaction",
                            lambda txid=txid: rpc(producer, "getMempoolEntry", [txid]) is not None, 120)
        first = producer.height() + 1
        checkpoint(f"mine {first}..{first + count - 1}")
        started = time.monotonic()
        with (BASE / "logs" / f"miner-{first}.log").open("w") as log:
            result = subprocess.run([str(contracts.MINER), "--rpc", f"http://127.0.0.1:{producer.rpc_port}",
                "--key-file", str(key), "--threads", "2", "--rpc-timeout", "600", "--blocks", str(count)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=max(1800, count * 240))
        live.require(result.returncode == 0 and producer.height() == first + count - 1, "finite mining failed")
        converge()
        report["blocks"].append({"first": first, "last": producer.height(), "seconds": time.monotonic() - started})

    def pruv(command, source_path, params_path, output=None):
        args = [str(PRUV), command, str(source_path), "--params", str(params_path)]
        if command == "verify": args += ["--z3", str(Z3)]
        if output is not None: args += ["--out", str(output)]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
        live.require(result.returncode == 0, f"Pruv {command}: {result.stderr}")
        return result.stdout.strip()

    objects = {}

    def request(name, **changes):
        obj = objects[name]
        slot = obj["slot"]
        value = {"opening_hex": obj["info"]["opening_hex"], "slot_index": slot["slot_index"],
                 "creation_id": slot["creation_id"], "terminal": False, "payout": None, "fee_micronoid": 0}
        value.update(changes)
        return value

    def submit(node, name, terminal=False, amount=None):
        obj = objects[name]
        payload = request(name, terminal=terminal,
                          payout=None if amount is None else {"address": payee, "amount_micronoid": amount})
        preview = rpc(node, "previewObjectCall", [payload])
        payload.update(expected_authority=rpc(node, "walletActiveAddress")["address"],
                       expected_txid=preview["txid"], expected_call_height=preview["call_height"],
                       expected_recovery=preview["recovery"])
        result = rpc(node, "walletCallObject", [payload])
        live.require(result["transaction"]["txid"] == preview["txid"] and result["successor"] == preview["successor"],
                     "submitted call differs from its review")
        record = {"name": name, "node": node.name, "opening_hex": obj["info"]["opening_hex"],
                  "before_state": obj["info"]["state"], "preview": preview, "result": result}
        report["calls"].append(record)
        return record

    def confirmed(record):
        obj = objects[record["name"]]
        result = record["result"]
        txid = result["transaction"]["txid"]
        transaction = rpc(producer, "getTx", [txid])
        live.require(transaction is not None, "call did not confirm")
        record["height"] = transaction["height"]
        if result["successor"] is not None:
            obj["info"] = result["successor"]
            obj["slot"] = contracts.output_for(producer, txid, obj["info"]["address"])
            live.require(obj["slot"] is not None, "successor missing from the block")
            for node in (producer, receiver):
                status = rpc(node, "getObjectStatus", [obj["info"]["opening_hex"], obj["slot"]["slot_index"]])
                live.require(status["matches_opening"], "successor differs from authenticated State")
        for node in (producer, receiver):
            receipt = rpc(node, "exportObjectReceipt", [record["opening_hex"], txid])
            live.require(rpc(node, "verifyObjectReceipt", [receipt])["valid"], "confirmed call receipt failed")

    try:
        checkpoint("start current isolated node builds on copied v2 databases")
        receiver.start("01-receiver")
        producer.start("02-producer", mode="extminer", genesis=True, seeds=[receiver.seed])
        converge()
        live.require(producer.info() == prior["final_tip"], "copied source tip differs")
        protocol = rpc(producer, "getContractProtocol")
        live.require(protocol["abi_version"] == 3 and protocol["active_at_next_block"] and protocol["runtime_available"],
                     "native contract runtime is unavailable")
        report["protocol"] = protocol
        payer = rpc(producer, "walletActiveAddress")["address"]
        payee = rpc(receiver, "walletActiveAddress")["address"]
        live.require(payer != payee, "two separate wallet authorities required")
        start_height = producer.height()
        deadline = start_height + 6
        parameters = {"spending_key": payee, "recovery_key": payer, "recipient": payee,
                      "recover_at": str(deadline), "budget": "2000000", "max_fee": "1000000", "max_payment": "1000000"}
        params_path = artifacts / "params.json"
        params_path.write_text(json.dumps(parameters, indent=2) + "\n")
        budget = (ROOT / "noid_pruv/examples/budget.pruv").read_text()
        overflow = budget.replace("state calls: u64 = 0;", "state calls: u64 = 18446744073709551615;")
        texts = {"budget": budget, "instructions": (ROOT / "noid_pruv/examples/instructions.pruv").read_text(), "overflow": overflow}
        funding = []
        checkpoint("compile, verify and create the Pruv contracts through the existing CLI")
        report["contracts"] = {}
        for name, text in texts.items():
            source_path = artifacts / (name + ".pruv")
            source_path.write_text(text)
            definition_path = artifacts / (name + ".definition.json")
            checks = pruv("check", source_path, params_path)
            verified = pruv("verify", source_path, params_path)
            pruv("smt", source_path, params_path, artifacts / (name + ".smt2"))
            pruv("build", source_path, params_path, definition_path)
            object_path = artifacts / (name + ".opening.json")
            info = contracts.cli(producer, ["create", definition_path, "--out", object_path])
            contracts.cli(receiver, ["watch", object_path])
            payment = contracts.cli(producer, ["fund", object_path, "10"])
            funding.append(payment["txid"])
            objects[name] = {"info": info, "path": str(object_path), "funding": payment}
            report["contracts"][name] = {"check": checks, "verify": verified, "initial": info,
                "definition_sha256": live.sha256(definition_path), "source_sha256": live.sha256(source_path)}
        opcodes = {step["opcode"] for step in objects["instructions"]["info"]["program"]}
        live.require(opcodes == {"keep", "move", "add", "subtract", "min", "max", "less_than", "equal", "assert_equal", "assert_less_or_equal"},
                     "instruction example does not cover every ABI opcode")
        report["opcode_coverage"] = sorted(opcodes)
        mine(pending=funding)
        for obj in objects.values():
            obj["slot"] = contracts.output_for(producer, obj["funding"]["txid"], obj["info"]["address"])
            live.require(obj["slot"] is not None, "contract funding missing")
        checkpoint("reject unauthorized and invalid contract calls")
        negative = report["negative_cases"]
        valid_payout = {"address": payee, "amount_micronoid": 1000000}
        negative["wrong_authority"] = contracts.rejected(producer, "walletCallObject", [request("budget", payout=valid_payout)])
        negative["wrong_creation_id"] = contracts.rejected(receiver, "previewObjectCall", [request("budget", creation_id=objects["budget"]["slot"]["creation_id"] + 1)])
        negative["payout_ceiling"] = contracts.rejected(receiver, "previewObjectCall", [request("budget", payout={"address": payee, "amount_micronoid": 1000001})])
        negative["wrong_recipient"] = contracts.rejected(receiver, "previewObjectCall", [request("budget", payout={"address": payer, "amount_micronoid": 1})])
        negative["fee_ceiling"] = contracts.rejected(receiver, "previewObjectCall", [request("budget", fee_micronoid=1000001)])
        negative["early_close"] = contracts.rejected(receiver, "previewObjectCall", [request("budget", terminal=True)])
        negative["counter_overflow"] = contracts.rejected(receiver, "previewObjectCall", [request("overflow", payout=valid_payout)])
        first = [submit(receiver, "budget", amount=1000000), submit(receiver, "instructions", amount=1000000)]
        mine(pending=[record["result"]["transaction"]["txid"] for record in first])
        for record in first: confirmed(record)
        budget_fee = int(first[0]["preview"]["fee_micronoid"])
        live.require(objects["budget"]["info"]["state"] == [str(1000000 - budget_fee), "1"], "budget did not debit payout and fee")
        live.require(objects["instructions"]["info"]["state"][1] == "2", "guard snapshots or instruction order differ")
        stale_request = request("budget", payout={"address": payee, "amount_micronoid": 100000})
        old_preview = rpc(receiver, "previewObjectCall", [stale_request])
        stale_request.update(expected_authority=payee, expected_txid=old_preview["txid"],
                             expected_call_height=old_preview["call_height"], expected_recovery=old_preview["recovery"])
        second_instructions = submit(receiver, "instructions", amount=100000)
        mine(pending=[second_instructions["result"]["transaction"]["txid"]])
        confirmed(second_instructions)
        live.require(objects["instructions"]["info"]["state"][1] == "3", "persistent counters differ on the second call")
        negative["stale_review"] = contracts.rejected(receiver, "walletCallObject", [stale_request])
        second_budget = submit(receiver, "budget", amount=100000)
        mine(pending=[second_budget["result"]["transaction"]["txid"]])
        confirmed(second_budget)
        remaining = int(objects["budget"]["info"]["state"][0])
        negative["budget_exhaustion"] = contracts.rejected(receiver, "previewObjectCall", [request("budget", payout={"address": payee, "amount_micronoid": remaining + 1})])
        mine()
        live.require(producer.height() + 1 == deadline, "recovery boundary differs")
        checkpoint("recover exactly at the deadline, including the overflowing counter")
        closing = [submit(producer, name, terminal=True) for name in objects]
        live.require(all(record["preview"]["recovery"] for record in closing), "deadline did not select recovery")
        mine(pending=[record["result"]["transaction"]["txid"] for record in closing])
        for record in closing:
            confirmed(record)
            live.require(record["result"]["successor"] is None, "closed contract has a successor")
        checkpoint("advance normal retention beyond every Pruv call")
        mine(43)
        for record in report["calls"]:
            for node in (producer, receiver):
                live.require(rpc(node, "getBlockDetails", [record["height"]])["retained"] is None, "call body remains retained")
                live.require(rpc(node, "getBlock", [record["height"]]) is None, "raw call block remains available")
        checkpoint("restart wallet, bootstrap a fresh verifier and check pruned receipts")
        receiver.stop()
        receiver.start("03-receiver-restart", seeds=[producer.seed])
        converge()
        verifier.start("04-fresh-verifier", seeds=[producer.seed])
        live.wait_value("fresh verifier selects exact pruned tip", lambda: live.exact_tip(producer, verifier), 900)
        for index, record in enumerate(report["calls"]):
            txid = record["result"]["transaction"]["txid"]
            encoded = rpc(receiver, "exportObjectReceipt", [record["opening_hex"], txid])
            artifact = artifacts / (txid + ".receipt")
            artifact.write_bytes(bytes.fromhex(encoded))
            for node in (producer, receiver, verifier):
                checked = rpc(node, "verifyObjectReceipt", [encoded])
                live.require(checked["valid"] and checked["txid"] == txid, "portable receipt failed after pruning")
            imported = rpc(verifier, "walletImportObjectReceipt", [encoded, record["opening_hex"]])
            live.require(imported["valid"], "fresh wallet did not import the receipt")
            report["receipts"].append({"txid": txid, "bytes": artifact.stat().st_size,
                                       "sha256": live.sha256(artifact), "height": record["height"]})
            if index == 0:
                corrupted = bytearray.fromhex(encoded)
                corrupted[-1] ^= 1
                negative["damaged_receipt"] = contracts.rejected(verifier, "verifyObjectReceipt", [corrupted.hex()])
                negative["wrong_receipt_opening"] = contracts.rejected(verifier, "walletImportObjectReceipt", [encoded, objects["instructions"]["info"]["opening_hex"]])
        report.update(status="passed", final_tip=producer.info(), pruning="passed", fresh_verifier="passed")
        checkpoint("complete")
    except Exception as error:
        report.update(status="failed", error=str(error))
        checkpoint("failed")
        raise
    finally:
        for node in (producer, receiver, verifier): node.request_stop()
        for node in (producer, receiver, verifier):
            try: node.finish_stop()
            except Exception as error: report.setdefault("shutdown_errors", []).append(str(error))
        if report.get("shutdown_errors"): report["status"] = "failed"
        (BASE / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
