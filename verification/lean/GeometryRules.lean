import ABCRules

/- Integer pixel-index geometry, not mouse coordinates or floating-point zoom.
   Pillow observations separately check rotation direction and crop offsets. -/
namespace PacsRules.Geometry

abbrev Point := Int × Int

def inBounds (width height : Int) (point : Point) : Prop :=
  0 ≤ point.1 ∧ point.1 < width ∧ 0 ≤ point.2 ∧ point.2 < height

def horizontal (width : Int) (point : Point) : Point := (width - 1 - point.1, point.2)
def vertical (height : Int) (point : Point) : Point := (point.1, height - 1 - point.2)
def clockwise (height : Int) (point : Point) : Point := (height - 1 - point.2, point.1)

theorem horizontal_twice (width : Int) (point : Point) :
    horizontal width (horizontal width point) = point := by
  apply Prod.ext
  · simp [horizontal]; omega
  · rfl

theorem vertical_twice (height : Int) (point : Point) :
    vertical height (vertical height point) = point := by
  apply Prod.ext
  · rfl
  · simp [vertical]; omega

theorem four_clockwise_turns (width height : Int) (point : Point) :
    clockwise width (clockwise height (clockwise width (clockwise height point))) = point := by
  apply Prod.ext <;> simp [clockwise] <;> omega

theorem clockwise_stays_in_swapped_bounds (width height : Int) (point : Point)
    (inside : inBounds width height point) : inBounds height width (clockwise height point) := by
  dsimp [inBounds, clockwise] at *
  omega

theorem horizontal_stays_in_bounds (width height : Int) (point : Point)
    (inside : inBounds width height point) : inBounds width height (horizontal width point) := by
  dsimp [inBounds, horizontal] at *
  omega

theorem vertical_stays_in_bounds (width height : Int) (point : Point)
    (inside : inBounds width height point) : inBounds width height (vertical height point) := by
  dsimp [inBounds, vertical] at *
  omega

theorem accepted_roi_has_positive_spans (width height : Nat) (roi : ABC.Bounds)
    (valid : ABC.validBounds width height roi) :
    0 < roi.right - roi.left ∧ 0 < roi.bottom - roi.top := by
  dsimp [ABC.validBounds] at valid
  omega

theorem accepted_roi_has_positive_area (width height : Nat) (roi : ABC.Bounds)
    (valid : ABC.validBounds width height roi) :
    0 < (roi.right - roi.left) * (roi.bottom - roi.top) := by
  have spans := accepted_roi_has_positive_spans width height roi valid
  exact Int.mul_pos spans.1 spans.2

def pixels (width height : Nat) : List (Nat × Nat) :=
  (List.range height).flatMap (fun y => (List.range width).map (fun x => (x, y)))

theorem pixel_count_matches_dimensions (width height : Nat) :
    (pixels width height).length = width * height := by
  induction height with
  | zero => simp [pixels]
  | succ height ih =>
    simp only [pixels] at ih
    simp [pixels, List.range_succ, List.flatMap_append, ih, Nat.mul_succ]

#print axioms horizontal_twice
#print axioms vertical_twice
#print axioms four_clockwise_turns
#print axioms clockwise_stays_in_swapped_bounds
#print axioms horizontal_stays_in_bounds
#print axioms vertical_stays_in_bounds
#print axioms accepted_roi_has_positive_spans
#print axioms accepted_roi_has_positive_area
#print axioms pixel_count_matches_dimensions

end PacsRules.Geometry
