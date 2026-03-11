import asyncio
import logging
from typing import Any, Dict, Optional

from bot.core.typing import TypingManager
from bot.message.reactions import ReactionManager
from bot.types import Request, RequestState
from settings import Settings

log = logging.getLogger("Bard")


class RequestManager:
    def __init__(
        self, reaction_manager: ReactionManager, typing_manager: TypingManager
    ):
        self._requests: Dict[str, Request] = {}
        self._max_requests = Settings.MAX_REQUESTS
        self._reaction_manager = reaction_manager
        self._typing_manager = typing_manager
        log.info("RequestManager initialized.")

    def _cleanup_old_requests(self):
        """Removes the oldest requests to keep the cache within limits."""
        if len(self._requests) > self._max_requests:
            # dict is insertion-ordered in Python 3.7+
            keys_to_remove = list(self._requests.keys())[:len(self._requests) - self._max_requests]
            for key in keys_to_remove:
                del self._requests[key]

    def create_request(
        self, message: Any, original_message_id: int
    ) -> Request:
        request = Request(message=message, original_message_id=original_message_id)
        self._requests[request.id] = request
        self._cleanup_old_requests()
        log.debug(
            f"Request {request.id} created for message {original_message_id}."
        )
        return request

    def get_request(self, request_id: str) -> Optional[Request]:
        return self._requests.get(request_id)

    async def cancel_request(self, request_id: str, is_edit: bool = False) -> bool:
        request = self.get_request(request_id)
        if not request:
            log.warning(f"Attempted to cancel non-existent request {request_id}.")
            return False

        if request.state in [RequestState.DONE, RequestState.CANCELLED]:
            log.warning(
                f"Attempted to cancel already completed or cancelled request {request_id}."
            )
            return False

        request.state = RequestState.CANCELLED

        if request.message:
            self._typing_manager.stop_typing(request.message.channel)

        if request.task and not request.task.done():
            request.task.cancel()
            log.info("Cancelled request task.")

        await self._reaction_manager.handle_request_cancellation(
            request, is_edit=is_edit
        )
        return True

    def update_request_state(self, request_id: str, state: RequestState):
        request = self.get_request(request_id)
        if request:
            log.debug(
                f"Updating request {request_id} state from {request.state} to {state}."
            )
            request.state = state
        else:
            log.warning(
                f"Attempted to update state for non-existent request {request_id}."
            )

    def assign_task_to_request(self, request_id: str, task: asyncio.Task):
        request = self.get_request(request_id)
        if request:
            request.task = task
            log.debug(f"Assigned task to request {request_id}.")
        else:
            log.warning(
                f"Attempted to assign task to non-existent request {request_id}."
            )
