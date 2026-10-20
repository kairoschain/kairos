// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Paranoid Zero.
// Modified by the Kairos Chain contributors (rebrand to Kairos Chain).

//! Immutable, source-independent synchronization plans.

use noid_chain::block_header::{block_id, semantic_header_id};
use thiserror::Error;

use super::{
    header_dag::ValidatedHeader,
    types::{
        BlockBodyClaimId, ChainPoint, Hash32, ObjectClaimId, PlanId, SnapshotId, StateSegmentId,
        TerminalClaimId,
    },
};

const PLAN_ID_DOMAIN: &[u8] = b"KAIROS/NETWORK/SYNC-PLAN/V3";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SyncPlanKind {
    LiveSuffix,
    Reorg,
    Snapshot,
}

impl SyncPlanKind {
    const fn tag(self) -> u8 {
        match self {
            Self::LiveSuffix => 0,
            Self::Reorg => 1,
            Self::Snapshot => 2,
        }
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SyncPlanError {
    #[error("a live or reorg suffix cannot be empty")]
    EmptySuffix,
    #[error("the first header does not extend the plan base")]
    FirstHeaderDoesNotExtendBase,
    #[error("the suffix contains a broken height or parent link")]
    BrokenSuffix,
    #[error("a validated header's cached hash does not match its canonical header")]
    HeaderHashMismatch,
    #[error("a reorg ancestor cannot be above the old canonical tip")]
    InvalidReorgBase,
    #[error("a snapshot boundary and its terminal claim identify different heights")]
    SnapshotTerminalHeightMismatch,
    #[error("a snapshot segment belongs to another immutable generation")]
    SnapshotSegmentMismatch,
    #[error("snapshot segment identifiers are not strictly increasing")]
    SnapshotSegmentsNotCanonical,
}

/// A frozen graph of consensus objects required to reach one exact target.
///
/// No transport identity is represented here. A peer disconnect can alter an
/// object's source lease, but cannot mutate or replace this plan.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SyncPlan {
    id: PlanId,
    kind: SyncPlanKind,
    base: ChainPoint,
    old_tip: Option<ChainPoint>,
    target: ChainPoint,
    target_work: Option<Hash32>,
    snapshot: Option<SnapshotId>,
    headers: Vec<ValidatedHeader>,
    required_objects: Vec<ObjectClaimId>,
}

impl SyncPlan {
    pub fn live_suffix(
        base: ChainPoint,
        headers: Vec<ValidatedHeader>,
        proof_class: u8,
    ) -> Result<Self, SyncPlanError> {
        Self::suffix(SyncPlanKind::LiveSuffix, None, base, headers, proof_class)
    }

    pub fn reorg(
        old_tip: ChainPoint,
        ancestor: ChainPoint,
        headers: Vec<ValidatedHeader>,
        proof_class: u8,
    ) -> Result<Self, SyncPlanError> {
        if ancestor.height > old_tip.height {
            return Err(SyncPlanError::InvalidReorgBase);
        }
        Self::suffix(
            SyncPlanKind::Reorg,
            Some(old_tip),
            ancestor,
            headers,
            proof_class,
        )
    }

    /// Freeze an immutable State generation at its exact boundary.
    ///
    /// Catch-up beyond this boundary is deliberately a separate suffix plan;
    /// a moving live tip can therefore never invalidate downloaded segments.
    pub fn snapshot(
        snapshot: SnapshotId,
        boundary_terminal: TerminalClaimId,
        segments: Vec<StateSegmentId>,
    ) -> Result<Self, SyncPlanError> {
        if snapshot.boundary.height != boundary_terminal.height {
            return Err(SyncPlanError::SnapshotTerminalHeightMismatch);
        }
        if segments.iter().any(|segment| segment.snapshot != snapshot) {
            return Err(SyncPlanError::SnapshotSegmentMismatch);
        }
        if !segments
            .windows(2)
            .all(|pair| pair[0].segment_id < pair[1].segment_id)
        {
            return Err(SyncPlanError::SnapshotSegmentsNotCanonical);
        }
        let mut required_objects = vec![
            ObjectClaimId::SnapshotManifest(snapshot),
            ObjectClaimId::Terminal(boundary_terminal),
        ];
        required_objects.extend(segments.into_iter().map(ObjectClaimId::StateSegment));
        let id = derive_plan_id(
            SyncPlanKind::Snapshot,
            snapshot.boundary,
            None,
            snapshot.boundary,
            None,
            Some(snapshot),
            &[],
            &required_objects,
        );
        Ok(Self {
            id,
            kind: SyncPlanKind::Snapshot,
            base: snapshot.boundary,
            old_tip: None,
            target: snapshot.boundary,
            target_work: None,
            snapshot: Some(snapshot),
            headers: Vec::new(),
            required_objects,
        })
    }

