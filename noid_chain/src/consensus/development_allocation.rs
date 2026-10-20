// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors: the Parano1d development
// allocation is removed. 100% of every block subsidy goes to the miner.

//! Development allocation — DISABLED on Kairos Chain.
//!
//! Parano1d paid 5% + 5% of the subsidy to two development funds for three
//! years. Kairos Chain has no development allocation: every query below
//! reports an inactive allocation, no payout is ever due and the miner may
//! claim the full subsidy. The API is kept so existing callers (template,
//! validation, recursive action surface) compile unchanged; their payout
//! branches are unreachable.

use noid_poseidon2b::primitives::Address;

use super::emission::{block_reward, block_reward_with_schedule};
use super::forks::{ForkSchedule, ACTIVE_SCHEDULE};
use super::params::BLOCK_TIME;

/// Target blocks in one wall-clock day at the legacy interval (unused).
pub const TARGET_BLOCKS_PER_DAY: u64 = 24 * 60 * 60 / BLOCK_TIME;

/// The allocation period is empty.
pub const DEVELOPMENT_ALLOCATION_END_HEIGHT: u64 = 0;

/// The allocation period is empty.
pub const DEVELOPMENT_ALLOCATION_DURATION_SECONDS: u64 = 0;

/// No payouts are ever made.
pub const DEVELOPMENT_ALLOCATION_PAYOUTS: u64 = 0;

/// Retained for API compatibility only.
pub const DEVELOPMENT_SHARE_DENOMINATOR: u64 = 20;

/// Placeholder recipients for the unreachable payout branches (burn address).
pub const DISABLED_FUND_ADDRESS_A: Address = Address([0u8; 32]);
/// Placeholder recipients for the unreachable payout branches (burn address).
pub const DISABLED_FUND_ADDRESS_B: Address = Address([0u8; 32]);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DevelopmentAllocation {
    /// Always false on Kairos Chain.
    pub active: bool,
    /// Always false on Kairos Chain.
    pub payout_due: bool,
    /// Always zero on Kairos Chain.
    pub share_each: u64,
    /// Always `None` on Kairos Chain.
    pub payout_each: Option<u64>,
    /// The full block subsidy.
    pub miner_subsidy: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DevelopmentAllocationError {
    InexactRewardShare,
    PayoutOverflow,
    InexactInterval,
}

impl core::fmt::Display for DevelopmentAllocationError {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(formatter, "{self:?}")
    }
}

impl std::error::Error for DevelopmentAllocationError {}

fn inactive(subsidy: u64) -> DevelopmentAllocation {
    DevelopmentAllocation {
        active: false,
        payout_due: false,
        share_each: 0,
        payout_each: None,
        miner_subsidy: subsidy,
    }
}

pub const fn development_allocation_end_height_with_schedule(_schedule: ForkSchedule) -> u64 {
    DEVELOPMENT_ALLOCATION_END_HEIGHT
}

pub fn development_allocation_with_schedule(
    height: u64,
    log_slots: u32,
    schedule: ForkSchedule,
) -> Result<DevelopmentAllocation, DevelopmentAllocationError> {
    Ok(inactive(block_reward_with_schedule(height, log_slots, schedule)))
}

pub fn development_allocation_at_height(
    height: u64,
    log_slots: u32,
) -> Result<DevelopmentAllocation, DevelopmentAllocationError> {
    development_allocation_with_schedule(height, log_slots, ACTIVE_SCHEDULE)
}

pub fn miner_subsidy_at_height(height: u64, log_slots: u32) -> u64 {
    block_reward_with_schedule(height, log_slots, ACTIVE_SCHEDULE)
}

pub fn development_payout_due_at_height(_height: u64) -> bool {
    false
}

#[inline]
pub const fn development_allocation_active(_height: u64) -> bool {
    false
}

#[inline]
pub const fn development_payout_due(_height: u64) -> bool {
    false
}

/// Retained for API compatibility only.
pub fn development_share_each(subsidy: u64) -> Result<u64, DevelopmentAllocationError> {
    if !subsidy.is_multiple_of(DEVELOPMENT_SHARE_DENOMINATOR) {
        return Err(DevelopmentAllocationError::InexactRewardShare);
    }
    Ok(subsidy / DEVELOPMENT_SHARE_DENOMINATOR)
}

/// Legacy-rule subsidy; the miner receives all of it.
#[inline]
pub fn miner_subsidy(_height: u64, log_slots: u32) -> u64 {
    block_reward(log_slots)
}

/// Legacy stateless allocation; always inactive.
pub fn development_allocation(
    _child_height: u64,
    child_log_slots: u32,
) -> Result<DevelopmentAllocation, DevelopmentAllocationError> {
    Ok(inactive(block_reward(child_log_slots)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::consensus::params::LOG_SLOTS_GENESIS;

    #[test]
    fn miners_receive_the_full_subsidy_at_every_height() {
        for height in [1, 2, 2_880, 1_051_200, 1_051_201, 10_000_000] {
            let allocation = development_allocation_at_height(height, LOG_SLOTS_GENESIS).unwrap();
            assert!(!allocation.active && !allocation.payout_due);
            assert_eq!(allocation.payout_each, None);
            assert_eq!(
                allocation.miner_subsidy,
                block_reward_with_schedule(height, LOG_SLOTS_GENESIS, ACTIVE_SCHEDULE)
            );
            assert!(!development_payout_due_at_height(height));
        }
    }
}
