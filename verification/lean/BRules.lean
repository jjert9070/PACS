import Std

/- Exact integer model only; not an IEEE floating-point implementation proof. -/
namespace PacsRules

namespace B
def acceptWidth (width : Int) : Option Int :=
  if 1 ≤ width then some width else none

theorem accepted_width_positive (w v : Int) (h : acceptWidth w = some v) : 1 ≤ v := by
  unfold acceptWidth at h
  split at h
  next hw => cases h; exact hw
  next => contradiction

theorem zero_width_rejected : acceptWidth 0 = none := by decide

theorem wider_window_denominator_positive (width : Int) (h : 1 < width) :
    0 < width - 1 := by omega

-- Doubled integer coordinates avoid representing a half as a floating-point number.
def unitWindow (value center : Int) : Int :=
  if 2 * value ≤ 2 * center - 1 then 0 else 255

theorem unit_window_binary (value center : Int) :
    unitWindow value center = 0 ∨ unitWindow value center = 255 := by
  unfold unitWindow
  split <;> simp

theorem unit_window_range (value center : Int) :
    0 ≤ unitWindow value center ∧ unitWindow value center ≤ 255 := by
  rcases unit_window_binary value center with h | h <;> simp [h]

theorem center_is_white (center : Int) : unitWindow center center = 255 := by
  unfold unitWindow
  split <;> omega

theorem shifted_threshold_equivalent (value center : Int) :
    2 * value ≤ 2 * center - 1 ↔ 2 * (value - center) ≤ -1 := by omega

theorem centered_expression_equivalent (value center : Int) :
    2 * value - (2 * center - 1) = 2 * (value - center) + 1 := by omega
end B

#print axioms B.accepted_width_positive
#print axioms B.zero_width_rejected
#print axioms B.wider_window_denominator_positive
#print axioms B.unit_window_binary
#print axioms B.unit_window_range
#print axioms B.center_is_white
#print axioms B.shifted_threshold_equivalent
#print axioms B.centered_expression_equivalent
end PacsRules
