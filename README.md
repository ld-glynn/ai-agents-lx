# Problem Solution Mapping (PSM)

**Agent-as-New-Hire** — a multi-agent system that takes messy team problems and turns them into structured, testable solutions executed by specialized AI agents.

## The Concept

PSM uses a **three-tier agent model**:

- **Tier 1 — Engine Agents** run the pipeline (Cataloger, Pattern Analyzer, Hypothesis Generator, Hiring Manager)
- **Tier 2 — Agent New Hires** are specialists created per problem cluster, each with a persona and focused scope
- **Tier 3 — Skills** are the work products each New Hire produces (recommendations, action plans, process docs, investigations)

Think of it like hiring: the engine agents are your recruiting team. They analyze problems, find patterns, propose solutions, and then *hire* specialist agents to do the work. Each specialist onboards with context about their problem domain and executes their skills to produce deliverables.

## Architecture

```
                    ┌─────────────────────┐
                    │    Orchestrator      │
                    │  (reads onboarding   │
                    │   doc as context)    │
                    └─────────┬───────────┘
                              │ delegates to
          ┌───────────┬───────┴───────┬──────────────┐
          ▼           ▼               ▼              ▼
    ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌────────────┐
    │Cataloger │ │ Pattern  │ │ Hypothesis │ │  Hiring    │
    │          │ │ Analyzer │ │ Generator  │ │  Manager   │
    └────┬─────┘ └────┬─────┘ └─────┬──────┘ └─────┬──────┘
         │            │             │               │
         ▼            ▼             ▼               ▼
    catalog.json  patterns.json  hypotheses.json  candidates
                                                    │
                                              ┌─────┴──────┐
                                              ▼            ▼
                                         ┌─────────┐  ┌─────────┐
                                         │  Eval   │  │  Eval   │
                                         │Screening│  │Screening│ ...
                                         └────┬────┘  └────┬────┘
                                              │            │
                                         pass/fail    pass/fail
                                              │            │
                                              ▼            ▼
                                        new_hires.json (qualified only)
                                              │
                                    ┌─────────┼─────────┐
                                    ▼         ▼         ▼
                              ┌─────────┐ ┌───────┐ ┌───────┐
                              │Recommend│ │Action │ │Process│
                              │         │ │ Plan  │ │  Doc  │
                              └─────────┘ └───────┘ └───────┘
```

## Pipeline

The system runs as a **five-stage sequential pipeline**. Each stage reads the previous stage's output and writes its own. JSON files serve as shared state — human-readable, diffable, inspectable.

### Stage 1: Catalog

**Agent:** Cataloger (`src/psm/agents/cataloger.py`)
**Input:** Raw problems from CSV
**Output:** `data/catalog.json`

Takes messy, inconsistent problem descriptions and normalizes them: assigns a domain, severity, consistent tags, affected roles, and frequency.

### Stage 2: Patterns

**Agent:** Pattern Analyzer (`src/psm/agents/pattern_analyzer.py`)
**Input:** `data/catalog.json`
**Output:** `data/patterns.json` + `data/themes.json`

Clusters related problems that share root causes or symptoms. A pattern must link at least 2 problems. Patterns are grouped into higher-level themes with priority scores.

### Stage 3: Hypotheses

**Agent:** Hypothesis Generator (`src/psm/agents/hypothesis_gen.py`)
**Input:** `data/patterns.json` + `data/themes.json`
**Output:** `data/hypotheses.json`

For each pattern, proposes 1-3 testable solutions in "If we X, then Y, because Z" format. Every hypothesis must include test criteria — untestable hypotheses are rejected.

### Stage 4: Hire + Screen

**Agent:** Hiring Manager (`src/psm/agents/hiring_manager.py`)
**Eval:** Screening gate (`src/psm/eval/gate.py`)
**Input:** `data/patterns.json` + `data/hypotheses.json` + `context/onboarding.md`
**Output:** `data/new_hires.json`

The Hiring Manager designs one specialist Agent New Hire per pattern, each with:
- A **name and persona** tailored to their problem domain
- A set of **skills** (Tier 3) mapped to specific hypotheses
- An **assigned role** on the team for collaboration

Before deployment, each candidate is **screened** through the evaluation framework (inspired by [elliot-eval](../elliot-eval/)). Candidates must pass all test cases with zero hard failures. Rejected candidates don't make it to the roster.

### Stage 5: Execute Skills

**Agents:** Agent New Hires (`src/psm/agents/solvers/base.py`)
**Input:** `data/new_hires.json` + skill playbooks
**Output:** `data/skill_outputs.json`

Each qualified New Hire executes their skills in priority order. The agent's persona becomes the system prompt, and the skill's playbook provides output structure.

| Skill Type | When to Use |
|---|---|
| `recommend` | Decision-maker needs a clear recommendation |
| `action_plan` | Team needs step-by-step implementation plan |
| `process_doc` | New or revised process needed |
| `investigate` | Not enough info to act — needs research first |

