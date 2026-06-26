```markdown
# odoo-ai-skills Development Patterns

> Auto-generated skill from repository analysis

## Overview
The `odoo-ai-skills` repository demonstrates patterns for building Python-based AI skills, with a focus on clear structure, maintainable code, and conventional commit practices. This skill teaches you how to organize Python projects with PascalCase file naming, relative imports, named exports, and standardized commit messages. It also covers how to structure and recognize test files for your codebase.

## Coding Conventions

### File Naming
- Use **PascalCase** for file names.
  - **Example:**  
    `MySkill.py`  
    `UserProfileManager.py`

### Import Style
- Use **relative imports** within the package.
  - **Example:**
    ```python
    from .UserProfileManager import UserProfileManager
    ```

### Export Style
- Use **named exports** (explicitly export classes/functions).
  - **Example:**
    ```python
    class UserProfileManager:
        pass

    __all__ = ["UserProfileManager"]
    ```

### Commit Messages
- Follow **conventional commit** format.
- Use the `feat` prefix for new features.
- Keep commit messages concise (average 76 characters).
  - **Example:**  
    ```
    feat: add UserProfileManager for handling user profiles
    ```

## Workflows

### Feature Development
**Trigger:** When adding a new feature or module  
**Command:** `/feature-dev`

1. Create a new file using PascalCase (e.g., `NewFeature.py`).
2. Implement the feature using relative imports for dependencies.
3. Export your classes/functions using named exports.
4. Write or update corresponding test files (`NewFeature.test.py`).
5. Commit your changes with a `feat:` prefix and a concise description.

### Testing
**Trigger:** When verifying code functionality  
**Command:** `/run-tests`

1. Identify test files matching the `*.test.*` pattern.
2. Run tests using your preferred Python test runner (e.g., `pytest`, `unittest`).
3. Ensure all tests pass before merging or deploying changes.

## Testing Patterns

- **Test File Naming:**  
  Test files follow the `*.test.*` pattern (e.g., `UserProfileManager.test.py`).
- **Framework:**  
  No specific testing framework detected; use your preferred Python testing tool.
- **Example Test File:**
  ```python
  # UserProfileManager.test.py

  from .UserProfileManager import UserProfileManager

  def test_create_user():
      manager = UserProfileManager()
      assert manager.create_user("Alice") is not None
  ```

## Commands
| Command         | Purpose                                   |
|-----------------|-------------------------------------------|
| /feature-dev    | Start a new feature development workflow   |
| /run-tests      | Run all test files in the repository       |
```
