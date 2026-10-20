// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors (rebrand to Kairos Chain).

use std::collections::{BTreeMap, BTreeSet};

use noid_poseidon2b::primitives::Address;
use noid_tx::experimental_object::{
    integer_program::{
        self as native, Instruction, Opcode, Operand, Predicate, PredicateSource, Register,
    },
    policy, ObjectOpening, ObjectRules, OBJECT_VERSION,
};
use serde_json::{json, Value as Json};

use crate::syntax::{self, Expr, ExprKind, Op, Source, Span, Statement, Type, Value};
use crate::Error;

#[derive(Clone, Debug)]
pub struct CompiledContract {
    pub(crate) name: String,
    pub(crate) instructions: usize,
    pub(crate) state_registers: usize,
    pub(crate) opening: ObjectOpening,
    pub(crate) source: Source,
    pub(crate) env: Environment,
}

impl CompiledContract {
    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn instructions(&self) -> usize {
        self.instructions
    }

    pub fn state_registers(&self) -> usize {
        self.state_registers
    }

    pub fn opening(&self) -> &ObjectOpening {
        &self.opening
    }

    /// Accepted by `kairos-cli contract create` and `paranoid_createObject`.
    pub fn definition(&self) -> Json {
        let opening = &self.opening;
        let modes = opening.rules.modes;
        let program: Vec<_> = opening.program[..self.instructions]
            .iter()
            .map(|fields| {
                let step = Instruction::from_fields(*fields).expect("compiler validated encoding");
                json!({
                    "opcode": step.opcode, "destination": step.destination,
                    "left": step.left, "right": step.right,
                    "predicate": step.predicate, "immediate": step.immediate.to_string(),
                })
            })
            .collect();
        json!({"kind": "custom_program", "definition": {
            "state": native::unpack_state(opening.state).map(|value| value.to_string()),
            "program": program,
            "claim_authority": opening.claim_authority.to_bech32(),
            "recovery_authority": opening.recovery_authority.to_bech32(),
            "claim_recipient": opening.claim_recipient.to_bech32(),
            "recovery_recipient": opening.recovery_recipient.to_bech32(),
            "deadline_height": opening.deadline,
            "max_fee_micronoid": opening.rules.max_fee,
            "max_payout_micronoid": opening.rules.max_payout,
            "min_retained_micronoid": opening.rules.min_retained,
            "claim_can_continue": modes & policy::CLAIM_CONTINUE != 0,
            "claim_can_close": modes & policy::CLAIM_CLOSE != 0,
            "recovery_can_continue": modes & policy::RECOVERY_CONTINUE != 0,
            "recovery_can_close": modes & policy::RECOVERY_CLOSE != 0,
            "unrestricted_payout_recipient": modes & policy::ANY_PAYOUT_RECIPIENT != 0,
        }})
    }

