# AGENTS.md

This file provides guidance to agents when working with code in this repository.

# Core Guidelines
- **Virtual Env**: Execute Python commands using `.venv/bin/python ...`.
- **Logging**: INFO for user processes, DEBUG for AI agents using `src/log.py`.
- **Commits**: Use Conventional Commits (`type(scope): description`) and ask before committing.
- **Documentation**: Start each task by reading documentation.
  - Read and maintain the project's `DOCUMENTATION.md` (using present tense).
  - Search for relevant API documentation in `docs/` when working with APIs.
- **Completion**: Signal completion only after the project documentation is updated and changes are committed.

# Naming & Structure
- **Filenames**: Use single-word descriptive names for files/folders in `src/` (e.g., `hotload.py`, `scraper/`). Avoid underscores/dashes/camelCase.
- **Organization**: Stick to Single Responsibility Principle.
  - Keep local helpers in the same file if only used there.
  - Only create new files for major distinct modules or shared utilities.
  - Avoid premature abstraction; keep related logic together.
- **Logs**: `data/logs/` contains verbose JSON logs; console shows human-readable INFO logs.

# Code Style & Patterns
- **Imports**: Alphabetical, group: stdlib, third-party, local.
- **Settings**: Use `src/settings.py` via `Settings` class.
- **Async**: Use `asyncio`. Most I/O (Discord, Gemini, Playwright) is async.
- **Retries**: Use `@async_retry` (`src/retry.py`) for unstable ops.
- **Tools**: Inherit `BaseTool` (`src/ai/tools/base.py`), place in `src/ai/tools/` (auto-discovered).
  - These are standalone modules for AI tool calls; nothing should depend on them and they should be entirely self-contained.
- **Dynamic Imports**: `src/ai/tools/registry.py` uses `importlib` to load tools. Ensure new tools in `src/ai/tools/` do not start with `_`.
- **Logging**: Use `logging.getLogger("Bard")` and structured logging (pass `extra` dict) for machine-readable logs.

# Testing & Debugging
- **Prerequesits**: All tests must explicitly mention the bot (`<@{BOT_ID}>`) to trigger a response except when directly replying to its message.
- **Hotloading**: The project uses hotloading (`src/hotload.py`). Any code change will automatically restart the bot and create a new log file in `data/logs/`. This enables a self-reliant debugging loop where you can edit code, check the new logs, and verify fixes immediately.
- **Manual Test**: Use `tests/runner.py send "..."` to directly send messages.
- **Automated Test**: Use `tests/runner.py run <case>` to run comprehensive premade tests.
- **Logs**: Always check the full logs in `data/logs/` for the main bot's internal state after the test. Do not rely on the command output alone.

# Conventional Commit Guide
**`type(scope): description`**
- **`type`**: This describes the kind of change you're making.
  - `feat`: A new feature
  - `fix`: A bug fix
  - `chore`: Changes to the build process or auxiliary tools and libraries such as documentation generation
  - `docs`: Documentation only changes
  - `style`: Changes that do not affect the meaning of the code (white-space, formatting, missing semi-colons, etc)
  - `refactor`: A code change that neither fixes a bug nor adds a feature
  - `perf`: A code change that improves performance
  - `test`: Adding missing tests or correcting existing tests
- **`scope`** (optional): This provides additional contextual information and is contained within parenthesis. For example, `feat(api): add new endpoint`.
- **`description`**: A short description of the change in present tense.
