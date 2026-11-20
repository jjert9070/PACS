import Std

/- Consistency model for accepted normalized fields; the normalizer is a parameter, not a proof of Python Unicode trimming. -/
namespace PacsRules

namespace C
structure Fields where
  modality : String
  bodyRegion : String
  deriving DecidableEq

structure StoredPair where
  metadata : Fields
  series : Fields

def normalize (n : String → String) (raw : Fields) : Fields :=
  { modality := n raw.modality, bodyRegion := n raw.bodyRegion }

def canonicalPair (n : String → String) (raw : Fields) : StoredPair :=
  let canonical := normalize n raw
  { metadata := canonical, series := canonical }

theorem metadata_series_agree (n : String → String) (raw : Fields) :
    (canonicalPair n raw).metadata = (canonicalPair n raw).series := by
  rfl
end C

#print axioms C.metadata_series_agree
end PacsRules
