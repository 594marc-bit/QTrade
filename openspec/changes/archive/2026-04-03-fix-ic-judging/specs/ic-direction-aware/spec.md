## ADDED Requirements

### Requirement: Direction-aware IC summary
`compute_ic_summary()` SHALL compute `win_rate` as the fraction of days where the IC sign matches the mean IC sign, not simply `IC > 0`.

#### Scenario: Positive IC factor
- **WHEN** ic_series mean is +0.05 and 60% of days have IC > 0
- **THEN** win_rate = 0.60 and ic_direction = +1

#### Scenario: Negative IC factor
- **WHEN** ic_series mean is -0.04 and 65% of days have IC < 0
- **THEN** win_rate = 0.65 and ic_direction = -1

#### Scenario: Zero or NaN IC
- **WHEN** ic_series mean is 0.0 or NaN
- **THEN** ic_direction = 0

### Requirement: Absolute IC threshold in effectiveness check
`evaluate_factor()` SHALL judge effectiveness using `abs(ic_mean) > threshold` instead of `ic_mean > threshold`.

#### Scenario: Negative IC exceeds threshold
- **WHEN** ic_mean = -0.046, ic_threshold = 0.03, win_rate = 0.62 > 0.55
- **THEN** is_effective = True, verdict = "有效"

#### Scenario: Positive IC below threshold
- **WHEN** ic_mean = 0.02, ic_threshold = 0.03
- **THEN** is_effective = False, verdict = "无效"

#### Scenario: Positive IC above threshold
- **WHEN** ic_mean = 0.04, ic_threshold = 0.03, win_rate = 0.58 > 0.55
- **THEN** is_effective = True, verdict = "有效"

### Requirement: IC direction in summary output
`compute_ic_summary()` SHALL include an `ic_direction` field: +1 when ic_mean > 0, -1 when ic_mean < 0, 0 when ic_mean is 0 or NaN.

#### Scenario: Direction field present
- **WHEN** compute_ic_summary is called with any valid IC series
- **THEN** the returned dict contains key "ic_direction" with value in {-1, 0, +1}
