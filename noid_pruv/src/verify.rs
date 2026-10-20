// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors (rebrand to Kairos Chain).

//! Universal postconditions for successful native integer-program execution.

use std::{
    io::{Read, Write},
    path::Path,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

use noid_tx::experimental_object::integer_program::{
    Instruction, Opcode, Operand, PredicateSource,
};

use crate::{
    compile::{builtin, Environment},
    syntax::{Expr, ExprKind, Op, Type, Value},
    CompiledContract, Error,
};

fn word(value: u64) -> String {
    format!("(_ bv{value} 64)")
}
fn flag(condition: &str) -> String {
    format!("(ite {condition} {} {})", word(1), word(0))
}

fn context(operand: Operand, deadline: u64) -> String {
    match operand {
        Operand::BeforeDeadline => return flag(&format!("(bvult height {})", word(deadline))),
        Operand::AfterDeadline => return flag(&format!("(bvuge height {})", word(deadline))),
        Operand::Terminal => return flag("terminal"),
        Operand::HasPayout => return flag("has_payout"),
        Operand::Zero => return word(0),
        Operand::One => return word(1),
        Operand::Height => "height",
        Operand::Fee => "fee",
        Operand::Payout => "payout",
        Operand::Retained => "retained",
        Operand::InputAmount => "input_amount",
        Operand::PayoutOwner0 => "payout_owner0",
        Operand::PayoutOwner1 => "payout_owner1",
        Operand::PayoutOwner2 => "payout_owner2",
        Operand::PayoutOwner3 => "payout_owner3",
        Operand::InputCreationId => "input_creation_id",
        Operand::InputSlot => "input_slot",
        Operand::RetainedSlot => "retained_slot",
        Operand::PayoutSlot => "payout_slot",
        _ => unreachable!("registers and immediates are resolved separately"),
    }
    .to_owned()
}

fn read(operand: Operand, immediate: u64, registers: &[String; 4], deadline: u64) -> String {
    match operand {
        Operand::State0 => registers[0].clone(),
        Operand::State1 => registers[1].clone(),
        Operand::Scratch0 => registers[2].clone(),
        Operand::Scratch1 => registers[3].clone(),
        Operand::Immediate => word(immediate),
        _ => context(operand, deadline),
    }
}

struct Expression {
    term: String,
    checks: Vec<String>,
}

fn expression(
    expr: &Expr,
    env: &Environment,
    registers: &[String; 4],
    deadline: u64,
) -> Result<Expression, Error> {
    let result = |term| {
        Ok(Expression {
            term,
            checks: Vec::new(),
        })
    };
    match &expr.kind {
        ExprKind::Value(Value::U64(value)) => result(word(*value)),
        ExprKind::Value(Value::Bool(value)) => result(value.to_string()),
        ExprKind::Value(Value::Address(_)) => {
            Err(expr.span.error("addresses cannot appear in postconditions"))
        }
        ExprKind::Name(name) => {
            if let Some(value) = env.values.get(name) {
                return match value {
                    Value::U64(value) => result(word(*value)),
                    Value::Bool(value) => result(value.to_string()),
                    _ => Err(expr.span.error("addresses cannot appear in postconditions")),
                };
            }
            if let Some(register) = env.states.get(name) {
                return result(registers[*register as usize].clone());
            }
            let (operand, ty) =
                builtin(name).ok_or_else(|| expr.span.error("unknown postcondition name"))?;
            let term = context(operand, deadline);
            result(if ty == Type::Bool {
                format!("(= {term} {})", word(1))
            } else {
                term
            })
        }
        ExprKind::Old(name) => {
            let register = env
                .states
                .get(name)
                .ok_or_else(|| expr.span.error("old() requires a state variable"))?;
            result(format!("old_state{}", *register as usize))
        }
        ExprKind::Not(inner) => {
            let mut inner = expression(inner, env, registers, deadline)?;
            inner.term = format!("(not {})", inner.term);
            Ok(inner)
        }
        ExprKind::Binary(op, left, right) => {
            let left = expression(left, env, registers, deadline)?;
            let right = expression(right, env, registers, deadline)?;
            let mut checks = left.checks;
            checks.extend(right.checks);
            let (left, right) = (left.term, right.term);
            let term = match op {
                Op::Add => {
                    checks.push(format!("(bvule {left} (bvnot {right}))"));
                    format!("(bvadd {left} {right})")
                }
                Op::Subtract => {
                    checks.push(format!("(bvuge {left} {right})"));
                    format!("(bvsub {left} {right})")
                }
                Op::Min => format!("(let ((p_left {left}) (p_right {right})) (ite (bvule p_left p_right) p_left p_right))"),
                Op::Max => format!("(let ((p_left {left}) (p_right {right})) (ite (bvuge p_left p_right) p_left p_right))"),
                Op::Equal => format!("(= {left} {right})"),
                Op::NotEqual => format!("(distinct {left} {right})"),
                Op::Less => format!("(bvult {left} {right})"),
                Op::LessEqual => format!("(bvule {left} {right})"),
                Op::Greater => format!("(bvugt {left} {right})"),
                Op::GreaterEqual => format!("(bvuge {left} {right})"),
            };
            Ok(Expression { term, checks })
        }
    }
}

/// The first query requires a successful execution to exist. The second searches
/// for a successful execution violating at least one declared postcondition.
pub fn smt(contract: &CompiledContract) -> Result<String, Error> {
    if contract.source.ensures.is_empty() {
        return Err(Error::plain("declare at least one ensures postcondition"));
    }
    let mut text = String::from("; Pruv / Kairos contract ABI 3\n; Expected results: sat, unsat. Initial state is universally quantified.\n(set-logic QF_BV)\n(set-option :timeout 10000)\n");
    for name in [
        "old_state0",
        "old_state1",
        "height",
        "fee",
        "payout",
        "retained",
        "input_amount",
        "payout_owner0",
        "payout_owner1",
        "payout_owner2",
        "payout_owner3",
        "input_creation_id",
        "input_slot",
        "retained_slot",
        "payout_slot",
    ] {
        text.push_str(&format!("(declare-fun {name} () (_ BitVec 64))\n"));
    }
    for name in ["terminal", "has_payout"] {
        text.push_str(&format!("(declare-fun {name} () Bool)\n"));
    }
    for name in ["input_slot", "retained_slot", "payout_slot"] {
        text.push_str(&format!(
            "(assert (bvule {name} {}))\n",
            word(u32::MAX.into())
        ));
    }
    let deadline = contract.opening.deadline;
    let mut registers = [
        "old_state0".to_owned(),
        "old_state1".to_owned(),
        word(0),
        word(0),
    ];
    for (index, fields) in contract.opening.program.iter().enumerate() {
        let step = Instruction::from_fields(*fields)
            .ok_or_else(|| Error::plain("invalid instruction encoding"))?;
        let predicate = match step.predicate.source {
            PredicateSource::Always => word(1),
            PredicateSource::BeforeDeadline => context(Operand::BeforeDeadline, deadline),
            PredicateSource::Terminal => context(Operand::Terminal, deadline),
            PredicateSource::HasPayout => context(Operand::HasPayout, deadline),
            PredicateSource::Scratch0 => registers[2].clone(),
            PredicateSource::Scratch1 => registers[3].clone(),
        };
        text.push_str(&format!(
            "(assert (or (= {predicate} {}) (= {predicate} {})))\n",
            word(0),
            word(1)
        ));
        let active = format!(
            "(= {predicate} {})",
            word(u64::from(!step.predicate.inverted))
        );
        let left = read(step.left, step.immediate, &registers, deadline);
        let right = read(step.right, step.immediate, &registers, deadline);
        let destination = step.destination as usize;
        let previous = &registers[destination];
        let (value, check) = match step.opcode {
            Opcode::Keep => (previous.clone(), None),
            Opcode::Move => (left.clone(), None),
            Opcode::Add => (
                format!("(bvadd {left} {right})"),
                Some(format!("(bvule {left} (bvnot {right}))")),
            ),
            Opcode::Subtract => (
                format!("(bvsub {left} {right})"),
                Some(format!("(bvuge {left} {right})")),
            ),
            Opcode::Min => (format!("(ite (bvule {left} {right}) {left} {right})"), None),
            Opcode::Max => (format!("(ite (bvuge {left} {right}) {left} {right})"), None),
            Opcode::LessThan => (flag(&format!("(bvult {left} {right})")), None),
            Opcode::Equal => (flag(&format!("(= {left} {right})")), None),
            Opcode::AssertEqual => (previous.clone(), Some(format!("(= {left} {right})"))),
            Opcode::AssertLessOrEqual => {
                (previous.clone(), Some(format!("(bvule {left} {right})")))
            }
        };
        if let Some(check) = check {
            text.push_str(&format!("(assert (=> {active} {check}))\n"));
        }
        let name = format!("r{index}_{destination}");
        text.push_str(&format!(
            "(define-fun {name} () (_ BitVec 64) (ite {active} {value} {previous}))\n"
        ));
        registers[destination] = name;
    }
    text.push_str("; Feasibility of successful integer-program execution.\n(check-sat)\n");
    let mut conditions = Vec::new();
    for condition in &contract.source.ensures {
        let expr = expression(condition, &contract.env, &registers, deadline)?;
        conditions.extend(expr.checks);
        conditions.push(expr.term);
    }
    text.push_str(&format!(
        "; Search for a postcondition violation.\n(assert (not (and {})))\n(check-sat)\n",
        conditions.join(" ")
    ));
    Ok(text)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Verified,
    Counterexample,
    Infeasible,
}

/// Z3 runs locally with a 10-second limit per query and a 30-second wall limit.
pub fn verify(contract: &CompiledContract, z3: &Path) -> Result<Verdict, Error> {
    verify_with_limit(contract, z3, Duration::from_secs(30))
}

pub(crate) fn verify_with_limit(
    contract: &CompiledContract,
    z3: &Path,
    wall_limit: Duration,
) -> Result<Verdict, Error> {
    let query = smt(contract)?;
    let mut child = Command::new(z3)
        .args(["-in", "-smt2"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| Error::plain(format!("start Z3: {error}")))?;
    let mut stdout = child.stdout.take().expect("piped stdout");
    let mut stderr = child.stderr.take().expect("piped stderr");
    let output = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout
            .by_ref()
            .take(1024 * 1024 + 1)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let errors = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stderr
            .by_ref()
            .take(64 * 1024 + 1)
            .read_to_end(&mut bytes)
            .map(|_| bytes)
    });
    let mut input = child.stdin.take().expect("piped stdin");
    let writer = std::thread::spawn(move || input.write_all(query.as_bytes()));
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if started.elapsed() < wall_limit => {
                std::thread::sleep(Duration::from_millis(20))
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(Error::plain("Z3 exceeded its wall limit"));
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(Error::plain(format!("wait for Z3: {error}")));
            }
        }
    };
    let input = writer
        .join()
        .map_err(|_| Error::plain("Z3 input worker failed"))?;
    let output = output
        .join()
        .map_err(|_| Error::plain("Z3 output worker failed"))?
        .map_err(|error| Error::plain(error.to_string()))?;
    let errors = errors
        .join()
        .map_err(|_| Error::plain("Z3 error worker failed"))?
        .map_err(|error| Error::plain(error.to_string()))?;
    let status = status?;
    input.map_err(|error| Error::plain(format!("write Z3 query: {error}")))?;
    if output.len() > 1024 * 1024
        || errors.len() > 64 * 1024
        || !status.success()
        || !errors.is_empty()
    {
        return Err(Error::plain(format!(
            "Z3 failed: {}",
            String::from_utf8_lossy(&errors)
        )));
    }
    match String::from_utf8_lossy(&output)
        .split_whitespace()
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["sat", "unsat"] => Ok(Verdict::Verified),
        ["sat", "sat"] => Ok(Verdict::Counterexample),
        ["unsat", "unsat"] => Ok(Verdict::Infeasible),
        _ => Err(Error::plain("Z3 did not conclusively answer both queries")),
    }
}
