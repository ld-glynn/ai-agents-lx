# Problem Solution Mapping (PSM)

**Agent-as-New-Hire** — a multi-agent system that takes messy team problems and turns them into structured, testable solutions assigned to the right people.

## The Concept

Think of PSM as a new hire joining your team. On day one, they:

1. Read their **onboarding packet** (your team structure, roles, domains)
2. Ingest **reported problems** (from a Google Sheet CSV)
3. Use their **skills** (specialized AI agents) to analyze and propose solutions
4. Produce **deliverables** (recommendations, action plans, process docs)

The new hire doesn't act unilaterally — they propose, and humans decide.

## Architecture

```
                    ┌─────────────────────┐
                    │   "The New Hire"     │
                    │    Orchestrator      │
                    │  (reads onboarding   │
                    │   doc as context)    │
                    └─────────┬───────────┘
                              │ delegates to skills
          ┌───────────┬───────┴───────┬──────────────┐
          ▼           ▼               ▼              ▼
    ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌────────────┐
    │Cataloger │ │ Pattern  │ │ Hypothesis │ │  Solver    │
    │          │ │ Analyzer │ │ Generator  │ │  Router    │
    └────┬─────┘ └────┬─────┘ └─────┬──────┘ └─────┬──────┘
         │            │             │               │
         ▼            ▼             ▼               ▼
    catalog.json  patterns.json  hypotheses.json  solution_map.json
                                                    │
                                          ┌─────────┼─────────┐
                                          ▼         ▼         ▼
                                    ┌─────────┐ ┌───────┐ ┌───────┐
                                    │Recommend│ │Action │ │Process│
                                    │  Solver │ │ Plan  │ │  Doc  │
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

### Stage 4: Route

**Agent:** Solver Router (`src/psm/agents/solver_router.py`)
**Input:** `data/hypotheses.json` + `context/onboarding.md`
**Output:** `data/solution_map.json`

Maps each hypothesis to a solver type and a team role:

| Solver Type | When to Use |
|---|---|
| `recommendation` | Decision-maker needs a clear recommendation |
| `action_plan` | Team needs step-by-step implementation plan |
| `process_doc` | New or revised process needed |
| `investigation` | Not enough info to act — needs research first |

### Stage 5: Solve

**Agents:** Solver agents (`src/psm/agents/solvers/base.py`)
**Input:** `data/solution_map.json` + solver playbooks
**Output:** `data/solver_outputs.json`

Each solver follows a structured playbook (`context/solver_playbooks/`) to produce its deliverable.

## Data Contracts

Every write between stages is validated through **strict Pydantic schemas** (`src/psm/schemas/`). This is inspired by the [Elliot evaluation harness](../elliot-eval/), which uses hard/soft failure codes to enforce agent output quality.

| Schema | File | Key Constraints |
|---|---|---|
| `RawProblem` | `schemas/problem.py` | Minimal — just structural validity |
| `CatalogEntry` | `schemas/problem.py` | Domain/severity enums, snake_case tags, ≥1 tag |
| `Pattern` | `schemas/pattern.py` | Must link ≥2 problems, confidence 0-1 |
| `ThemeSummary` | `schemas/pattern.py` | Must link ≥1 pattern, priority 0-10 |
| `Hypothesis` | `schemas/hypothesis.py` | Must have test_criteria, If/Then/Because format |
| `SolutionMapping` | `schemas/solution.py` | Valid solver type, priority 1-5 |
| `SolverOutput` | `schemas/solution.py` | Must have title, content ≥10 chars |

Referential integrity is checked between stages: patterns must reference real problem IDs, hypotheses must reference real pattern IDs, etc.

## Agent Job Descriptions

Each agent has a formal job description in `docs/job_descriptions/`. These define:

- **Responsibilities** — what the agent does
- **Output contract** — exact JSON structure expected
- **Hard failures** — errors that auto-disqualify output
- **Quality standards** — guidelines for good output

This pattern is borrowed from Elliot's "hiring" framework — agents are evaluated against their job description, not just vibes.

## Project Structure

```
psm/
├── pyproject.toml                          # Project config & dependencies
├── context/
│   ├── onboarding.md                       # Team structure (EDIT THIS)
│   └── solver_playbooks/                   # Templates for each solver type
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
│   ├── solution_map.json                   # Stage 4 output
│   └── solver_outputs.json                 # Stage 5 output
├── docs/job_descriptions/                  # Agent job descriptions
├── src/psm/
│   ├── main.py                             # CLI entry point
│   ├── config.py                           # Paths & settings
│   ├── schemas/                            # Pydantic data contracts
│   │   ├── problem.py                      # RawProblem, CatalogEntry
│   │   ├── pattern.py                      # Pattern, ThemeSummary
│   │   ├── hypothesis.py                   # Hypothesis
│   │   └── solution.py                     # SolutionMapping, SolverOutput
│   ├── agents/                             # Agent implementations
│   │   ├── orchestrator.py                 # The New Hire (pipeline coordinator)
│   │   ├── cataloger.py                    # Stage 1
│   │   ├── pattern_analyzer.py             # Stage 2
│   │   ├── hypothesis_gen.py               # Stage 3
│   │   ├── solver_router.py                # Stage 4
│   │   └── solvers/base.py                 # Stage 5
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

