```markdown
# odoo-ai-skills Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches you the core development patterns and conventions used in the `odoo-ai-skills` repository. The codebase is written in Python and focuses on clear, maintainable code without relying on a specific framework. You'll learn about file naming, import/export styles, commit conventions, and testing patterns to ensure consistency and quality in your contributions.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - **Example:**  
    ```
    ai_module.py
    utils_helper.py
    ```

### Import Style
- Use **relative imports** within the package.
  - **Example:**  
    ```python
    from .utils_helper import process_data
    ```

### Export Style
- Use **named exports** (i.e., define and explicitly expose functions/classes).
  - **Example:**  
    ```python
    def process_data(data):
        # processing logic
        return result

    __all__ = ['process_data']
    ```

### Commit Patterns
- Follow **conventional commit** standards.
- Use the `fix` prefix for bug fixes.
- Keep commit messages concise (average 75 characters).
  - **Example:**  
    ```
    fix: resolve issue with data processing in ai_module.py
    ```

## Workflows

_No automated workflows detected in this repository._

## Testing Patterns

- **Testing Framework:** Unknown (not explicitly detected)
- **Test File Pattern:** Files end with `.test.ts`
  - **Example:**  
    ```
    ai_module.test.ts
    ```
- **Note:** While the codebase is Python, test files use a TypeScript naming convention, suggesting possible integration or interface testing with TypeScript components.

## Commands
| Command | Purpose |
|---------|---------|
| /fix-commit | Use when committing a bug fix following conventional commit standards |
| /run-tests | Run all test files matching `*.test.ts` pattern |
| /format-code | Ensure code follows snake_case and relative import conventions |
```
