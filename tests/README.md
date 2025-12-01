# Testing Architecture Plan

This document outlines the detailed architecture for the "black-box" testing suite for Bard. The suite uses a separate "dummy bot" to interact with the main bot in a real Discord server, verifying behavior from a user's perspective.

## 1. Directory Structure

```
tests/
├── README.md           # This documentation
├── settings.py         # Configuration loading (TestSettings class)
├── dummy.py            # DummyClient class implementation
├── runner.py           # CLI entry point
├── base.py             # Base TestCase class and shared utilities
├── resources/          # Test resources (images, videos, etc.)
└── cases/              # Test case definitions
    ├── 1_basic.py      # Basic sanity checks
    ├── ...
```

## 2. Component Specifications

### A. Configuration (`tests/settings.py`)

**Class: `TestSettings`**
- Loads environment variables using `python-dotenv`.
- **Fields:**
  - `TEST_BOT_TOKEN`: str (Required)
  - `BOT_ID`: int (Required) - The User ID of the main bot.
  - `TEST_GUILD_ID`: int (Required)
  - `TEST_CHANNEL_ID`: int (Required)
  - `RESPONSE_TIMEOUT`: int (Default: 300s)
- **Methods:**
  - `validate()`: Raises `ValueError` if required fields are missing.

### B. The Dummy Bot (`tests/dummy.py`)

**Class: `DummyClient(discord.Client)`**
- **Attributes:**
  - `target_id` (int): ID of the bot to listen to.
  - `channel_id` (int): ID of the channel to interact in.
  - `response_queue` (asyncio.Queue): Stores incoming messages from the target bot.
- **Methods:**
  - `on_ready()`: Logs connection status.
  - `on_message(message)`: Captures messages from target bot in test channel.
  - `on_typing(...)`: Detects when target bot starts typing.
  - `clear_queue()`: Empties the queue.
  - `send_and_wait(content, files=None, timeout=None, reference=None)`:
    - Sends message to channel.
    - Waits for typing signal (via file system).
    - Waits for response in `response_queue`.
  - `wait_for_response(timeout)`: Waits for next message in queue.
  - `wait_for_typing(timeout)`: Waits for file system signal indicating bot is processing.
  - `wait_for_target_presence(timeout)`: Waits for bot ready signal.

### C. The Runner (`tests/runner.py`)

**Functionality:**
- Uses `argparse` to handle commands.
- **Commands:**
  1.  `send <message>`:
      - Instantiates `DummyClient`.
      - Sends the raw message provided.
      - **User Note:** Message must include `<@TARGET_ID>` to trigger a mention-based bot.
  2.  `run <test_name> [method_name]`:
      - Loads module `tests.cases.<test_name>`.
      - Finds class inheriting from `BardTestCase`.
      - Runs all methods starting with `test_` or the specific method if provided.
  3.  `run all`:
      - Scans `tests/cases/` recursively.
      - Runs all test classes found.
      - Aggregates and prints results.

### D. Test Cases (`tests/cases/*.py`)

**Interface:**
- Must inherit from `tests.base.BardTestCase`.
- Define methods starting with `test_`.
- Use `self.bot` to interact with `DummyClient`.
- Use `self.assertTrue`, `self.assertEqual`, etc. (standard `unittest` assertions).

**Example (`tests/cases/1_basic.py`):**
```python
from tests.base import BardTestCase

class BasicTest(BardTestCase):
    """
    Basic sanity check test case.
    """

    async def test_ping(self):
        """
        Verifies that the main bot responds to a simple ping.
        """
        response = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> hello there"
        )

        self.assertTrue(response.content)
        print(f"Response: {response.content}")
```

## 3. Execution Workflow

1.  **Prerequisites:**
    - Main Bot is running (e.g., `python src/main.py`).
    - `.env` file contains test variables.

2.  **Running a Test:**
    ```bash
    # Run specific test module
    python tests/runner.py run 1_basic

    # Run specific test method
    python tests/runner.py run 1_basic test_ping
    
    # Run all tests
    python tests/runner.py run all
    ```

3.  **Manual Interaction:**
    ```bash
    # Send a custom message
    python tests/runner.py send "<@{BOT_ID}> Hello there"
    ```