## Evaluation Framework

PSM includes a built-in evaluation framework (`src/psm/eval/`) that screens Agent New Hires before deployment. This is inspired by [elliot-eval](../elliot-eval/)'s screening/gold pattern, rebuilt natively in Python.

### Two-Stage Evaluation

**Screening** — Must pass 100% of cases with zero hard failures. Tests basic contract compliance: can the agent produce valid, non-trivial output for each of its skills?

**Gold** — Must pass >= 85% of cases with avg score >= 0.7. Tests output quality: is the agent good enough to deploy? (Requires passing screening first.)

### Failure Codes

| Hard Failures (disqualify) | Soft Failures (warnings) |
|---|---|
| `JSON_PARSE_ERROR` — output wasn't valid JSON | `WEAK_NEXT_STEPS` — missing or generic next steps |
| `SCHEMA_INVALID` — failed Pydantic validation | `SHORT_CONTENT` — content exists but too brief |
| `HALLUCINATED_REF` — referenced IDs not in input | `TITLE_MISMATCH` — title doesn't relate to hypothesis |
| `MISSING_CONTENT` — content field empty or trivial | `CONFIDENCE_MISMATCH` — high confidence, weak evidence |
| `WRONG_SKILL_TYPE` — output doesn't match skill | `GENERIC_OUTPUT` — doesn't reference specific problems |
| `ADAPTER_ERROR` — LLM call failed | |
| `TIMEOUT` — exceeded time limit | |

### Scoring

Each test case is scored across 5 dimensions:
1. **Content depth** — meets minimum length for skill type
2. **Input references** — proves the agent read the context (hypothesis ID, pattern ID, problem IDs)
3. **Hallucination check** — no fabricated IDs that don't exist in input
4. **Next steps** — actionable follow-up items
5. **Keyword coverage** — uses domain-relevant terminology

## Data Contracts

Every write between stages is validated through **strict Pydantic schemas** (`src/psm/schemas/`).

| Schema | File | Key Constraints |
|---|---|---|
| `RawProblem` | `schemas/problem.py` | Minimal — just structural validity |
| `CatalogEntry` | `schemas/problem.py` | Domain/severity enums, snake_case tags, >= 1 tag |
| `Pattern` | `schemas/pattern.py` | Must link >= 2 problems, confidence 0-1 |
| `ThemeSummary` | `schemas/pattern.py` | Must link >= 1 pattern, priority 0-10 |
| `Hypothesis` | `schemas/hypothesis.py` | Must have test_criteria, If/Then/Because format |
| `AgentNewHire` | `schemas/agent.py` | Persona >= 10 chars, >= 1 skill, >= 1 hypothesis |
| `AgentSkill` | `schemas/agent.py` | Valid SkillType enum, priority 1-5 |
| `SkillOutput` | `schemas/agent.py` | Must have title, content >= 10 chars |

Referential integrity is checked between stages: patterns must reference real problem IDs, hypotheses must reference real pattern IDs, new hires must reference real patterns and hypotheses.

## Project Structure

```
psm/
├── pyproject.toml                          # Project config & dependencies
├── context/
│   ├── onboarding.md                       # Team structure (EDIT THIS)
│   └── solver_playbooks/                   # Templates for each skill type
│       ├── recommendation.md
│       ├── action_plan.md
│       ├── process_doc.md
│       └── investigation.md
├── data/
│   ├── input/problems.csv                  # Google Sheet export (REPLACE THIS)
│   ├── catalog.json                        # Stage 1 output
│   ├── patterns.json                       # Stage 2 output
│   ├── themes.json                         # Stage 2 output
│   ├── hypotheses.json                     # Stage 3 output
│   ├── new_hires.json                      # Stage 4 output (qualified agents)
│   └── skill_outputs.json                  # Stage 5 output
├── docs/job_descriptions/                  # Agent job descriptions
├── src/psm/
│   ├── main.py                             # CLI entry point
│   ├── config.py                           # Paths & settings
│   ├── schemas/                            # Pydantic data contracts
│   │   ├── problem.py                      # RawProblem, CatalogEntry
│   │   ├── pattern.py                      # Pattern, ThemeSummary
│   │   ├── hypothesis.py                   # Hypothesis
│   │   ├── agent.py                        # AgentNewHire, AgentSkill, SkillOutput
│   │   └── solution.py                     # Legacy (SolutionMapping, SolverOutput)
│   ├── agents/                             # Agent implementations
│   │   ├── orchestrator.py                 # Pipeline coordinator
│   │   ├── cataloger.py                    # Stage 1
│   │   ├── pattern_analyzer.py             # Stage 2
│   │   ├── hypothesis_gen.py               # Stage 3
│   │   ├── hiring_manager.py              # Stage 4 (creates Agent New Hires)
│   │   └── solvers/base.py                 # Stage 5 (skill executor)
│   ├── eval/                               # Evaluation framework
│   │   ├── failure_codes.py                # Hard/soft failure classification
│   │   ├── test_gen.py                     # Test case generator per skill
│   │   ├── scorer.py                       # 3-phase scoring (parse, schema, quality)
│   │   ├── runner.py                       # Invokes agent against test cases
│   │   └── gate.py                         # Screening + gold thresholds
│   └── tools/                              # Shared utilities
│       ├── data_store.py                   # JSON persistence with validation
│       ├── csv_reader.py                   # Google Sheet CSV parser
│       └── context_loader.py               # Onboarding & playbook loader
└── tests/
```

