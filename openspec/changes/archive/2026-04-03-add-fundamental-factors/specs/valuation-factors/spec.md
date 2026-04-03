## ADDED Requirements

### Requirement: PE TTM rank factor
The system SHALL provide a `PeFactor` that computes cross-sectional percentile ranking of PE_TTM per trading date.

#### Scenario: Normal calculation
- **WHEN** PeFactor.calculate is called with a DataFrame containing trade_date, ts_code, pe_ttm
- **THEN** a pe_ttm_rank column is added with values in [0, 100], where lower values indicate lower PE (more undervalued)

#### Scenario: Missing PE values
- **WHEN** some stocks have NaN pe_ttm (e.g., loss-making companies)
- **THEN** those stocks get NaN pe_ttm_rank, and ranking is computed only on valid values

### Requirement: PB rank factor
The system SHALL provide a `PbFactor` that computes cross-sectional percentile ranking of PB per trading date.

#### Scenario: Normal calculation
- **WHEN** PbFactor.calculate is called with a DataFrame containing trade_date, ts_code, pb
- **THEN** a pb_rank column is added with values in [0, 100], where lower values indicate lower PB

#### Scenario: Missing PB values
- **WHEN** some stocks have NaN pb
- **THEN** those stocks get NaN pb_rank

### Requirement: Factor registration and scoring integration
Both PeFactor and PbFactor SHALL be registered via @register_factor and produce score columns compatible with the existing scorer.

#### Scenario: Score column naming
- **WHEN** standardize_factors processes pe_ttm_rank and pb_rank
- **THEN** score columns are named pe_ttm_rank_score and pb_rank_score respectively
