# Nexus Codebase Style Guidelines

## Programming Paradigm
- **Purely Functional Core**: Implement core logic as pure functions with immutable data models
- **Avoid OOP**: No classes with methods, inheritance, or complex object hierarchies
- **Dataclasses Only**: Use frozen dataclasses for data structures, never mutable classes
- **State Flow Pattern**: State changes flow through function returns, never as side effects
- **Function Composition**: Build complex operations by composing smaller pure functions

## Function Design
- **Pure Functions**: No side effects, same output for same input (see `generate_job_id`, `build_job_env`)
- **Error Decorators**: Wrap functions with `@handle_exceptions` and `@catch_and_log` for error handling
- **Function Naming**: Use verb_noun format for function names (`create_job`, `update_job`)
- **Function Size**: Keep functions under 20 lines, extract helpers for logical parts
- **Private Helpers**: Use underscore prefix (`_parse_exit_code`, `_build_script_content`)
- **Function Replacement**: Prefer `dc.replace()` over mutation to modify dataclass instances

## Type System 
- **Full Type Annotations**: Use complete type hints for all parameters and return values
- **Union Types**: Use pipe syntax for union types (`str | None`, not `Optional[str]`)
- **Literal Types**: Use `Literal` for constrained string values (`JobStatus = Literal["queued", "running", "completed", "failed"]`)
- **Type Aliases**: Define type aliases for complex types at the module level
- **Return Type Clarity**: Always specify return types, including `None` when appropriate

## Import Style
- **Standard Imports First**: stdlib -> third-party -> local modules
- **Fixed Abbreviations**:
  - `import dataclasses as dc`
  - `import pathlib as pl`
  - `import datetime as dt`
  - `import fastapi as fa`
- **Explicit Module Imports**: Import from specific modules, not packages
- **Local Import Format**: `from nexus.service.core import exceptions, logger, models`

## Error Handling
- **Custom Exceptions**: Define domain-specific exception hierarchy
- **Decorator Pattern**: Use decorators to standardize error handling
- **Exception Mapping**: Map third-party exceptions to domain exceptions
- **Clear Error Messages**: Include detailed context in error messages
- **Error Codes**: Use consistent error codes for all exceptions

## Database Operations
- **Pure Database Functions**: Each function does one database operation
- **Transaction Safety**: Explicit transaction boundaries with proper error handling
- **Data Mapping**: Explicit row-to-model mapping functions
- **Parameterized SQL**: No string concatenation for SQL, always use parameters
- **SQL Validation**: Validate inputs before sending to database

## Utils & Helpers
- **Composable Utils**: Small, reusable utility functions
- **Consistent Naming**: Similar operations use similar naming patterns
- **Parameter Order**: Context/logger parameters first, optional params last
- **Early Returns**: Use early returns for validation and guard clauses
- **Default Values**: Use sensible defaults for optional parameters