    pub fn postconditions(&self) -> usize {
        self.source.ensures.len()
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Environment {
    pub values: BTreeMap<String, Value>,
    pub states: BTreeMap<String, Register>,
}

pub(crate) fn builtin(name: &str) -> Option<(Operand, Type)> {
    use Operand::*;
    Some(match name {
        "height" => (Height, Type::U64),
        "fee" => (Fee, Type::U64),
        "payout" => (Payout, Type::U64),
        "retained" => (Retained, Type::U64),
        "input_amount" => (InputAmount, Type::U64),
        "before_deadline" => (BeforeDeadline, Type::Bool),
        "after_deadline" => (AfterDeadline, Type::Bool),
        "terminal" => (Terminal, Type::Bool),
        "has_payout" => (HasPayout, Type::Bool),
        "payout_owner0" => (PayoutOwner0, Type::U64),
        "payout_owner1" => (PayoutOwner1, Type::U64),
        "payout_owner2" => (PayoutOwner2, Type::U64),
        "payout_owner3" => (PayoutOwner3, Type::U64),
        "input_creation_id" => (InputCreationId, Type::U64),
        "input_slot" => (InputSlot, Type::U64),
        "retained_slot" => (RetainedSlot, Type::U64),
        "payout_slot" => (PayoutSlot, Type::U64),
        _ => return None,
    })
}

fn operand(register: Register) -> Operand {
    match register {
        Register::State0 => Operand::State0,
        Register::State1 => Operand::State1,
        Register::Scratch0 => Operand::Scratch0,
        Register::Scratch1 => Operand::Scratch1,
    }
}

fn reserved(name: &str) -> bool {
    builtin(name).is_some()
        || matches!(
            name,
            "contract"
                | "param"
                | "const"
                | "state"
                | "policy"
                | "call"
                | "when"
                | "require"
                | "ensures"
                | "old"
                | "min"
                | "max"
                | "true"
                | "false"
                | "u64"
                | "bool"
                | "address"
                | "state0"
                | "state1"
                | "scratch0"
                | "scratch1"
        )
}

impl Environment {
    fn declare(&self, name: &str, span: Span) -> Result<(), Error> {
        if reserved(name) {
            return Err(span.error(format!("'{name}' is reserved")));
        }
        if self.values.contains_key(name) || self.states.contains_key(name) {
            return Err(span.error(format!("duplicate declaration '{name}'")));
        }
        Ok(())
    }

    pub(crate) fn ty(&self, expr: &Expr, old: bool) -> Result<Type, Error> {
        let error = |message: &str| expr.span.error(message);
        match &expr.kind {
            ExprKind::Value(value) => Ok(value.ty()),
            ExprKind::Name(name) => self
                .values
                .get(name)
                .map(Value::ty)
                .or_else(|| self.states.get(name).map(|_| Type::U64))
                .or_else(|| builtin(name).map(|(_, ty)| ty))
                .ok_or_else(|| error(&format!("unknown name '{name}'"))),
            ExprKind::Old(name) if old && self.states.contains_key(name) => Ok(Type::U64),
            ExprKind::Old(_) => Err(error(
                "old() is available for state variables in ensures only",
            )),
            ExprKind::Not(value) => {
                if self.ty(value, old)? != Type::Bool {
                    return Err(error("'!' requires bool"));
                }
                Ok(Type::Bool)
            }
            ExprKind::Binary(op, left, right) => {
                let (left, right) = (self.ty(left, old)?, self.ty(right, old)?);
                if matches!(op, Op::Equal | Op::NotEqual)
                    && left == Type::Bool
                    && right == Type::Bool
                {
                    return Ok(Type::Bool);
                }
                if left != Type::U64 || right != Type::U64 {
                    return Err(error("operator requires u64 operands"));
                }
                Ok(
                    if matches!(op, Op::Add | Op::Subtract | Op::Min | Op::Max) {
                        Type::U64
                    } else {
                        Type::Bool
                    },
                )
            }
        }
    }

    fn constant(&self, expr: &Expr) -> bool {
        match &expr.kind {
            ExprKind::Value(_) => true,
            ExprKind::Name(name) => self.values.contains_key(name),
            ExprKind::Old(_) => false,
            ExprKind::Not(value) => self.constant(value),
            ExprKind::Binary(_, left, right) => self.constant(left) && self.constant(right),
        }
    }

    pub(crate) fn value(&self, expr: &Expr) -> Result<Value, Error> {
        match &expr.kind {
            ExprKind::Value(Value::Address(text)) => Address::parse(text)
                .map(|address| Value::Address(address.to_bech32()))
                .map_err(|error| expr.span.error(error.to_string())),
            ExprKind::Value(value) => Ok(value.clone()),
            ExprKind::Name(name) => self.values.get(name).cloned().ok_or_else(|| {
                expr.span
                    .error(format!("'{name}' is not a compile-time value"))
            }),
            ExprKind::Not(value) => match self.value(value)? {
                Value::Bool(value) => Ok(Value::Bool(!value)),
                _ => Err(expr.span.error("'!' requires bool")),
            },
            ExprKind::Binary(op, left, right) => {
                let (left, right) = (self.value(left)?, self.value(right)?);
                if let (Value::Bool(left), Value::Bool(right)) = (&left, &right) {
                    return match op {
                        Op::Equal => Ok(Value::Bool(left == right)),
                        Op::NotEqual => Ok(Value::Bool(left != right)),
                        _ => Err(expr.span.error("operator requires u64 operands")),
                    };
                }
                let (Value::U64(left), Value::U64(right)) = (left, right) else {
                    return Err(expr.span.error("operator requires u64 operands"));
                };
                Ok(match op {
                    Op::Add => Value::U64(
                        left.checked_add(right)
                            .ok_or_else(|| expr.span.error("constant addition overflows u64"))?,
                    ),
                    Op::Subtract => {
                        Value::U64(left.checked_sub(right).ok_or_else(|| {
                            expr.span.error("constant subtraction underflows u64")
                        })?)
                    }
                    Op::Min => Value::U64(left.min(right)),
                    Op::Max => Value::U64(left.max(right)),
                    Op::Equal => Value::Bool(left == right),
                    Op::NotEqual => Value::Bool(left != right),
                    Op::Less => Value::Bool(left < right),
                    Op::LessEqual => Value::Bool(left <= right),
                    Op::Greater => Value::Bool(left > right),
                    Op::GreaterEqual => Value::Bool(left >= right),
                })
            }
            ExprKind::Old(_) => Err(expr.span.error("old() is not a compile-time value")),
        }
    }
}

#[derive(Clone, Copy)]
struct Word {
    operand: Operand,
    immediate: u64,
    temporary: Option<Register>,
}

impl Word {
    fn literal(value: u64) -> Self {
        Self {
            operand: match value {
                0 => Operand::Zero,
                1 => Operand::One,
                _ => Operand::Immediate,
            },
            immediate: value,
            temporary: None,
        }
    }

    fn register(register: Register, temporary: bool) -> Self {
        Self {
            operand: operand(register),
            immediate: 0,
            temporary: temporary.then_some(register),
        }
    }
}

struct Lowering<'a> {
    env: &'a Environment,
    program: Vec<Instruction>,
    scratch: [bool; 2],
}

impl Lowering<'_> {
    fn allocate(&mut self, span: Span) -> Result<Register, Error> {
        for (index, used) in self.scratch.iter_mut().enumerate() {
            if !*used {
                *used = true;
                return Ok(if index == 0 {
                    Register::Scratch0
                } else {
                    Register::Scratch1
                });
            }
        }
        Err(span.error("expression needs more than two scratch registers; simplify it or split the state update"))
    }

