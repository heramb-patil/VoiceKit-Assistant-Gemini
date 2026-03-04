"""Web search tool using Gemini with Google Search Grounding (standalone)."""

import os


async def web_search(query: str) -> str:
    """Search the web for current information on a topic.

    Args:
        query: Search query or question.
    """
    try:
        from google import genai
        from google.genai import types

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY or GOOGLE_API_KEY environment variable not set"

        client = genai.Client(api_key=api_key)

        prompt = f"""Search the web and provide current, accurate information about: {query}

Include:
- Key facts and recent developments
- Relevant sources and dates
- Concise summary suitable for voice response

Format for spoken delivery (not markdown)."""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3
            )
        )

        if not response.text:
            return f"No results found for: {query}"

        return response.text

    except ImportError:
        return "Error: google-genai package not installed. Run: pip install google-genai"
    except Exception as exc:
        return f"Web search failed: {exc}"
