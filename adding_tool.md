 Adding a New Tool

  Step 1 — Write the tool function (backend)

  Create a Python file in one of:
  - backend/tools/ — simple utilities (calculator, file ops, time)
  - backend/skills/ — AI-powered tools (web search, research)
  - backend/integrations/<service>/ — external APIs (Gmail, Calendar)

  The function signature drives everything — name, args, and docstring become the Gemini tool declaration automatically:

  # backend/tools/my_tool.py
  async def my_tool(arg1: str, arg2: int = 0) -> str:
      """Short description Gemini uses to decide when to call this tool.

      Args:
          arg1: Description of arg1.
          arg2: Description of arg2.
      """
      # do the work
      return "result as string"

  ---
  Step 2 — Register it in orchestration.py

  Add your import and register in _load_tools():

  from tools.my_tool import my_tool
  self.tool_registry["my_tool"] = my_tool

  That's all the backend needs. The /gemini-live/tools endpoint automatically serves every registered tool's schema to the frontend.

  ---
  Step 3 — Add to BACKEND_TOOLS in ToolRouter.tsx

  const BACKEND_TOOLS = [
    // ... existing tools ...
    'my_tool',
  ];

  This tells the frontend to route calls for my_tool to the backend instead of handling locally.

  ---
  Step 4 — Add an ack message in ACK_MESSAGES in ToolRouter.tsx

  const ACK_MESSAGES: Record<string, string> = {
    // ... existing ...
    my_tool: 'On it.',
  };

  Gemini speaks this immediately while the tool runs in the background.

  ---
  Restart backend — no frontend restart needed

  The frontend fetches the tool list from the backend on startup, so just restarting the backend is enough. The new tool will appear in Gemini's function declarations automatically.

  ---
  For background tools (long-running like deep_research): return { "background": true, ... } from the backend and the result will be delivered via the notification panel instead of
  injected into the conversation.
