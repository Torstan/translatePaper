## ADDED Requirements

### Requirement: Page Component Ownership Model
The system SHALL build a deterministic page component ownership model before translation batch planning or render planning.

#### Scenario: Component model is built before translation planning
- **WHEN** a page is prepared for translation
- **THEN** the system MUST produce page components before selecting blocks for Codex translation

#### Scenario: Component records ownership metadata
- **WHEN** a page component is created
- **THEN** it MUST record component ID, component kind, source block IDs, source bbox, confidence, and reason codes

#### Scenario: Component kinds are explicit
- **WHEN** a non-trivial source block is assigned to a component
- **THEN** the component kind MUST be one of translated text, visual, reference, header/footer, page number, unknown fallback, duplicate, or explicit skip

### Requirement: Unique Source Block Owner
The system SHALL assign each non-trivial source block to exactly one non-duplicate component owner.

#### Scenario: Every source block has an owner
- **WHEN** component ownership is validated for a page
- **THEN** every non-empty, non-trivial source block MUST have exactly one component owner or an explicit duplicate/skip owner

#### Scenario: Duplicate non-skip ownership fails
- **WHEN** the same non-trivial source block appears in more than one non-duplicate component
- **THEN** ownership validation MUST report an error before translation or rendering is accepted

#### Scenario: Split components require child ownership
- **WHEN** a component is split into child components
- **THEN** each child MUST declare owned source IDs and the parent MUST NOT also render those child-owned source IDs

### Requirement: Ownership-Driven Translation Batches
The system SHALL build translation batches from component ownership rather than raw source block classification alone.

#### Scenario: Only translated text owners enter translation batches
- **WHEN** translation batches are built
- **THEN** only blocks owned by translated text components and classified as title, heading, subheading, or body MAY be sent to Codex

#### Scenario: Visual-owned blocks are never translated
- **WHEN** a source block is owned by a visual component
- **THEN** it MUST NOT be included in any Codex translation batch

#### Scenario: References are excluded by ownership
- **WHEN** a source block is owned by a reference component
- **THEN** it MUST NOT be included in any Codex translation batch

### Requirement: Ownership-Driven Render Plan
The system SHALL derive render items from component ownership and MUST persist ownership data in render-plan artifacts.

#### Scenario: Visual component renders as one visual owner
- **WHEN** a visual component is rendered
- **THEN** the render plan MUST create an original image clip or explicit split render plan for that component and mark all owned source IDs as visual-owned

#### Scenario: Text component renders as text owner
- **WHEN** a translated text component is rendered
- **THEN** the render plan MUST create translated text or explicit text fallback items only for source IDs owned by that component

#### Scenario: Coverage ledger includes component ID
- **WHEN** a coverage ledger entry is written
- **THEN** it MUST include the component ID and component kind used to choose the render item or skip

#### Scenario: Render plan artifact includes ownership validation
- **WHEN** a page render-plan artifact is written
- **THEN** it MUST include serialized components and ownership validation results

### Requirement: Render Layer Exclusivity
The system SHALL prevent the same source content from being rendered by both image and text layers.

#### Scenario: Source ID cannot render as both image and text
- **WHEN** render-plan validation finds a source ID in both an image render item and a text render item
- **THEN** validation MUST fail before PDF writing is accepted

#### Scenario: Text cannot overlap unrelated visual component
- **WHEN** a translated or original selectable text item significantly overlaps a visual component it does not own
- **THEN** validation MUST fail before PDF writing is accepted

#### Scenario: Visual clip cannot capture translated text component
- **WHEN** a visual image clip captures a translated text component beyond the configured tolerance
- **THEN** validation MUST fail or require an explicit recorded exception before the PDF is accepted

### Requirement: Conservative Ambiguous Visual Ownership
The system SHALL prefer conservative visual ownership for ambiguous non-prose clusters that could otherwise create duplicate rendering.

#### Scenario: Ambiguous diagram internals become visual-owned
- **WHEN** short labels, arrows, table cells, formulas, or diagram internals are spatially connected to a visual component and cannot be safely separated
- **THEN** they MUST be owned by the visual component instead of translated independently

#### Scenario: Nearby body prose remains separate
- **WHEN** prose is outside the visual component boundary and is classified as body text
- **THEN** it MUST remain owned by a translated text component unless ownership validation detects unsafe overlap

#### Scenario: Unknown ambiguous content is preserved
- **WHEN** content cannot be confidently assigned to translated text without risking duplicate rendering
- **THEN** it MUST be assigned to an unknown fallback or visual component with a conservative preserve strategy

### Requirement: Ownership Visual QA
The system SHALL add deterministic QA checks for ownership violations and ghosting-class visual defects.

#### Scenario: Duplicate source ownership is reported
- **WHEN** visual QA reads a render plan with duplicate non-skip source ownership
- **THEN** it MUST report the page number, source IDs, component IDs, bboxes, and failure category

#### Scenario: Text over visual is reported
- **WHEN** visual QA detects a text render item over a visual component it does not own
- **THEN** it MUST report a text-over-visual failure with source IDs and bboxes

#### Scenario: Component undercapture is reported
- **WHEN** a visual component clip excludes dark source pixels belonging to its owned source bbox
- **THEN** visual QA MUST report a possible undercapture failure

#### Scenario: Component overcapture is reported
- **WHEN** a visual component clip captures source pixels or source blocks owned by a translated text component
- **THEN** visual QA MUST report a possible overcapture failure

### Requirement: Ownership Regression Fixtures
The system SHALL support page-level ownership fixtures for known ghosting and clipping failures.

#### Scenario: BERT page 9 table ownership fixture
- **WHEN** the BERT page 9 fixture is evaluated
- **THEN** the table component MUST own all table cells, including narrow numeric and placeholder columns, and no translated text may overlap the table component

#### Scenario: BERT page 12 reference ownership fixture
- **WHEN** the BERT page 12 fixture is evaluated
- **THEN** reference components MUST NOT own adjacent body or appendix content outside the reference component geometry

#### Scenario: BERT page 15 figure ownership fixture
- **WHEN** the BERT page 15 fixture is evaluated
- **THEN** figure components MUST own internal diagram labels and no translated text item may overlap the figure component

#### Scenario: User-reported ghosting becomes fixture
- **WHEN** a user reports ghosting, white blocks over visual content, or duplicate text rendering
- **THEN** the defect MUST be represented by an ownership fixture before the fix is accepted