    fn release(&mut self, register: Register) {
        match register {
            Register::Scratch0 => self.scratch[0] = false,
            Register::Scratch1 => self.scratch[1] = false,
            _ => {}
        }
    }

    fn emit(
        &mut self,
        opcode: Opcode,
        destination: Register,
        left: Word,
        right: Word,
        predicate: Predicate,
        span: Span,
    ) -> Result<(), Error> {
        if self.program.len() == native::PROGRAM_STEPS {
            return Err(span.error("compiled program exceeds ABI 3's 16 instructions"));
        }
        if left.operand == Operand::Immediate
            && right.operand == Operand::Immediate
            && left.immediate != right.immediate
        {
            return Err(span.error("instruction requires two distinct immediate values"));
        }
        let immediate = if left.operand == Operand::Immediate {
            left.immediate
        } else if right.operand == Operand::Immediate {
            right.immediate
        } else {
            0
        };
        self.program.push(
            Instruction::new(opcode, destination, left.operand, right.operand, immediate)
                .when(predicate),
        );
        Ok(())
    }

    fn word(&mut self, expr: &Expr, predicate: Predicate) -> Result<Word, Error> {
        if self.env.constant(expr) {
            return match self.env.value(expr)? {
                Value::U64(value) => Ok(Word::literal(value)),
                Value::Bool(value) => Ok(Word::literal(u64::from(value))),
                _ => Err(expr.span.error("addresses belong in the policy")),
            };
        }
        if let ExprKind::Name(name) = &expr.kind {
            if let Some(register) = self.env.states.get(name) {
                return Ok(Word::register(*register, false));
            }
            if let Some((operand, _)) = builtin(name) {
                return Ok(Word {
                    operand,
                    immediate: 0,
                    temporary: None,
                });
            }
        }
        self.expression(expr, None, predicate)
    }

