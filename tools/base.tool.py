from abc import ABC, abstractmethod
from google.genai import types
from typing import Any, Dict, List, Optional
class ToolContext:
    """
    A simple container to pass shared resources and context-specific data to tools.
    """
    def __init__(self, **kwargs: Any):
        self.__dict__.update(kwargs)
    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        return self.__dict__.get(key, default)
class BaseTool(ABC):
    """
    Abstract base class for all tools the Gemini bot can use.
    """
    @abstractmethod
    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """
        Returns a list of Gemini FunctionDeclaration objects that this tool provides.
        These declarations are used to inform the LLM about the tool's capabilities.
        """
        pass
    @abstractmethod
    async def execute_tool(
        self,
        function_name: str,
        args: Dict[str, Any],
        context: ToolContext
    ) -> types.Part:
        """
        Executes the specified function of the tool.
        Args:
            function_name: The name of the function to execute (must match one from get_function_declarations).
            args: A dictionary of arguments for the function.
            context: A ToolContext object containing shared resources (e.g., gemini_client, config, user_id)
                     and any other necessary data for tool execution.
        Returns:
            A google.genai.types.Part object, typically a FunctionResponse, containing the result of the execution.
        """
        pass