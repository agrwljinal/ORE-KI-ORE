"""Per-zone spatial + spectral fusion for MOIL exploration prioritization.

This module is the clean seam between:

* The spatial intelligence layer  --> WHERE candidate zones sit.
* The spectral intelligence layer --> WHAT the surface looks like at each zone.

The output is a FINAL EXPLORATION PRIORITY SCORE, per zone, that MOIL can use
to prioritize field verification.  It is explicitly NOT an ore-grade estimate,
NOT a manganese-concentration claim, and NOT a certified reserve figure.

Design notes
------------
* The fusion rule is a transparent, deterministic prototype weighted sum. The
  weights live in ``constants.py`` (or are injected) so the demo team can tune
  without touching this module.
* Multiple mineral references are supported via a small registry. Pyrolusite
  is the only reference shipped by default; more can be added by dropping a
  ``MineralReference`` into ``modules.spectral.MINERAL_REFERENCES``.
* Nothing here fabricates satellite data. The zone reflectance is looked up
  via the spectral module's provenance-tagged extractor. If a zone has no
  extractable reflectance the fusion still runs on the spatial score alone
  and clearly marks ``spectral_similarity=None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# Fusion weights (kept here so unit tests do not depend on constants.py)
# ---------------------------------------------------------------------------

DEFAULT_FUSION_WEIGHTS: Dict[str, float] = {
    "spatial": 0.5,
    "spectral": 0.5,
}

# Priority-band thresholds applied to the final exploration score (0-100).
PRIORITY_THRESHOLDS: Dict[str, float] = {
    "HIGH": 75.0,
    "MEDIUM": 50.0,
    # anything below MEDIUM is LOW
}

DATA_PROVENANCE_SYNTHETIC = "SYNTHETIC_DEMO_ZONE_DATA"
DATA_PROVENANCE_REAL = "REAL_SATELLITE"
DATA_PROVENANCE_MIXED = "REAL_SPATIAL_SYNTHETIC_SPECTRAL"


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MineralReference:
    """A single mineral spectral reference the zones are scored against.

    Reflectance values are the reference vector re-sampled to the four
    Sentinel-2 bands the current pipeline uses (B04, B08, B11, B12).
    """

    mineral_id: str
    display_name: str
    reflectance: Dict[str, float]
    provenance: str  # e.g. "USGS Digital Spectral Library (splib05a)"
    source_url: Optional[str] = None


@dataclass
class ZoneEvaluation:
    """The single canonical result contract the API + UI both consume."""

    zone_id: str
    latitude: float
    longitude: float
    spatial_score: float
    spectral_similarity: Optional[float]        # 0-100, None if unavailable
    best_mineral_match: Optional[str]
    all_mineral_scores: Dict[str, float]        # mineral_id -> 0-100 similarity
    final_exploration_score: float              # 0-100
    priority: str                               # HIGH / MEDIUM / LOW
    explanation: str
    recommended_action: str
    data_provenance: str
    weights_used: Dict[str, float]
    scientific_note: str = (
        "Spectral similarity is a screening signal, not an ore-grade measurement. "
        "This priority score is a prototype decision-support figure and requires "
        "field/lab validation."
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Fusion primitives
# ---------------------------------------------------------------------------

def priority_band(final_score: float,
                  thresholds: Mapping[str, float] = PRIORITY_THRESHOLDS) -> str:
    """Return HIGH / MEDIUM / LOW for a 0-100 exploration score."""
    if final_score >= thresholds["HIGH"]:
        return "HIGH"
    if final_score >= thresholds["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def _normalize_weights(weights: Optional[Mapping[str, float]]) -> Dict[str, float]:
    active = dict(DEFAULT_FUSION_WEIGHTS)
    if weights:
        for key in active:
            if key in weights:
                active[key] = float(weights[key])
    total = sum(active.values())
    if total <= 0:
        return dict(DEFAULT_FUSION_WEIGHTS)
    return {k: v / total for k, v in active.items()}


def combine_spatial_spectral(
    spatial_score: float,
    spectral_similarity: Optional[float],
    weights: Optional[Mapping[str, float]] = None,
) -> float:
    """Prototype fusion rule.

    Both inputs are expressed on the same 0-100 scale.

    When ``spectral_similarity`` is ``None`` (e.g. no zone-level spectral
    data was available for this zone), the final score falls back to the
    spatial score alone. This is the honest, non-fabricating behaviour.

    Formula (prototype, configurable, non-scientific):
        final = w_spatial * spatial + w_spectral * spectral
    with the weights re-normalized to sum to 1.
    """
    active = _normalize_weights(weights)

    if spectral_similarity is None:
        # No spectral evidence available; use spatial score only.
        return float(round(max(0.0, min(100.0, spatial_score)), 2))

    final = active["spatial"] * spatial_score + active["spectral"] * spectral_similarity
    return float(round(max(0.0, min(100.0, final)), 2))


# ---------------------------------------------------------------------------
# Zone evaluation (the single canonical entry point)
# ---------------------------------------------------------------------------

def _pick_best_mineral(
    mineral_scores: Mapping[str, float],
) -> Optional[str]:
    if not mineral_scores:
        return None
    return max(mineral_scores.items(), key=lambda kv: kv[1])[0]


def _build_explanation(
    spatial_score: float,
    spectral_similarity: Optional[float],
    best_mineral: Optional[str],
    priority: str,
) -> str:
    if spectral_similarity is None:
        return (
            f"Spatial prospectivity {spatial_score:.0f}% with no zone-level "
            f"spectral evidence available; priority set from the spatial score alone."
        )
    if priority == "HIGH":
        return (
            f"High spatial prospectivity ({spatial_score:.0f}%) combined with "
            f"strong spectral similarity to the {best_mineral} reference "
            f"({spectral_similarity:.0f}%)."
        )
    if priority == "MEDIUM":
        return (
            f"Moderate combined signal: spatial {spatial_score:.0f}%, "
            f"{best_mineral} spectral similarity {spectral_similarity:.0f}%."
        )
    return (
        f"Low combined signal: spatial {spatial_score:.0f}%, "
        f"{best_mineral} spectral similarity {spectral_similarity:.0f}%."
    )


def _recommended_action(priority: str, spectral_available: bool) -> str:
    if priority == "HIGH":
        return "Prioritize field sampling / assay verification."
    if priority == "MEDIUM":
        base = "Schedule secondary spectral analysis"
        if not spectral_available:
            base = "Acquire zone-level spectral data"
        return base + " before committing field crews."
    return "Deprioritize for this planning cycle; revisit if inputs change."


def evaluate_zone(
    zone_id: str,
    latitude: float,
    longitude: float,
    spatial_score: float,
    zone_reflectance: Optional[Mapping[str, float]],
    mineral_references: Sequence[MineralReference],
    spectral_scorer,                              # callable, injected
    weights: Optional[Mapping[str, float]] = None,
    spatial_provenance: str = DATA_PROVENANCE_SYNTHETIC,
    spectral_provenance: Optional[str] = None,
) -> ZoneEvaluation:
    """Score a single zone end-to-end.

    Parameters
    ----------
    spectral_scorer
        Callable ``(zone_reflectance, reference_reflectance) -> similarity_0_to_1``.
        Injected so this module has no hard dependency on the spectral
        implementation (keeps unit tests trivial).
    """
    mineral_scores: Dict[str, float] = {}
    if zone_reflectance and mineral_references:
        for mineral in mineral_references:
            try:
                similarity = spectral_scorer(zone_reflectance, mineral.reflectance)
            except Exception:  # pragma: no cover - degrade gracefully
                continue
            mineral_scores[mineral.mineral_id] = round(similarity * 100.0, 2)

    best_mineral = _pick_best_mineral(mineral_scores)
    best_score = mineral_scores[best_mineral] if best_mineral else None

    final_score = combine_spatial_spectral(spatial_score, best_score, weights)
    priority = priority_band(final_score)
    explanation = _build_explanation(spatial_score, best_score, best_mineral, priority)
    action = _recommended_action(priority, best_score is not None)

    if best_score is None:
        provenance = spatial_provenance
    elif spectral_provenance == DATA_PROVENANCE_REAL and spatial_provenance == DATA_PROVENANCE_REAL:
        provenance = DATA_PROVENANCE_REAL
    elif spectral_provenance in (None, DATA_PROVENANCE_SYNTHETIC):
        provenance = DATA_PROVENANCE_MIXED if spatial_provenance == DATA_PROVENANCE_REAL else DATA_PROVENANCE_SYNTHETIC
    else:
        provenance = spectral_provenance

    return ZoneEvaluation(
        zone_id=zone_id,
        latitude=float(latitude),
        longitude=float(longitude),
        spatial_score=float(round(spatial_score, 2)),
        spectral_similarity=best_score,
        best_mineral_match=best_mineral,
        all_mineral_scores=mineral_scores,
        final_exploration_score=final_score,
        priority=priority,
        explanation=explanation,
        recommended_action=action,
        data_provenance=provenance,
        weights_used=_normalize_weights(weights),
    )


__all__ = [
    "DEFAULT_FUSION_WEIGHTS",
    "PRIORITY_THRESHOLDS",
    "DATA_PROVENANCE_SYNTHETIC",
    "DATA_PROVENANCE_REAL",
    "DATA_PROVENANCE_MIXED",
    "MineralReference",
    "ZoneEvaluation",
    "combine_spatial_spectral",
    "priority_band",
    "evaluate_zone",
]