# Run the full pipeline
PYTHONPATH=src python -m psm.main run

# Run stage by stage (recommended for first time)
PYTHONPATH=src python -m psm.main run --stage catalog
PYTHONPATH=src python -m psm.main run --stage patterns
PYTHONPATH=src python -m psm.main run --stage hypotheses
PYTHONPATH=src python -m psm.main run --stage routes
PYTHONPATH=src python -m psm.main run

# Inspect any stage's output
PYTHONPATH=src python -m psm.main inspect catalog
PYTHONPATH=src python -m psm.main inspect patterns
PYTHONPATH=src python -m psm.main inspect themes
PYTHONPATH=src python -m psm.main inspect hypotheses
PYTHONPATH=src python -m psm.main inspect solutions
PYTHONPATH=src python -m psm.main inspect outputs

# Use a different model
PYTHONPATH=src python -m psm.main run --model claude-opus-4-6
```

## Customization

### 1. Add your team context

Edit `context/onboarding.md` with your actual team structure, roles, and domains. This is loaded as context for the orchestrator and solver router — it's how the system knows who to assign solutions to.

### 2. Replace sample problems

Export your Google Sheet as CSV to `data/input/problems.csv`. Required columns:

```
id, title, description, reported_by, date_reported, domain, tags
```

### 3. Modify solver playbooks

Edit the templates in `context/solver_playbooks/` to match your team's preferred document formats.

### 4. Adjust agent behavior

Each agent's behavior is defined by its job description in `docs/job_descriptions/`. Edit these to change how agents classify, analyze, or propose solutions.

## Design Decisions

| Decision | Rationale |
|---|---|
| **Flat delegation** (orchestrator is the only coordinator) | Easy to debug — if output is bad, you know exactly which agent produced it |
| **JSON files as shared state** | Human-readable, git-diffable, inspectable between runs |
| **Pydantic validation on every write** | Agents can't produce malformed data — schema is enforced in Python, not in prompts |
| **Sequential pipeline** (not parallel) | Each stage depends on the previous — patterns need catalog, hypotheses need patterns |
| **Sonnet for analysis, Haiku for solvers** | Solvers follow structured playbooks and don't need deep reasoning — saves cost |
| **Job descriptions, not just prompts** | Borrowed from Elliot — defines responsibilities, contracts, and failure modes explicitly |

## Relationship to Elliot

This project is complementary to the [Elliot evaluation harness](../elliot-eval/). Elliot provides the framework for rigorously testing and "hiring" agents. PSM could adopt Elliot's screening/gold evaluation pattern to validate that each agent consistently meets its job description before being promoted to production.

## Future

- **Automated inputs**: Ingest from Salesforce, Gong, support tickets (not just CSV)
- **Eval harness**: Elliot-style screening + gold evaluation for each PSM agent
- **Feedback loop**: Track which hypotheses were tested and whether they worked
- **Interactive mode**: Chat with the orchestrator to explore problems and solutions