    fn expression(
        &mut self,
        expr: &Expr,
        target: Option<Register>,
        predicate: Predicate,
    ) -> Result<Word, Error> {
        if self.env.constant(expr) || matches!(&expr.kind, ExprKind::Name(_)) {
            let value = self.word(expr, predicate)?;
            return if let Some(target) = target {
                self.emit(
                    Opcode::Move,
                    target,
                    value,
                    Word::literal(0),
                    predicate,
                    expr.span,
                )?;
                Ok(Word::register(target, false))
            } else {
                Ok(value)
            };
        }
        let (opcode, left, right, invert) = match &expr.kind {
            ExprKind::Not(inner) => (
                Opcode::Equal,
                self.word(inner, predicate)?,
                Word::literal(0),
                false,
            ),
            ExprKind::Binary(op, left, right) => {
                let left = self.word(left, predicate)?;
                let right = self.word(right, predicate)?;
                match op {
                    Op::Add => (Opcode::Add, left, right, false),
                    Op::Subtract => (Opcode::Subtract, left, right, false),
                    Op::Min => (Opcode::Min, left, right, false),
                    Op::Max => (Opcode::Max, left, right, false),
                    Op::Equal => (Opcode::Equal, left, right, false),
                    Op::NotEqual => (Opcode::Equal, left, right, true),
                    Op::Less => (Opcode::LessThan, left, right, false),
                    Op::LessEqual => (Opcode::LessThan, right, left, true),
                    Op::Greater => (Opcode::LessThan, right, left, false),
                    Op::GreaterEqual => (Opcode::LessThan, left, right, true),
                }
            }
            _ => return Err(expr.span.error("unsupported runtime expression")),
        };
        let destination = if let Some(target) = target {
            target
        } else if let Some(register) = left.temporary.or(right.temporary) {
            register
        } else {
            self.allocate(expr.span)?
        };
        self.emit(opcode, destination, left, right, predicate, expr.span)?;
        for register in [left.temporary, right.temporary].into_iter().flatten() {
            if register != destination {
                self.release(register);
            }
        }
        if invert {
            self.emit(
                Opcode::Equal,
                destination,
                Word::register(destination, false),
                Word::literal(0),
                predicate,
                expr.span,
            )?;
        }
        Ok(Word::register(destination, target.is_none()))
    }

    fn direct_predicate(&self, expr: &Expr) -> Option<Predicate> {
        if let ExprKind::Not(inner) = &expr.kind {
            return self.direct_predicate(inner).map(Predicate::not);
        }
        if let ExprKind::Name(name) = &expr.kind {
            let predicate = match name.as_str() {
                "before_deadline" => Predicate::BEFORE_DEADLINE,
                "after_deadline" => Predicate::BEFORE_DEADLINE.not(),
                "terminal" => Predicate {
                    source: PredicateSource::Terminal,
                    inverted: false,
                },
                "has_payout" => Predicate {
                    source: PredicateSource::HasPayout,
                    inverted: false,
                },
                _ => return None,
            };
            return Some(predicate);
        }
        None
    }

    fn require(&mut self, expr: &Expr, predicate: Predicate) -> Result<(), Error> {
        if self.env.constant(expr) {
            let value = self.word(expr, predicate)?;
            return self.emit(
                Opcode::AssertEqual,
                Register::State0,
                value,
                Word::literal(1),
                predicate,
                expr.span,
            );
        }
        if let ExprKind::Binary(op, left, right) = &expr.kind {
            if matches!(op, Op::Equal | Op::LessEqual | Op::GreaterEqual) {
                let left = self.word(left, predicate)?;
                let right = self.word(right, predicate)?;
                let (opcode, left, right) = match op {
                    Op::Equal => (Opcode::AssertEqual, left, right),
                    Op::LessEqual => (Opcode::AssertLessOrEqual, left, right),
                    Op::GreaterEqual => (Opcode::AssertLessOrEqual, right, left),
                    _ => unreachable!(),
                };
                self.emit(opcode, Register::State0, left, right, predicate, expr.span)?;
                for register in [left.temporary, right.temporary].into_iter().flatten() {
                    self.release(register);
                }
                return Ok(());
            }
        }
        let value = self.word(expr, predicate)?;
        self.emit(
            Opcode::AssertEqual,
            Register::State0,
            value,
            Word::literal(1),
            predicate,
            expr.span,
        )?;
        if let Some(register) = value.temporary {
            self.release(register);
        }
        Ok(())
    }

