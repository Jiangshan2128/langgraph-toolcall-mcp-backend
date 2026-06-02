from typing import Any, Callable, List

ToolFunc = Callable[..., Any]

class ToolRegistory:
    """tool registtory class"""
    def __init__(self):
        self._tools: dict[str, ToolFunc] = {}
        self._tool_definitions: List[dict] = []

    def register(self, name: str, description: str, parameters: dict):
        def decorator(func: ToolFunc):
            self._tools[name] = func
            self._tool_definitions.append(
                {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            )
            return func
        return decorator

    def get_tools(self) -> List[dict]:
        return self._tool_definitions

    def execute(self, name: str, **kwargs):
        if name not in self._tools:
            raise ValueError(f"Tool '{name}' not found.")
        return self._tools[name](**kwargs)
    

tool_registory = ToolRegistory()