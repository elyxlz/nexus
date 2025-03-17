# Project Setup and Commands

## CRITICAL INSTRUCTIONS - READ FIRST

- **IMPORTANT**: Thoroughly review ALL guidelines in this document BEFORE modifying code
- **MANDATORY**: Apply ALL style guidelines from this document to your work without exception
- **ZERO TOLERANCE**: The user will not accept violations of these guidelines
- **REPEATED MISTAKES**: Will result in degraded user trust and experience

## Package Management

- **uv**: This project uses `uv` for Python package management
- **Run Commands**: Use `uv run script.py` to run scripts
- **Install Packages**: Use `uv add package` to add dependencies

## Type Checking

- **Pyright**: This project uses strict type checking with Pyright
- **Verification**: Always run `uv run pyright` (typically in src directory) before submitting changes
- **No Type Errors**: All code must satisfy Pyright's type checker without errors or warnings

## Memory & Learning

- Update this file whenever user corrects or provides specific instructions
- Record user's command preferences and workflow patterns
- Proactively remember past corrections and apply them consistently
- Ask if unclear whether a correction should be recorded here

# Codebase Style Guidelines

## Programming Paradigm

- **Purely Functional Core**: Implement core logic as pure functions with immutable data models
- **Avoid OOP**: No classes with methods, inheritance, or complex object hierarchies
- **Dataclasses Only**: Use frozen dataclasses for data structures, never mutable classes
- **State Flow Pattern**: State changes flow through function returns, never as side effects
- **Function Composition**: Build complex operations by composing smaller pure functions

## Function Design

- **Pure Functions**: No side effects, same output for same input (see `generate_job_id`, `build_job_env`)
- **Function Naming**: Use verb_noun format for function names (`create_job`, `update_job`)
- **Function Size**: Keep functions under 20 lines, extract helpers for logical parts
- **Private Helpers**: Use underscore prefix (`_parse_exit_code`, `_build_script_content`)
- **Function Replacement**: Prefer `dc.replace()` over mutation to modify dataclass instances

## Type System

- **Full Type Annotations**: Use complete type hints for all parameters and return values
- **Union Types**: Use pipe syntax for union types (`str | None`, not `Optional[str]`)
- **Literal Types**: Use `tp.Literal` for constrained string values (`JobStatus = tp.Literal["queued", "running", "completed", "failed"]`)
- **Type Aliases**: Define type aliases for complex types at the module level
- **Return Type Clarity**: Always specify return types, including `None` when appropriate

## Import Style

- **Fixed Abbreviations**:
  - `import dataclasses as dc`
  - `import pathlib as pl`
  - `import datetime as dt`
  - `import fastapi as fa`
- **Explicit Module Imports**: Import from specific modules, not packages
- **Local Import Format**: `from package.service.core import exceptions, logger, models`

## Utils & Helpers

- **Composable Utils**: Small, reusable utility functions
- **Consistent Naming**: Similar operations use similar naming patterns
- **Parameter Order**: Context/logger parameters first, optional params last
- **Default Values**: Use sensible defaults for optional parameters

## Code Documentation

- **Self-Documenting Code**: Prefer clear, descriptive variable and function names over comments
- **No Redundant Comments**: Avoid comments that repeat what the code already expresses
- **No Useless Comments**: Never add comments like "Add X to Y" that simply describe the next line of code
- **No Implementation Comments**: Don't comment on how code works; make code readable instead
- **Rare Comments**: Only use comments for non-obvious design decisions or complex domain logic
- **Type-Based Documentation**: Rely on type signatures to document interfaces, not comments
- **Clean Interfaces**: Function names and signatures should be clear enough without comments
- **Docstrings Optional**: Only add docstrings when function purpose isn't obvious from name/types
