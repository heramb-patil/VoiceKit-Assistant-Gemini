"""Deep Research - Simplified standalone version using web_search multiple times."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Import web_search from same directory
from .web_search import web_search

WORKSPACE = Path("data/workspace")


async def deep_research(
    topic: str,
    depth: int = 3,
    _drive_save_fn: Optional[Callable] = None,
) -> str:
    """Conduct multi-angle research on a topic.

    Args:
        topic: Research topic or question
        depth: Number of search queries (2-5, default 3)

    Returns:
        Comprehensive summary combining multiple searches
    """
    # Convert depth to int if it's passed as string
    if isinstance(depth, str):
        try:
            depth = int(depth)
        except (ValueError, TypeError):
            depth = 3

    depth = min(max(2, depth), 5)
    logger.info(f"Starting deep research on: {topic} (depth={depth})")

    # Generate diverse search queries
    queries = _generate_queries(topic, depth)

    # Execute searches in parallel
    logger.info(f"Executing {len(queries)} searches...")
    results = await asyncio.gather(*[web_search(q) for q in queries], return_exceptions=True)

    # Filter successful results
    successful_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"Search {i+1} failed: {result}")
        else:
            successful_results.append(result)

    if not successful_results:
        return f"Failed to gather research on '{topic}'. Please try again."

    logger.info(f"Got {len(successful_results)}/{len(queries)} successful results")

    # Synthesize results
    synthesis = _synthesize_results(topic, queries, successful_results)

    # Save full report to local file
    filepath = _save_report(topic, synthesis)
    file_note = f" Saved to {filepath}." if filepath else ""

    # Optionally upload to Google Drive
    drive_note = ""
    if _drive_save_fn is not None:
        try:
            slug = topic[:50].replace(" ", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            drive_result = await _drive_save_fn(
                filename=f"Research_{slug}_{timestamp}",
                content=synthesis,
            )
            if "Link:" in drive_result:
                link = drive_result.split("Link:")[-1].strip()
                drive_note = f" Also uploaded to Drive: {link}"
                logger.info("Research uploaded to Drive for topic '%s'", topic)
        except Exception as exc:
            logger.warning("Drive upload failed for deep_research: %s", exc)

    return (
        f"Research complete on '{topic}'. "
        f"Gathered {len(successful_results)} perspectives."
        f"{file_note}{drive_note}"
    )


def _generate_queries(topic: str, depth: int) -> list[str]:
    """Generate diverse search queries for a topic."""
    # Ensure depth is an integer
    depth = int(depth) if not isinstance(depth, int) else depth

    # Base query
    queries = [f"{topic} overview 2024 2025"]

    if depth >= 2:
        queries.append(f"{topic} latest developments technical details")
    if depth >= 3:
        queries.append(f"{topic} challenges limitations criticism")
    if depth >= 4:
        queries.append(f"{topic} expert opinions future implications")
    if depth >= 5:
        queries.append(f"{topic} real world applications case studies")
    
    return queries[:depth]


def _synthesize_results(topic: str, queries: list[str], results: list[str]) -> str:
    """Combine multiple search results into a coherent summary."""
    date_str = datetime.now().strftime("%B %d, %Y")
    
    synthesis = f"# Research Summary: {topic}\n"
    synthesis += f"*{date_str}*\n\n"
    
    synthesis += f"Based on {len(results)} comprehensive searches:\n\n"
    
    for i, (query, result) in enumerate(zip(queries, results), 1):
        synthesis += f"## Perspective {i}: {query}\n\n"
        # Clean up the result (remove any "Error:" prefixes)
        clean_result = result.replace("Error:", "").strip()
        synthesis += f"{clean_result}\n\n"
    
    synthesis += "\n---\n\n"
    synthesis += f"*Research compiled from {len(results)} searches on {date_str}*\n"
    
    return synthesis


def _save_report(topic: str, report: str) -> str:
    """Save research report to workspace."""
    try:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        
        # Create filename
        slug = topic.lower().replace(" ", "_")[:40]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"research_{slug}_{timestamp}.md"
        
        filepath = WORKSPACE / filename
        filepath.write_text(report, encoding="utf-8")
        
        logger.info(f"Research report saved: {filepath}")
        return str(filepath)
    except Exception as e:
        logger.error(f"Failed to save report: {e}")
        return ""