    fn block(&mut self, statements: &[Statement], predicate: Predicate) -> Result<(), Error> {
        for statement in statements {
            match statement {
                Statement::Assign(name, value, span) => {
                    let target =
                        *self.env.states.get(name).ok_or_else(|| {
                            span.error(format!("'{name}' is not a state variable"))
                        })?;
                    if self.env.ty(value, false)? != Type::U64 {
                        return Err(value.span.error("state assignment requires u64"));
                    }
                    self.expression(value, Some(target), predicate)?;
                }
                Statement::Require(value) => {
                    if self.env.ty(value, false)? != Type::Bool {
                        return Err(value.span.error("require needs a bool expression"));
                    }
                    self.require(value, predicate)?;
                }
                Statement::When(condition, body) => {
                    if self.env.ty(condition, false)? != Type::Bool {
                        return Err(condition.span.error("when needs a bool expression"));
                    }
                    if predicate == Predicate::ALWAYS {
                        if let Some(direct) = self.direct_predicate(condition) {
                            self.block(body, direct)?;
                            continue;
                        }
                    }
                    // Reset before the guarded calculation: an inactive parent must
                    // leave a Boolean false, even if this register held a u64 earlier.
                    let register = self.allocate(condition.span)?;
                    self.emit(
                        Opcode::Move,
                        register,
                        Word::literal(0),
                        Word::literal(0),
                        Predicate::ALWAYS,
                        condition.span,
                    )?;
                    self.expression(condition, Some(register), predicate)?;
                    let guard = Predicate {
                        source: if register == Register::Scratch0 {
                            PredicateSource::Scratch0
                        } else {
                            PredicateSource::Scratch1
                        },
                        inverted: false,
                    };
                    self.block(body, guard)?;
                    self.release(register);
                }
            }
        }
        Ok(())
    }
}

fn parameter(value: &Json, ty: Type, span: Span) -> Result<Value, Error> {
    match ty {
        Type::U64 => {
            let text = value.as_str().ok_or_else(|| {
                span.error("u64 parameters require canonical decimal JSON strings")
            })?;
            if text.is_empty()
                || text.len() > 20
                || (text.len() > 1 && text.starts_with('0'))
                || !text.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(span.error("expected a canonical decimal u64 string"));
            }
            Ok(Value::U64(
                text.parse()
                    .map_err(|_| span.error("parameter exceeds u64"))?,
            ))
        }
        Type::Bool => value
            .as_bool()
            .map(Value::Bool)
            .ok_or_else(|| span.error("expected a JSON bool")),
        Type::Address => {
            let text = value
                .as_str()
                .ok_or_else(|| span.error("expected an o1 address string"))?;
            let address = Address::parse(text).map_err(|error| span.error(error.to_string()))?;
            Ok(Value::Address(address.to_bech32()))
        }
    }
}

fn policy(source: &Source, env: &Environment, state: [u64; 2]) -> Result<ObjectOpening, Error> {
    let types: BTreeMap<_, _> = [
        ("claim_authority", Type::Address),
        ("recovery_authority", Type::Address),
        ("claim_recipient", Type::Address),
        ("recovery_recipient", Type::Address),
        ("deadline_height", Type::U64),
        ("max_fee_micronoid", Type::U64),
        ("max_payout_micronoid", Type::U64),
        ("min_retained_micronoid", Type::U64),
        ("claim_can_continue", Type::Bool),
        ("claim_can_close", Type::Bool),
        ("recovery_can_continue", Type::Bool),
        ("recovery_can_close", Type::Bool),
        ("unrestricted_payout_recipient", Type::Bool),
    ]
    .into_iter()
    .collect();
    let mut values = BTreeMap::new();
    for (name, expr, span) in &source.policy {
        let expected = types
            .get(name.as_str())
            .ok_or_else(|| span.error(format!("unknown policy field '{name}'")))?;
        if env.ty(expr, false)? != *expected {
            return Err(expr
                .span
                .error(format!("policy field '{name}' requires {expected:?}")));
        }
        let value = env.value(expr)?;
        if let Value::Address(address) = &value {
            Address::parse(address).map_err(|error| expr.span.error(error.to_string()))?;
        }
        if values.insert(name.as_str(), value).is_some() {
            return Err(span.error(format!("duplicate policy field '{name}'")));
        }
    }
    for name in types.keys() {
        if !values.contains_key(name) {
            return Err(Error::plain(format!(
                "missing policy field '{name}'; permissions must be explicit"
            )));
        }
    }
    let address = |name| match &values[name] {
        Value::Address(text) => {
            Address::parse(text).map_err(|error| Error::plain(error.to_string()))
        }
        _ => unreachable!(),
    };
    let number = |name| match values[name] {
        Value::U64(value) => value,
        _ => unreachable!(),
    };
    let flag = |name| match values[name] {
        Value::Bool(value) => value,
        _ => unreachable!(),
    };
    let modes = (u8::from(flag("claim_can_continue")) * policy::CLAIM_CONTINUE)
        | (u8::from(flag("claim_can_close")) * policy::CLAIM_CLOSE)
        | (u8::from(flag("recovery_can_continue")) * policy::RECOVERY_CONTINUE)
        | (u8::from(flag("recovery_can_close")) * policy::RECOVERY_CLOSE)
        | (u8::from(flag("unrestricted_payout_recipient")) * policy::ANY_PAYOUT_RECIPIENT);
    Ok(ObjectOpening {
        program: native::EMPTY_PROGRAM,
        state: native::pack_state(state),
        claim_authority: address("claim_authority")?,
        recovery_authority: address("recovery_authority")?,
        claim_recipient: address("claim_recipient")?,
        recovery_recipient: address("recovery_recipient")?,
        deadline: number("deadline_height"),
        rules: ObjectRules {
            max_fee: number("max_fee_micronoid"),
            max_payout: number("max_payout_micronoid"),
            min_retained: number("min_retained_micronoid"),
            modes,
        },
    })
}