## Setup

```bash
cd psm
python3 -m venv .venv
source .venv/bin/activate
pip install pydantic anthropic eval_type_backport
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# Check pipeline status
PYTHONPATH=src python -m psm.main status

# Run the full pipeline (includes screening gate at Stage 4)
PYTHONPATH=src python -m psm.main run

# Run stage by stage (recommended for first time)
PYTHONPATH=src python -m psm.main run --stage catalog
PYTHONPATH=src python -m psm.main run --stage patterns
PYTHONPATH=src python -m psm.main run --stage hypotheses
PYTHONPATH=src python -m psm.main run --stage hire
PYTHONPATH=src python -m psm.main run --stage execute

# Inspect any stage's output
PYTHONPATH=src python -m psm.main inspect catalog
PYTHONPATH=src python -m psm.main inspect patterns
PYTHONPATH=src python -m psm.main inspect themes
PYTHONPATH=src python -m psm.main inspect hypotheses
PYTHONPATH=src python -m psm.main inspect new-hires
PYTHONPATH=src python -m psm.main inspect skills

# Evaluate existing agents (without re-running pipeline)
PYTHONPATH=src python -m psm.main eval --stage screening
PYTHONPATH=src python -m psm.main eval --stage gold

# Use a different model
PYTHONPATH=src python -m psm.main run --model claude-opus-4-6
```

## Customization

### 1. Add your team context

Edit `context/onboarding.md` with your actual team structure, roles, and domains. This is loaded as context for the orchestrator and hiring manager — it's how the system knows who to assign agents to.

### 2. Replace sample problems

Export your Google Sheet as CSV to `data/input/problems.csv`. Required columns:

```
id, title, description, reported_by, date_reported, domain, tags
```

### 3. Modify skill playbooks

Edit the templates in `context/solver_playbooks/` to match your team's preferred document formats.

### 4. Adjust agent behavior

Each agent's behavior is defined by its job description in `docs/job_descriptions/`. Edit these to change how agents classify, analyze, or propose solutions.

### 5. Tune evaluation thresholds

Edit `src/psm/eval/gate.py` to adjust:
- `SCREENING_PASS_RATE` — default 1.0 (100%, all cases must pass)
- `GOLD_PASS_RATE` — default 0.85 (85% of cases)
- `GOLD_MIN_SCORE` — default 0.7 (average quality score)

## Design Decisions

| Decision | Rationale |
|---|---|
| **Three-tier agent model** | Engine agents are reusable process; New Hires are specialized per-problem; Skills are typed work products |
| **Screening gate before deployment** | Agents that can't produce valid output don't get to execute — prevents wasted LLM calls and bad deliverables |
| **JSON files as shared state** | Human-readable, git-diffable, inspectable between runs |
| **Pydantic validation on every write** | Agents can't produce malformed data — schema is enforced in Python, not in prompts |
| **Sequential pipeline** (not parallel) | Each stage depends on the previous — patterns need catalog, hypotheses need patterns |
| **Job descriptions, not just prompts** | Borrowed from Elliot — defines responsibilities, contracts, and failure modes explicitly |
| **Hallucination detection** | Agents must reference real IDs from their input context — fabricated references are hard failures |

## Relationship to Elliot

The [elliot-eval](../elliot-eval/) harness is a TypeScript evaluation framework for testing agent candidates. PSM's evaluation framework (`src/psm/eval/`) rebuilds elliot-eval's core patterns natively in Python:

| Elliot Pattern | PSM Implementation |
|---|---|
| Hard/soft failure codes | `eval/failure_codes.py` — 7 hard, 5 soft |
| Screening + gold stages | `eval/gate.py` — screening (100%), gold (85%) |
| AJV schema validation | Pydantic strict mode validation |
| Hallucination detection | ID-reference checking in `eval/scorer.py` |
| Adapter pattern | Direct invocation of skill executor |
| Test dataset (JSONL) | Generated from pipeline data by `eval/test_gen.py` |

The key difference: elliot-eval tests agents against static, curated test sets. PSM generates test cases dynamically from the pipeline's own data — each Agent New Hire is tested against the specific patterns and hypotheses it was created to solve.

## Future

- **Automated inputs**: Ingest from Salesforce, Gong, support tickets (not just CSV)
- **Feedback loop**: Track which hypotheses were tested and whether they worked
- **Interactive mode**: Chat with the orchestrator to explore problems and solutions
- **Gold evaluation dataset**: Curate gold-standard test cases for each skill type
