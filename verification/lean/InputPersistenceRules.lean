import Std

/- Boundary specification only. Counts are natural numbers, and payloads are
   already validated immutable serializations. Parsing, hash collision resistance,
   filesystem writes and network atomicity are outside this model. -/
namespace PacsRules.Boundaries

def sizeAllowed (limit count : Nat) : Bool := decide (0 < count ∧ count ≤ limit)

theorem accepted_size_within_limit (limit count : Nat)
    (accepted : sizeAllowed limit count = true) : 0 < count ∧ count ≤ limit := by
  simpa [sizeAllowed] using accepted

theorem exact_positive_limit_allowed (limit : Nat) (positive : 0 < limit) :
    sizeAllowed limit limit = true := by simp [sizeAllowed, positive]

theorem over_limit_rejected (limit count : Nat) (large : limit < count) :
    sizeAllowed limit count = false := by simp [sizeAllowed]; omega

theorem empty_file_rejected (limit : Nat) : sizeAllowed limit 0 = false := by
  simp [sizeAllowed]

def bitsAllowed (allocated stored : Nat) : Bool :=
  decide ((allocated = 8 ∨ allocated = 16) ∧ 1 ≤ stored ∧ stored ≤ allocated)

theorem accepted_bits_are_supported (allocated stored : Nat)
    (accepted : bitsAllowed allocated stored = true) :
    (allocated = 8 ∨ allocated = 16) ∧ 1 ≤ stored ∧ stored ≤ allocated := by
  simpa [bitsAllowed] using accepted

theorem excess_stored_bits_rejected (allocated stored : Nat) (excess : allocated < stored) :
    bitsAllowed allocated stored = false := by simp [bitsAllowed]; omega

def frameAllowed (frames : Nat) : Bool := decide (frames = 1)

theorem accepted_frame_is_single (frames : Nat) : frameAllowed frames = true ↔ frames = 1 := by
  simp [frameAllowed]

theorem unsupported_frame_rejected (frames : Nat) (unsupported : frames ≠ 1) :
    frameAllowed frames = false := by simp [frameAllowed, unsupported]

def remember (old : Option String) (payload : String) : Bool × Option String :=
  match old with
  | none => (true, some payload)
  | some prior => (decide (prior = payload), some prior)

theorem identical_retry_accepted (payload : String) :
    remember (some payload) payload = (true, some payload) := by simp [remember]

theorem conflicting_retry_does_not_overwrite (prior payload : String) (different : prior ≠ payload) :
    remember (some prior) payload = (false, some prior) := by simp [remember, different]

theorem repeating_request_is_idempotent (old : Option String) (payload : String) :
    (remember (remember old payload).2 payload).2 = (remember old payload).2 := by
  cases old <;> simp [remember]

structure Key where
  owner : String
  project : String
  operation : String
  deriving DecidableEq

def put (store : Key → Option String) (key : Key) (payload : String) : Key → Option String :=
  fun lookup => if lookup = key then (remember (store key) payload).2 else store lookup

theorem other_key_is_unchanged (store : Key → Option String) (key other : Key) (payload : String)
    (different : other ≠ key) : put store key payload other = store other := by
  simp [put, different]

theorem other_owner_is_unchanged (store : Key → Option String) (key other : Key) (payload : String)
    (different : other.owner ≠ key.owner) : put store key payload other = store other := by
  apply other_key_is_unchanged
  intro same
  exact different (congrArg Key.owner same)

#print axioms accepted_size_within_limit
#print axioms exact_positive_limit_allowed
#print axioms over_limit_rejected
#print axioms empty_file_rejected
#print axioms accepted_bits_are_supported
#print axioms excess_stored_bits_rejected
#print axioms accepted_frame_is_single
#print axioms unsupported_frame_rejected
#print axioms identical_retry_accepted
#print axioms conflicting_retry_does_not_overwrite
#print axioms repeating_request_is_idempotent
#print axioms other_key_is_unchanged
#print axioms other_owner_is_unchanged

end PacsRules.Boundaries