    fn suffix(
        kind: SyncPlanKind,
        old_tip: Option<ChainPoint>,
        base: ChainPoint,
        headers: Vec<ValidatedHeader>,
        proof_class: u8,
    ) -> Result<Self, SyncPlanError> {
        let first = headers.first().ok_or(SyncPlanError::EmptySuffix)?;
        if first.hash != block_id(&first.header) {
            return Err(SyncPlanError::HeaderHashMismatch);
        }
        if first.header.height != base.height.saturating_add(1)
            || first.header.prev_block_hash != base.hash
        {
            return Err(SyncPlanError::FirstHeaderDoesNotExtendBase);
        }
        for pair in headers.windows(2) {
            let previous = &pair[0];
            let next = &pair[1];
            if next.hash != block_id(&next.header) {
                return Err(SyncPlanError::HeaderHashMismatch);
            }
            if next.header.height != previous.header.height.saturating_add(1)
                || next.header.prev_block_hash != previous.hash
            {
                return Err(SyncPlanError::BrokenSuffix);
            }
        }

        let last = headers.last().expect("non-empty suffix checked above");
        let target = last.point();
        let target_work = Some(last.cumulative_work);
        let terminal = TerminalClaimId {
            height: target.height,
            semantic_header_id: semantic_header_id(&last.header),
            proof_class,
        };
        let mut required_objects = headers
            .iter()
            .map(|header| {
                ObjectClaimId::BlockBody(BlockBodyClaimId {
                    height: header.header.height,
                    block_hash: header.hash,
                })
            })
            .collect::<Vec<_>>();
        required_objects.push(ObjectClaimId::Terminal(terminal));

        let id = derive_plan_id(
            kind,
            base,
            old_tip,
            target,
            target_work,
            None,
            &headers,
            &required_objects,
        );
        Ok(Self {
            id,
            kind,
            base,
            old_tip,
            target,
            target_work,
            snapshot: None,
            headers,
            required_objects,
        })
    }

    pub const fn id(&self) -> PlanId {
        self.id
    }

    pub const fn kind(&self) -> SyncPlanKind {
        self.kind
    }

    pub const fn base(&self) -> ChainPoint {
        self.base
    }

    pub const fn old_tip(&self) -> Option<ChainPoint> {
        self.old_tip
    }

    pub const fn target(&self) -> ChainPoint {
        self.target
    }

    pub const fn target_work(&self) -> Option<Hash32> {
        self.target_work
    }

    pub const fn snapshot_id(&self) -> Option<SnapshotId> {
        self.snapshot
    }

    pub fn headers(&self) -> &[ValidatedHeader] {
        &self.headers
    }

    pub fn required_objects(&self) -> &[ObjectClaimId] {
        &self.required_objects
    }

