from tests.base import BardTestCase


class ContextBranchingTest(BardTestCase):
    async def test_branching_session(self):
        """
        Creates a conversation fork where the user replies to an older bot message twice with different topics.
        """
        root = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> I am going to tell you a secret code."
        )
        print(f"Response: {root.content}")

        branch_a_set = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> The code is 'ALPHA'. Remember this. Do NOT use tools.",
            reference=root,
        )
        print(f"Response: {branch_a_set.content}")

        branch_b_set = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> The code is 'BETA'. Remember this. Do NOT use tools.",
            reference=root,
        )
        print(f"Response: {branch_b_set.content}")

        branch_a_check = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is the code?",
            reference=branch_a_set,
        )
        self.assertIn("ALPHA", branch_a_check.content)
        self.assertNotIn("BETA", branch_a_check.content)
        print(f"Response: {branch_a_check.content}")

        branch_b_check = await self.bot.send_and_wait(
            f"<@{self.bot.settings.BOT_ID}> What is the code?",
            reference=branch_b_set,
        )
        self.assertIn("BETA", branch_b_check.content)
        self.assertNotIn("ALPHA", branch_b_check.content)
        print(f"Response: {branch_b_check.content}")
