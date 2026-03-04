"""
Gemini Live Backend - Main Entry Point

FastAPI application that provides HTTP/WebSocket bridge
between Gemini Live frontend and VoiceKit orchestration.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8001 --reload
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables FIRST (before importing config)
from dotenv import load_dotenv
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

from config import config
from api import router, bg_queue
from orchestration import get_orchestration  # Using standalone orchestration
from websocket import ws_manager

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown.
    """
    # Startup
    logger.info("Starting Gemini Live Backend...")
    logger.info(f"Server: {config.host}:{config.port}")
    logger.info(f"VoiceKit DB: {config.voicekit_db_path}")
    logger.info(f"WebSocket notifications: {config.notification_websocket_enabled}")

    # Initialize orchestration bridge
    try:
        orchestration = await get_orchestration()
        logger.info(f"Orchestration initialized with {len(orchestration.tool_registry)} tools")
    except Exception as e:
        logger.error(f"Failed to initialize orchestration: {e}", exc_info=True)
        raise

    # Start SJF background queue
    await bg_queue.start(orchestration)
    logger.info("SJF background queue started")

    # Start background task notification watcher
    notification_task = None
    if config.notification_websocket_enabled:
        notification_task = asyncio.create_task(watch_task_completions())
        logger.info("Started background task notification watcher")

    yield

    # Shutdown
    logger.info("Shutting down Gemini Live Backend...")
    if notification_task:
        notification_task.cancel()
        try:
            await notification_task
        except asyncio.CancelledError:
            pass

    orchestration = await get_orchestration()
    await orchestration.shutdown()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Gemini Live Backend",
    description="HTTP/WebSocket bridge between Gemini Live frontend and VoiceKit orchestration",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Gemini Live Backend",
        "status": "running",
        "endpoints": {
            "tool_execute": "/gemini-live/tool-execute",
            "task_delegate": "/gemini-live/task-delegate",
            "tasks": "/gemini-live/tasks",
            "followup_response": "/gemini-live/followup-response",
            "notifications": "ws://localhost:8001/gemini-live/notifications",
            "health": "/gemini-live/health"
        }
    }


async def watch_task_completions():
    """
    Background task that watches for completed background tasks
    and sends WebSocket notifications.

    Polls database every 2 seconds for newly completed tasks.
    """
    logger.info("Task completion watcher started")

    # Track tasks we've already notified about
    notified_tasks = set()

    while True:
        try:
            await asyncio.sleep(config.poll_interval_seconds)

            orchestration = await get_orchestration()

            # Get all completed tasks from database
            async with orchestration.session_factory() as session:
                from sqlalchemy import select
                from database.models import BackgroundTask, TaskStatus

                result = await session.execute(
                    select(BackgroundTask).where(
                        BackgroundTask.status == TaskStatus.completed,
                        BackgroundTask.delivered == False
                    )
                )
                tasks = result.scalars().all()

                # Send notifications for newly completed tasks
                for task in tasks:
                    if task.id not in notified_tasks:
                        # Send WebSocket notification
                        await ws_manager.send_notification(
                            user_identity=task.user_identity,
                            message={
                                "type": "task_complete",
                                "task_id": task.id,
                                "result": task.result or "",
                                "tool_name": task.tool_name
                            }
                        )

                        # Mark as delivered
                        await orchestration.mark_task_delivered(task.id)

                        notified_tasks.add(task.id)
                        logger.info(f"Sent notification for task {task.id} to {task.user_identity}")

                # Cleanup old notified tasks (keep last 100)
                if len(notified_tasks) > 100:
                    notified_tasks.clear()

        except asyncio.CancelledError:
            logger.info("Task completion watcher cancelled")
            break
        except Exception as e:
            logger.error(f"Error in task completion watcher: {e}", exc_info=True)
            # Continue running despite errors
            continue


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.host,
        port=config.port,
        reload=True,
        log_level=config.log_level.lower()
    )