pub fn compile(source: &str, parameters: &Json) -> Result<CompiledContract, Error> {
    if OBJECT_VERSION != 3 || native::PROGRAM_STEPS != 16 {
        return Err(Error::plain(
            "this compiler targets contract ABI 3 with 16 instructions",
        ));
    }
    let source = syntax::parse(source)?;
    let parameters = parameters
        .as_object()
        .ok_or_else(|| Error::plain("parameters must be a JSON object"))?;
    let mut env = Environment::default();
    let mut names = BTreeSet::new();
    for declaration in &source.params {
        env.declare(&declaration.name, declaration.span)?;
        let value = parameters.get(&declaration.name).ok_or_else(|| {
            declaration
                .span
                .error(format!("missing parameter '{}'", declaration.name))
        })?;
        env.values.insert(
            declaration.name.clone(),
            parameter(value, declaration.ty, declaration.span)?,
        );
        names.insert(declaration.name.as_str());
    }
    for name in parameters.keys() {
        if !names.contains(name.as_str()) {
            return Err(Error::plain(format!("unknown parameter '{name}'")));
        }
    }
    for declaration in &source.constants {
        env.declare(&declaration.name, declaration.span)?;
        let expr = declaration.value.as_ref().expect("constant initializer");
        if env.ty(expr, false)? != declaration.ty {
            return Err(expr.span.error("constant initializer has the wrong type"));
        }
        env.values
            .insert(declaration.name.clone(), env.value(expr)?);
    }
    if source.states.len() > 2 {
        return Err(source.states[2]
            .span
            .error("ABI 3 has two persistent state registers"));
    }
    let mut state = [0; 2];
    for (index, declaration) in source.states.iter().enumerate() {
        env.declare(&declaration.name, declaration.span)?;
        if declaration.ty != Type::U64 {
            return Err(declaration.span.error("persistent state must be u64"));
        }
        let expr = declaration.value.as_ref().expect("state initializer");
        let Value::U64(value) = env.value(expr)? else {
            return Err(expr.span.error("state initializer requires u64"));
        };
        state[index] = value;
        env.states.insert(
            declaration.name.clone(),
            if index == 0 {
                Register::State0
            } else {
                Register::State1
            },
        );
    }
    let mut opening = policy(&source, &env, state)?;
    for condition in &source.ensures {
        if env.ty(condition, true)? != Type::Bool {
            return Err(condition.span.error("ensures requires bool"));
        }
    }
    let mut lowering = Lowering {
        env: &env,
        program: Vec::new(),
        scratch: [false; 2],
    };
    lowering.block(&source.body, Predicate::ALWAYS)?;
    let instructions = lowering.program.len();
    for (destination, instruction) in opening.program.iter_mut().zip(lowering.program) {
        *destination = instruction.to_fields();
    }
    opening
        .validate()
        .map_err(|error| Error::plain(error.to_string()))?;
    Ok(CompiledContract {
        name: source.name.clone(),
        state_registers: source.states.len(),
        instructions,
        opening,
        source,
        env,
    })
}