    /// Return whether `point` is on this plan's exact selected ancestry.
    ///
    /// This deliberately inspects only the source-independent plan. It lets
    /// the runtime distinguish a moving tip on the same branch from a real
    /// branch replacement without assigning authority to the peer that
    /// announced either view.
    pub fn contains_point(&self, point: ChainPoint) -> bool {
        self.base == point || self.headers.iter().any(|header| header.point() == point)
    }
}

#[allow(clippy::too_many_arguments)]
fn derive_plan_id(
    kind: SyncPlanKind,
    base: ChainPoint,
    old_tip: Option<ChainPoint>,
    target: ChainPoint,
    target_work: Option<Hash32>,
    snapshot: Option<SnapshotId>,
    headers: &[ValidatedHeader],
    required_objects: &[ObjectClaimId],
) -> PlanId {
    let mut hasher = blake3::Hasher::new();
    hasher.update(PLAN_ID_DOMAIN);
    hasher.update(&[kind.tag()]);
    hash_point(&mut hasher, base);
    match old_tip {
        Some(point) => {
            hasher.update(&[1]);
            hash_point(&mut hasher, point);
        }
        None => {
            hasher.update(&[0]);
        }
    }
    hash_point(&mut hasher, target);
    match target_work {
        Some(work) => {
            hasher.update(&[1]);
            hasher.update(&work);
        }
        None => {
            hasher.update(&[0]);
        }
    }
    match snapshot {
        Some(snapshot) => {
            hasher.update(&[1]);
            hash_snapshot(&mut hasher, snapshot);
        }
        None => {
            hasher.update(&[0]);
        }
    }
    hasher.update(&(headers.len() as u64).to_le_bytes());
    for header in headers {
        hasher.update(&header.header.to_bytes());
        hasher.update(&header.cumulative_work);
    }
    hasher.update(&(required_objects.len() as u64).to_le_bytes());
    for claim in required_objects {
        hash_claim(&mut hasher, *claim);
    }
    PlanId(*hasher.finalize().as_bytes())
}

fn hash_point(hasher: &mut blake3::Hasher, point: ChainPoint) {
    hasher.update(&point.height.to_le_bytes());
    hasher.update(&point.hash);
}

fn hash_snapshot(hasher: &mut blake3::Hasher, snapshot: SnapshotId) {
    hash_point(hasher, snapshot.boundary);
    hasher.update(&snapshot.state_root);
    hasher.update(&snapshot.manifest_digest);
    hasher.update(&snapshot.format_version.to_le_bytes());
}

fn hash_claim(hasher: &mut blake3::Hasher, claim: ObjectClaimId) {
    match claim {
        ObjectClaimId::BlockBody(body) => {
            hasher.update(&[0]);
            hasher.update(&body.height.to_le_bytes());
            hasher.update(&body.block_hash);
        }
        ObjectClaimId::Terminal(terminal) => {
            hasher.update(&[1]);
            hasher.update(&terminal.height.to_le_bytes());
            hasher.update(&terminal.semantic_header_id);
            hasher.update(&[terminal.proof_class]);
        }
        ObjectClaimId::SnapshotManifest(snapshot) => {
            hasher.update(&[2]);
            hash_snapshot(hasher, snapshot);
        }
        ObjectClaimId::StateSegment(segment) => {
            hasher.update(&[3]);
            hash_snapshot(hasher, segment.snapshot);
            hasher.update(&segment.segment_id.to_le_bytes());
            hasher.update(&segment.segment_root);
            hasher.update(&segment.encoded_len.to_le_bytes());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use noid_chain::{block_header::BlockHeader, consensus::genesis_header};

    fn child(parent: BlockHeader, nonce: u128, work: u8) -> ValidatedHeader {
        let mut header = parent;
        header.prev_block_hash = block_id(&parent);
        header.height += 1;
        header.timestamp += 1;
        header.nonce = nonce;
        ValidatedHeader::new_after_consensus_checks(header, [work; 32])
    }

    #[test]
    fn live_plan_is_exact_and_has_one_tip_terminal() {
        let genesis = genesis_header();
        let base = ChainPoint::new(0, block_id(&genesis));
        let first = child(genesis, 1, 2);
        let second = child(first.header, 2, 3);
        let plan = SyncPlan::live_suffix(base, vec![first, second], 0).unwrap();

        assert_eq!(plan.kind(), SyncPlanKind::LiveSuffix);
        assert_eq!(plan.target(), second.point());
        assert_eq!(plan.required_objects().len(), 3);
        assert!(matches!(
            plan.required_objects().last(),
            Some(ObjectClaimId::Terminal(terminal)) if terminal.height == second.header.height
        ));
    }

    #[test]
    fn identical_branch_always_has_identical_plan_id() {
        let genesis = genesis_header();
        let base = ChainPoint::new(0, block_id(&genesis));
        let first = child(genesis, 7, 2);
        let left = SyncPlan::live_suffix(base, vec![first], 0).unwrap();
        let right = SyncPlan::live_suffix(base, vec![first], 0).unwrap();
        assert_eq!(left.id(), right.id());
    }

    #[test]
    fn broken_suffix_is_rejected_before_fetching() {
        let genesis = genesis_header();
        let base = ChainPoint::new(0, block_id(&genesis));
        let first = child(genesis, 1, 2);
        let mut second = child(first.header, 2, 3);
        second.header.prev_block_hash = [0xEE; 32];
        second.hash = block_id(&second.header);
        assert_eq!(
            SyncPlan::live_suffix(base, vec![first, second], 0),
            Err(SyncPlanError::BrokenSuffix)
        );
    }

    #[test]
    fn snapshot_plan_stops_at_immutable_boundary() {
        let boundary = ChainPoint::new(1_000, [1; 32]);
        let snapshot = SnapshotId {
            boundary,
            state_root: [2; 32],
            manifest_digest: [3; 32],
            format_version: 2,
        };
        let terminal = TerminalClaimId {
            height: boundary.height,
            semantic_header_id: [4; 32],
            proof_class: 0,
        };
        let segments = vec![StateSegmentId {
            snapshot,
            segment_id: 7,
            segment_root: [5; 32],
            encoded_len: 64,
        }];
        let plan = SyncPlan::snapshot(snapshot, terminal, segments).unwrap();
        assert_eq!(plan.base(), boundary);
        assert_eq!(plan.target(), boundary);
        assert!(plan.headers().is_empty());
        assert_eq!(plan.required_objects().len(), 3);
    }

    #[test]
    fn longer_same_branch_plan_contains_the_pinned_target() {
        let genesis = genesis_header();
        let base = ChainPoint::new(0, block_id(&genesis));
        let first = child(genesis, 1, 2);
        let second = child(first.header, 2, 3);
        let pinned = SyncPlan::live_suffix(base, vec![first], 0).unwrap();
        let moving_tip = SyncPlan::live_suffix(base, vec![first, second], 0).unwrap();

        assert!(moving_tip.contains_point(pinned.target()));
        assert!(!pinned.contains_point(moving_tip.target()));
    }
}
