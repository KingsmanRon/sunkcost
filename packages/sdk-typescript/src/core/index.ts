// Sunkcost core (C5) for TypeScript: the same features, score, canonical form and hashes as the
// Python core, pinned to it by the shared golden vectors in packages/spec/vectors.

export { ARGS_HASH_PREFIX, argsHash } from "./argsHash.js";
export { CanonicalJSONError, canonicalJson, canonicalJsonText } from "./canonical.js";
export {
  FEATURE_INDEX,
  FEATURE_NAMES,
  FEATURE_SPEC,
  FeatureState,
  featuresV1,
  stepFromWire,
  type StepRecord,
} from "./features.js";
export { BUCKETS, holdoutBucket, holdoutThreshold, isHoldout } from "./holdout.js";
export { LogisticModel, ModelError, parseF64, scoreLogisticV1, sigmoid } from "./scoring.js";
export { formatTimestampUs, parseTimestampUs, utcDate } from "./time.js";
