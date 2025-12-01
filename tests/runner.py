import argparse
import asyncio
import glob
import importlib
import logging
import os
import sys

sys.path.append(os.getcwd())

from tests.base import BardTestCase as TestCase
from tests.dummy import DummyClient
from tests.settings import TestSettings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("TestRunner")


async def run_test_method(test_instance: TestCase, method_name: str):
    """
    Runs a single test method (async).
    """
    method = getattr(test_instance, method_name)
    if not asyncio.iscoroutinefunction(method):
        method()
    else:
        await method()


async def run_test_class(
    test_class_name: str, client: DummyClient, specific_method: str | None = None
):
    """
    Runs a test class module.
    """
    try:
        module_path = f"tests.cases.{test_class_name}"
        log.info(f"Loading test module: {module_path}")
        module = importlib.import_module(module_path)

        test_class = None
        for name, obj in vars(module).items():
            if isinstance(obj, type):
                try:
                    if (
                        (issubclass(obj, TestCase) or obj.__name__.endswith("Test"))
                        and obj is not TestCase
                        and obj.__name__ != "TestCase"
                        and obj.__module__ == module.__name__
                    ):
                        test_class = obj
                        log.info(f"Found test class: {name} ({obj})")
                        break
                except TypeError:
                    continue

        if not test_class:
            if hasattr(module, "run") and not specific_method:
                log.info(f"Running legacy test function: {test_class_name}")
                await module.run(client)
                return True
            elif hasattr(module, "run") and specific_method:
                log.error(
                    f"Cannot run specific method '{specific_method}' on legacy test module {test_class_name}"
                )
                return False
            else:
                log.error(f"No TestCase class or 'run' function found in {module_path}")
                log.info(f"Contents of {module_path}: {dir(module)}")
                return False

        methods = []
        if specific_method:
            if not hasattr(test_class, specific_method):
                log.error(f"Method '{specific_method}' not found in {test_class_name}")
                return False
            methods.append(specific_method)
        else:
            methods = [
                func
                for func in dir(test_class)
                if callable(getattr(test_class, func)) and func.startswith("test_")
            ]

        log.info(f"Running tests in {test_class_name}: {methods}")

        all_passed = True
        for method_name in methods:
            instance = test_class(methodName=method_name, client=client)
            if not instance.client:
                instance.client = client

            log.info(f"--> {method_name}...")
            try:
                await instance.asyncSetUp()
                await run_test_method(instance, method_name)
                await instance.asyncTearDown()
                log.info(f"PASS: {method_name}")
                asyncio.create_task(
                    client.send_to_channel(f"# 🟢 {method_name} PASSED")
                )
            except AssertionError as e:
                log.error(f"FAIL: {method_name} - Assertion Error: {e}")
                asyncio.create_task(
                    client.send_to_channel(
                        f"# 🔴 {method_name} FAILED\n**Reason:** {e}"
                    )
                )
                all_passed = False
            except Exception as e:
                log.exception(f"FAIL: {method_name} - Exception: {e}")
                asyncio.create_task(
                    client.send_to_channel(f"# 🔴 {method_name} FAILED\n**Error:** {e}")
                )
                all_passed = False

        return all_passed

    except Exception as e:
        log.exception(f"Error loading/running {test_class_name}: {e}")
        return False


async def run_all_tests(client: DummyClient):
    """
    Runs all test cases found in tests/cases/ recursively.
    """
    log.info("Scanning for test cases...")
    base_dir = os.path.join("tests", "cases")
    case_files = glob.glob(os.path.join(base_dir, "**", "*.py"), recursive=True)

    test_names = []
    for f in case_files:
        if os.path.basename(f).startswith("__"):
            continue

        rel_path = os.path.relpath(f, base_dir)
        module_name = rel_path.replace(os.path.sep, ".")[:-3]
        test_names.append(module_name)

    if not test_names:
        log.warning("No test cases found in tests/cases/")
        return

    test_names.sort()

    results = {}
    for test_name in test_names:
        results[test_name] = await run_test_class(test_name, client)

    log.info("=" * 30)
    log.info("TEST RESULTS SUMMARY")
    log.info("=" * 30)
    passed = 0
    for name, result in results.items():
        status = "PASSED" if result else "FAILED"
        if result:
            passed += 1
        log.info(f"{name:<20}: {status}")
    log.info("-" * 30)
    log.info(
        f"Total: {len(results)}, Passed: {passed}, Failed: {len(results) - passed}"
    )

    if passed != len(results):
        sys.exit(1)


async def send_manual_message(client: DummyClient, content: str):
    """
    Sends a manual message and prints the response.
    """
    try:
        response = await client.send_and_wait(content)
        log.info(f"Response from bot:\n{response.content}")
    except Exception as e:
        log.error(f"Error sending message: {e}")


async def main():
    parser = argparse.ArgumentParser(description="Bard Black-Box Test Runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    send_parser = subparsers.add_parser("send", help="Send a manual message to the bot")
    send_parser.add_argument("message", help="The message content to send")
    send_parser.add_argument(
        "--no-wait", action="store_true", help="Don't wait for a response"
    )

    run_parser = subparsers.add_parser("run", help="Run test cases")
    run_parser.add_argument(
        "test_name", help="Name of the test case (filename without extension) or 'all'"
    )
    run_parser.add_argument(
        "method_name", nargs="?", help="Specific method to run (optional)"
    )

    args = parser.parse_args()

    try:
        TestSettings.validate()
    except ValueError as e:
        log.critical(f"Configuration Error: {e}")
        sys.exit(1)

    client = DummyClient()

    async with client:
        token = TestSettings.TEST_BOT_TOKEN
        if not token:
            log.critical("TEST_BOT_TOKEN is not set")
            sys.exit(1)
        asyncio.create_task(client.start(token))

        try:
            await client.wait_until_ready()
        except Exception as e:
            log.critical(f"Failed to connect to Discord: {e}")
            sys.exit(1)

        log.info("Waiting for target bot to be online...")
        is_online = await client.wait_for_target_presence(timeout=30)
        if not is_online:
            log.warning(
                "Target bot did not come online in time (or presence not visible). Proceeding with tests might fail."
            )
        else:
            log.info("Target bot is online!")

        if args.command == "send":
            await send_manual_message(client, args.message)
        elif args.command == "run":
            if args.test_name == "all":
                await run_all_tests(client)
            else:
                test_name = args.test_name.replace(os.path.sep, ".")
                if test_name.endswith(".py"):
                    test_name = test_name[:-3]

                prefix = "tests.cases."
                if test_name.startswith(prefix):
                    test_name = test_name[len(prefix) :]

                success = await run_test_class(test_name, client, args.method_name)
                if not success:
                    sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Test runner stopped by user.")
